import asyncio
import logging
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async_task(coro):
    """Run a coroutine inside a fresh event loop with orderly shutdown.

    The previous implementation just called loop.run_until_complete then
    loop.close, but aiohttp's connector leaves background keepalive tasks
    alive past close() and they crash the next invocation with
    "Future attached to a different loop". Mirrors the helper in
    tldv_sync.py / instantly_sync.py / personal_email_sync.py.
    """
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro)
        loop.run_until_complete(loop.shutdown_asyncgens())
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        if pending:
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        return result
    finally:
        asyncio.set_event_loop(None)
        loop.close()


@celery_app.task(name="app.tasks.sales_reports.send_us_pod_call_report")
def send_us_pod_call_report(
    report_date: str | None = None,
    report_type: str = "auto",
    recipients: list[str] | None = None,
) -> dict:
    """Send the scheduled US pod call report by email."""
    return _run_async_task(_async_send_us_pod_call_report(report_date, report_type, recipients))


async def _async_send_us_pod_call_report(
    report_date: str | None = None,
    report_type: str = "auto",
    recipients: list[str] | None = None,
) -> dict:
    # Local imports so module load doesn't bind the shared engine to whatever
    # loop happens to be active at Celery worker import time. We deliberately
    # use `task_session()` (NullPool, one-shot engine) instead of the shared
    # `AsyncSessionLocal` to be provably loop-safe across Celery task
    # invocations — see app/database.py docstring.
    from app.database import task_session
    from app.config import settings
    from app.services.us_pod_call_report import (
        WEEKDAY_TO_KEY,
        current_report_local_date,
        default_report_date,
        is_production_environment,
        is_weekend_report_day,
        load_sales_report_settings,
        normalize_sales_report_settings,
        scheduled_report_type,
        send_us_pod_call_report_email,
    )
    from app.models.settings import WorkspaceSettings

    parsed_date = date.fromisoformat(report_date) if report_date else None

    async with task_session() as session:
        report_settings = await load_sales_report_settings(session)
        resolved_report_type = scheduled_report_type(report_settings=report_settings) if report_type == "auto" else report_type
        scheduled_call = not parsed_date and recipients is None and report_type == "auto"

        if scheduled_call:
            if not report_settings["enabled"]:
                return {"status": "skipped", "reason": "disabled"}

            now = datetime.now(timezone.utc)
            send_tz = ZoneInfo(report_settings["send_timezone"])
            local_now = now.astimezone(send_tz)
            day_key = WEEKDAY_TO_KEY[local_now.weekday()]
            if day_key not in report_settings["send_days"]:
                return {
                    "status": "skipped",
                    "reason": "not_a_report_day",
                    "local_date": local_now.date().isoformat(),
                    "day": day_key,
                }

            due_at = datetime.combine(
                local_now.date(),
                time(report_settings["send_hour"], report_settings["send_minute"]),
                tzinfo=send_tz,
            )
            if now < due_at.astimezone(timezone.utc):
                return {
                    "status": "skipped",
                    "reason": "before_send_time",
                    "local_time": local_now.isoformat(),
                    "due_at": due_at.isoformat(),
                }

            send_key = f"{local_now.date().isoformat()}:{resolved_report_type}:{report_settings['send_timezone']}:{report_settings['send_hour']:02d}:{report_settings['send_minute']:02d}"
            if report_settings.get("last_scheduled_send_key") == send_key:
                return {"status": "skipped", "reason": "already_sent", "send_key": send_key}

            if report_settings["skip_weekends"] and is_weekend_report_day(report_settings=report_settings):
                return {
                    "status": "skipped",
                    "reason": "weekend",
                    "local_date": current_report_local_date(report_settings=report_settings).isoformat(),
                    "report_type": resolved_report_type,
                }
            report_period_date = default_report_date(report_settings=report_settings)
            if report_settings["skip_weekends"] and report_period_date.weekday() >= 5:
                return {
                    "status": "skipped",
                    "reason": "report_period_weekend",
                    "local_date": current_report_local_date(report_settings=report_settings).isoformat(),
                    "report_date": report_period_date.isoformat(),
                    "report_type": resolved_report_type,
                }
            if (
                not is_production_environment()
                and not settings.SALES_REPORT_ENABLE_NONPROD_SCHEDULED_SENDS
                and not report_settings["nonprod_scheduled_enabled"]
            ):
                return {
                    "status": "skipped",
                    "reason": "nonprod_scheduled_reports_disabled",
                    "local_date": current_report_local_date(report_settings=report_settings).isoformat(),
                    "report_type": resolved_report_type,
                }

        report = await send_us_pod_call_report_email(
            session,
            parsed_date,
            report_type=resolved_report_type,
            recipients=recipients,
        )

        # Did every recipient actually send? `send_us_pod_call_report_email`
        # now attempts all recipients (no break-on-first-failure) and returns
        # one result per recipient. We treat any non-"sent" as a partial
        # failure and *deliberately do not commit* `last_scheduled_send_key`
        # — that way Beat's next 15-minute tick will retry the day instead of
        # silently skipping it as "already_sent". Yes, recipients who got the
        # email once may get it again, but a duplicate is far less harmful
        # than a missing report (which is what reps complained about).
        send_results = report.get("send_results", []) or []
        all_sent = bool(send_results) and all(r.get("status") == "sent" for r in send_results)
        failed_recipients = [
            r.get("to") for r in send_results if r.get("status") != "sent" and r.get("to")
        ]

        if scheduled_call and all_sent:
            current = normalize_sales_report_settings(report_settings)
            current["last_scheduled_send_key"] = send_key
            current["last_scheduled_send_at"] = datetime.now(timezone.utc).isoformat()
            row = await session.get(WorkspaceSettings, 1)
            if row is not None:
                sync_settings = dict(row.sync_schedule_settings or {})
                sync_settings["sales_report"] = current
                row.sync_schedule_settings = sync_settings
                session.add(row)
                await session.commit()
        elif scheduled_call and not all_sent:
            logger.warning(
                "US pod call report partial-failure: %d/%d recipients failed (%s). "
                "Not committing send_key — Beat will retry next tick.",
                len(failed_recipients), len(send_results), failed_recipients,
            )

        return {
            "status": "completed" if all_sent else ("partial_failure" if send_results else "no_recipients"),
            "report_date": report["report_date"],
            "report_type": report["report_type"],
            "period_start": report["period_start"],
            "period_end": report["period_end"],
            "recipients": report["recipients"],
            "send_results": send_results,
            "failed_recipients": failed_recipients,
        }
