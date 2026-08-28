import logging
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.celery_app import celery_app
from app.tasks._runner import run_async_task as _run_async_task

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.weekly_digest.send_weekly_digest")
def send_weekly_digest(
    period_start: str | None = None,
    period_end: str | None = None,
    recipients: list[str] | None = None,
) -> dict:
    """Send the scheduled weekly CRM activity digest by email. Fires every 15
    min via Beat and self-gates on its own config block (weekly_digest):
    enabled flag, send_days (Monday only), send time, and a dedup key so a
    given week's digest goes out exactly once."""
    return _run_async_task(_async_send_weekly_digest(period_start, period_end, recipients))


async def _async_send_weekly_digest(
    period_start: str | None = None,
    period_end: str | None = None,
    recipients: list[str] | None = None,
) -> dict:
    from app.database import task_session
    from app.config import settings
    from sqlalchemy import select

    from app.models.settings import WorkspaceSettings
    from app.services.weekly_digest import (
        WEEKDAY_TO_KEY,
        WEEKLY_DIGEST_CONFIG_KEY,
        is_production_environment,
        load_weekly_digest_settings,
        normalize_weekly_digest_settings,
        send_weekly_digest_email,
        weekly_digest_period,
    )

    parsed_start = date.fromisoformat(period_start) if period_start else None
    parsed_end = date.fromisoformat(period_end) if period_end else None
    scheduled_call = not parsed_start and not parsed_end and recipients is None

    async with task_session() as session:
        digest_settings = await load_weekly_digest_settings(session)

        if scheduled_call:
            if not digest_settings["enabled"]:
                return {"status": "skipped", "reason": "disabled"}

            now = datetime.now(timezone.utc)
            send_tz = ZoneInfo(digest_settings["send_timezone"])
            local_now = now.astimezone(send_tz)
            day_key = WEEKDAY_TO_KEY[local_now.weekday()]
            if day_key not in digest_settings["send_days"]:
                return {
                    "status": "skipped",
                    "reason": "not_a_send_day",
                    "local_date": local_now.date().isoformat(),
                    "day": day_key,
                }

            due_at = datetime.combine(
                local_now.date(),
                time(digest_settings["send_hour"], digest_settings["send_minute"]),
                tzinfo=send_tz,
            )
            if now < due_at.astimezone(timezone.utc):
                return {
                    "status": "skipped",
                    "reason": "before_send_time",
                    "local_time": local_now.isoformat(),
                    "due_at": due_at.isoformat(),
                }

            if (
                not is_production_environment()
                and not settings.SALES_REPORT_ENABLE_NONPROD_SCHEDULED_SENDS
                and not digest_settings["nonprod_scheduled_enabled"]
            ):
                return {
                    "status": "skipped",
                    "reason": "nonprod_scheduled_sends_disabled",
                    "local_date": local_now.date().isoformat(),
                }

            send_key = local_now.date().isoformat()
            if digest_settings.get("last_scheduled_send_key") == send_key:
                return {"status": "skipped", "reason": "already_sent", "send_key": send_key}

            resolved_start, resolved_end = weekly_digest_period(now, digest_settings)
        else:
            send_key = None
            resolved_start = parsed_start or weekly_digest_period(digest_settings=digest_settings)[0]
            resolved_end = parsed_end or weekly_digest_period(digest_settings=digest_settings)[1]

        already_sent: set[str] = set()
        if scheduled_call and digest_settings.get("partial_send_key") == send_key:
            already_sent = {str(r).lower() for r in (digest_settings.get("partial_sent_recipients") or [])}

        pass_recipients = recipients
        if scheduled_call and already_sent and recipients is None:
            pass_recipients = [
                r for r in (digest_settings.get("recipients") or [])
                if str(r).lower() not in already_sent
            ]

        if scheduled_call and already_sent and pass_recipients == []:
            send_results: list[dict] = []
            all_sent = True
            failed_recipients: list[str] = []
            digest_recipients = sorted(already_sent)
        else:
            digest = await send_weekly_digest_email(
                session,
                resolved_start,
                resolved_end,
                recipients=pass_recipients,
                digest_settings=digest_settings,
            )
            send_results = digest.send_results or []
            all_sent = bool(send_results) and all(r.get("status") == "sent" for r in send_results)
            failed_recipients = [r.get("to") for r in send_results if r.get("status") != "sent" and r.get("to")]
            digest_recipients = digest.recipients

        sent_now = {str(r.get("to")).lower() for r in send_results if r.get("status") == "sent" and r.get("to")}
        covered = already_sent | sent_now

        if scheduled_call:
            row = (
                await session.execute(
                    select(WorkspaceSettings)
                    .where(WorkspaceSettings.id == 1)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if row is not None:
                sync_settings = dict(row.sync_schedule_settings or {})
                stored_block = sync_settings.get(WEEKLY_DIGEST_CONFIG_KEY)
                current = normalize_weekly_digest_settings(stored_block if isinstance(stored_block, dict) else digest_settings)
                if all_sent:
                    current["last_scheduled_send_key"] = send_key
                    current["last_scheduled_send_at"] = datetime.now(timezone.utc).isoformat()
                    current["partial_send_key"] = None
                    current["partial_sent_recipients"] = []
                elif sent_now:
                    current["partial_send_key"] = send_key
                    current["partial_sent_recipients"] = sorted(covered)
                sync_settings[WEEKLY_DIGEST_CONFIG_KEY] = current
                row.sync_schedule_settings = sync_settings
                session.add(row)
                await session.commit()
            if not all_sent:
                logger.warning(
                    "Weekly CRM digest partial-failure: %d/%d recipients failed (%s). "
                    "Delivered ones are recorded; Beat retries only the rest next tick.",
                    len(failed_recipients), len(send_results), failed_recipients,
                )

        return {
            "status": "completed" if all_sent else ("partial_failure" if send_results else "no_recipients"),
            "period_start": resolved_start.isoformat(),
            "period_end": resolved_end.isoformat(),
            "recipients": digest_recipients,
            "send_results": send_results,
            "failed_recipients": failed_recipients,
        }
