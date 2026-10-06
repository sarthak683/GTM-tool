import logging
from datetime import date, datetime, time, timezone

from zoneinfo import ZoneInfo

from app.celery_app import celery_app
from app.tasks._runner import run_async_task as _run_async_task

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.team_activity_report.send_team_activity_report")
def send_team_activity_report(
    period_start: str | None = None,
    period_end: str | None = None,
    recipients: list[str] | None = None,
) -> dict:
    """Send the scheduled weekly Calls/Email/LinkedIn team activity report.
    Fires every 15 min via Beat and self-gates on its own config block
    (team_activity_report): enabled flag, send_days (Monday only), send
    time, and a per-week dedup key so a given week's report goes out
    exactly once. Same pattern as app.tasks.weekly_digest."""
    return _run_async_task(_async_send_team_activity_report(period_start, period_end, recipients))


async def _async_send_team_activity_report(
    period_start: str | None = None,
    period_end: str | None = None,
    recipients: list[str] | None = None,
) -> dict:
    from app.database import task_session
    from app.config import settings

    from app.services.sync_settings import claim_scheduled_send, merge_sync_settings_block
    from app.services.weekly_digest import is_production_environment
    from app.services.team_activity_report import (
        TEAM_ACTIVITY_REPORT_CONFIG_KEY,
        WEEKDAY_TO_KEY,
        load_team_activity_report_settings,
        send_team_activity_report_email,
        team_activity_report_period,
    )

    parsed_start = date.fromisoformat(period_start) if period_start else None
    parsed_end = date.fromisoformat(period_end) if period_end else None
    scheduled_call = not parsed_start and not parsed_end and recipients is None

    async with task_session() as session:
        report_settings = await load_team_activity_report_settings(session)

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
                    "reason": "not_a_send_day",
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

            if (
                not is_production_environment()
                and not settings.SALES_REPORT_ENABLE_NONPROD_SCHEDULED_SENDS
                and not report_settings["nonprod_scheduled_enabled"]
            ):
                return {
                    "status": "skipped",
                    "reason": "nonprod_scheduled_sends_disabled",
                    "local_date": local_now.date().isoformat(),
                }

            send_key = local_now.date().isoformat()
            if report_settings.get("last_scheduled_send_key") == send_key:
                return {"status": "skipped", "reason": "already_sent", "send_key": send_key}

            # Claim this week's send BEFORE sending, atomically. The old flow
            # marked the week done only after sending, and wrote it by
            # rewriting the whole settings blob — so a concurrent job (RecoTap
            # holds a stale copy for ~4 min at 04:00 UTC) could erase the mark
            # and the next 15-min tick sent the whole report again.
            if not await claim_scheduled_send(
                session, TEAM_ACTIVITY_REPORT_CONFIG_KEY, send_key, now.isoformat()
            ):
                return {"status": "skipped", "reason": "already_claimed", "send_key": send_key}

            resolved_start, resolved_end = team_activity_report_period(now, report_settings)
        else:
            send_key = None
            resolved_start = parsed_start or team_activity_report_period(report_settings=report_settings)[0]
            resolved_end = parsed_end or team_activity_report_period(report_settings=report_settings)[1]

        # A retry after a partial failure only goes to whoever hasn't got it yet.
        already_sent: set[str] = set()
        pass_recipients = recipients
        if scheduled_call and report_settings.get("partial_send_key") == send_key:
            already_sent = {str(r).lower() for r in (report_settings.get("partial_sent_recipients") or [])}
            pass_recipients = [
                r for r in report_settings["recipients"] if str(r).lower() not in already_sent
            ]

        if scheduled_call and already_sent and not pass_recipients:
            await merge_sync_settings_block(
                session,
                TEAM_ACTIVITY_REPORT_CONFIG_KEY,
                {"partial_send_key": None, "partial_sent_recipients": []},
            )
            await session.commit()
            return {"status": "completed", "reason": "all_recipients_already_covered", "send_key": send_key}

        try:
            report = await send_team_activity_report_email(
                session,
                resolved_start,
                resolved_end,
                recipients=pass_recipients,
                report_settings=report_settings,
            )
        except Exception:
            if scheduled_call:
                # Nothing was sent (building/connecting failed) — give the
                # week back so the next tick retries instead of skipping it.
                await merge_sync_settings_block(
                    session, TEAM_ACTIVITY_REPORT_CONFIG_KEY, {"last_scheduled_send_key": None}
                )
                await session.commit()
            raise

        send_results = report.send_results or []
        all_sent = bool(send_results) and all(r.get("status") == "sent" for r in send_results)
        failed_recipients = [r.get("to") for r in send_results if r.get("status") != "sent" and r.get("to")]
        sent_now = {str(r.get("to")).lower() for r in send_results if r.get("status") == "sent" and r.get("to")}

        if scheduled_call and all_sent:
            await merge_sync_settings_block(
                session,
                TEAM_ACTIVITY_REPORT_CONFIG_KEY,
                {"partial_send_key": None, "partial_sent_recipients": []},
            )
            await session.commit()
        elif scheduled_call:
            # Release the claim so the next tick retries, and remember who
            # already has it so they aren't sent a second copy.
            await merge_sync_settings_block(
                session,
                TEAM_ACTIVITY_REPORT_CONFIG_KEY,
                {
                    "last_scheduled_send_key": None,
                    "partial_send_key": send_key,
                    "partial_sent_recipients": sorted(already_sent | sent_now),
                },
            )
            await session.commit()
            logger.warning(
                "Team activity report send failure: %d/%d recipients failed (%s). "
                "Claim released; next tick retries only the recipients not yet covered.",
                len(failed_recipients), len(send_results), failed_recipients,
            )

        return {
            "status": "completed" if all_sent else ("partial_failure" if send_results else "no_recipients"),
            "period_start": resolved_start.isoformat(),
            "period_end": resolved_end.isoformat(),
            "recipients": report.recipients,
            "send_results": send_results,
            "failed_recipients": failed_recipients,
        }
