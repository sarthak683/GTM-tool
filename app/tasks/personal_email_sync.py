"""
Celery task: sync personal Gmail inbox for a single user.

Flow per user:
  1. Load UserEmailConnection row (token, last_sync_epoch, backfill state)
  2. Determine time range: backfill (90 days) on first run, else incremental
  3. Fetch emails via GmailInboxClient using the user's personal token
  4. Refresh token if expired, write updated token back to DB
  5. Call process_personal_emails() for matching + gap-fill + task gen
  6. Update last_sync_epoch and backfill_completed flag

Beat schedule:
  A single beat task `sync-all-personal-inboxes` fires every 10 minutes.
  It loads all active UserEmailConnection rows and enqueues one
  `sync_personal_inbox` task per user. This avoids N dynamic beat entries.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from uuid import UUID

from app.celery_app import celery_app
from app.config import settings
from app.tasks._runner import run_async_task as _run_async_task

logger = logging.getLogger(__name__)

def _is_invalid_grant(exc: Exception) -> bool:
    message = str(exc).lower()
    return "invalid_grant" in message or "expired or revoked" in message


@celery_app.task(
    name="app.tasks.personal_email_sync.sync_personal_inbox",
    bind=True,
    max_retries=2,
    default_retry_delay=180,
    time_limit=1800,  # 30 min hard limit (backfill can be large)
    soft_time_limit=1500,
)
def sync_personal_inbox(self, connection_id: str) -> dict:
    """Sync one user's personal Gmail inbox. Called per-user."""
    import redis

    from app.config import settings as app_settings

    # Per-connection overlap guard. The 10-minute fan-out re-enqueues every
    # connection each tick while one sync may legitimately run up to the
    # 30-minute time limit (backfills); without this, two workers sync the same
    # mailbox from the same cursor concurrently and race the check-then-insert
    # dedup into duplicate email activities. TTL sits above time_limit so a
    # killed worker's lock always expires before the next legitimate run.
    lock_key = f"personal_sync:lock:{connection_id}"
    r = redis.Redis.from_url(app_settings.REDIS_URL, decode_responses=True)
    try:
        acquired = r.set(lock_key, "1", nx=True, ex=1900)
        if not acquired:
            logger.info(
                "Personal email sync for connection %s already in flight; skipping",
                connection_id,
            )
            return {"status": "skipped", "reason": "sync already in flight"}
    except redis.RedisError:
        # If Redis is unreachable the broker is down too — let the task run
        # rather than adding a new failure mode for the lock itself.
        logger.warning("Personal sync lock unavailable; running unlocked")
        acquired = False

    try:
        return _run_async_task(_async_sync_inbox(connection_id))
    except Exception as exc:
        if _is_invalid_grant(exc):
            logger.warning(
                "Personal email sync needs reconnect for connection %s: %s",
                connection_id,
                exc,
            )
            _run_async_task(_mark_connection_reconnect_required(connection_id, str(exc)))
            return {"status": "reconnect_required", "reason": "invalid_grant"}
        logger.error("Personal email sync failed for connection %s: %s", connection_id, exc)
        raise self.retry(exc=exc)
    finally:
        if acquired:
            try:
                r.delete(lock_key)
            except redis.RedisError:
                pass  # TTL expires it


@celery_app.task(
    name="app.tasks.personal_email_sync.sync_all_personal_inboxes",
    bind=True,
)
def sync_all_personal_inboxes(self) -> dict:
    """
    Beat-scheduled task. Loads all active UserEmailConnection rows and
    enqueues one sync_personal_inbox task per user.
    """
    try:
        return _run_async_task(_enqueue_all_inboxes())
    except Exception:
        # Re-raise instead of returning {"error": ...}: a swallowed exception
        # makes Celery record SUCCESS, job_health advances last_effective_at,
        # and the System Health panel shows green while zero inboxes sync —
        # the exact blind spot the job_health rework exists to close.
        logger.exception("Failed to enqueue personal inbox syncs")
        raise


async def _enqueue_all_inboxes() -> dict:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlmodel import select

    from app.models.settings import WorkspaceSettings
    from app.models.user_email_connection import UserEmailConnection

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    queued = 0
    try:
        async with SessionLocal() as session:
            # Zippy-only mode: stop pulling reps' full personal inboxes. Existing
            # synced activities are untouched — this only halts future capture,
            # and flipping the flag back off resumes syncing immediately.
            ws = await session.get(WorkspaceSettings, 1)
            if ws and (ws.sync_schedule_settings or {}).get("zippy_only_email_sync"):
                logger.info("Personal inbox sync skipped — zippy_only_email_sync on")
                return {"queued": 0, "status": "skipped", "reason": "zippy_only_email_sync"}
            result = await session.execute(
                select(UserEmailConnection.id).where(
                    UserEmailConnection.is_active == True  # noqa: E712
                )
            )
            connection_ids = [str(row.id) for row in result.all()]

        for cid in connection_ids:
            sync_personal_inbox.delay(cid)
            queued += 1

        logger.info("Enqueued %d personal inbox syncs", queued)
        return {"queued": queued}
    finally:
        await engine.dispose()


async def _mark_connection_reconnect_required(connection_id: str, error: str) -> None:
    from app.database import task_session
    from app.models.user_email_connection import UserEmailConnection

    async with task_session() as session:
        conn = await session.get(UserEmailConnection, connection_id)
        if not conn:
            return
        conn.is_active = False
        conn.last_error = f"Reconnect Gmail: {error}"[:500]
        conn.updated_at = datetime.utcnow()
        session.add(conn)
        await session.commit()


async def _async_sync_inbox(connection_id: str) -> dict:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlmodel import select

    from app.clients.gmail_inbox import GmailInboxClient
    from app.models.user import User
    from app.models.user_email_connection import UserEmailConnection
    from app.services.personal_email_sync import process_personal_emails

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with SessionLocal() as session:
            connection = await session.get(UserEmailConnection, connection_id)
            if not connection or not connection.is_active:
                return {"status": "skipped", "reason": "connection not found or inactive"}

            user = await session.get(User, connection.user_id)
            if not user or not user.is_active:
                return {"status": "skipped", "reason": "user not found or inactive"}

            # Determine time range
            if not connection.backfill_completed:
                # First run: scan back backfill_days
                after_epoch = int(time.time()) - (connection.backfill_days * 86400)
                logger.info(
                    "personal_email_sync: starting backfill for %s (back %d days)",
                    connection.email_address, connection.backfill_days,
                )
            else:
                after_epoch = connection.last_sync_epoch or (int(time.time()) - 600)

            current_epoch = int(time.time())

            # Fetch emails (use larger batch for backfill)
            max_results = 200 if not connection.backfill_completed else 50
            gmail = GmailInboxClient(
                inbox=connection.email_address,
                token_payload=connection.token_data,
            )
            messages = gmail.fetch_new_messages(
                after_epoch=after_epoch,
                max_results=max_results,
            )

            # Persist refreshed token if Google rotated it
            if gmail.updated_token_payload:
                connection.token_data = gmail.updated_token_payload
                connection.updated_at = __import__("datetime").datetime.utcnow()

            if not messages and gmail.updated_token_payload:
                await session.commit()

            if not messages:
                # No emails — still run calendar sync
                cal_meetings_created = 0
                try:
                    from app.clients.google_calendar import fetch_upcoming_events
                    from app.services.calendar_sync import sync_calendar_events

                    cal_events, updated_token = await fetch_upcoming_events(
                        token_data=connection.token_data,
                        client_id=settings.gmail_client_id,
                        client_secret=settings.gmail_client_secret,
                        days_ahead=60,
                        max_results=100,
                    )
                    if updated_token is not connection.token_data:
                        connection.token_data = updated_token
                    if cal_events:
                        cal_stats = await sync_calendar_events(
                            session=session,
                            events=cal_events,
                            user_email=connection.email_address,
                            owner_user_id=user.id,
                        )
                        cal_meetings_created = cal_stats["meetings_created"]
                except Exception as cal_exc:
                    if _is_invalid_grant(cal_exc):
                        raise
                    logger.warning("calendar_sync (no-mail path) failed: %s", cal_exc)

                connection.last_sync_epoch = current_epoch
                if not connection.backfill_completed:
                    connection.backfill_completed = True
                connection.last_error = None
                session.add(connection)
                await session.commit()
                return {
                    "status": "completed",
                    "emails_found": 0,
                    "activities_created": 0,
                    "meetings_from_calendar": cal_meetings_created,
                }

            # Process emails
            stats = await process_personal_emails(
                session=session,
                messages=messages,
                connection=connection,
                sync_user=user,
            )

            # ── Calendar sync (runs every cycle, not just backfill) ───────
            try:
                from app.clients.google_calendar import fetch_upcoming_events
                from app.services.calendar_sync import sync_calendar_events

                cal_events, updated_token = await fetch_upcoming_events(
                    token_data=connection.token_data,
                    client_id=settings.gmail_client_id,
                    client_secret=settings.gmail_client_secret,
                    days_ahead=60,
                    max_results=100,
                )

                # Persist refreshed token if calendar refresh rotated it
                if updated_token is not connection.token_data:
                    connection.token_data = updated_token

                if cal_events:
                    cal_stats = await sync_calendar_events(
                        session=session,
                        events=cal_events,
                        user_email=connection.email_address,
                        owner_user_id=user.id,
                    )
                    stats["meetings_from_calendar"] = cal_stats["meetings_created"]
                    stats["meetings_updated_from_calendar"] = cal_stats["meetings_updated"]
                    logger.info(
                        "calendar_sync: %s → %d new, %d updated meetings",
                        connection.email_address,
                        cal_stats["meetings_created"],
                        cal_stats["meetings_updated"],
                    )
            except Exception as cal_exc:
                if _is_invalid_grant(cal_exc):
                    raise
                # Calendar failure must not block email sync
                logger.warning("calendar_sync failed for %s: %s", connection.email_address, cal_exc)

            # Update connection state
            connection.last_sync_epoch = current_epoch
            connection.backfill_completed = True
            connection.last_error = None
            connection.updated_at = __import__("datetime").datetime.utcnow()
            session.add(connection)
            await session.commit()

            logger.info(
                "personal_email_sync: %s → %d emails, %d activities, %d contacts, "
                "%d companies, %d tasks, %d meetings (calendar)",
                connection.email_address,
                stats["emails_processed"],
                stats["activities_created"],
                stats["contacts_created"],
                stats["companies_created"],
                stats["tasks_created"],
                stats.get("meetings_from_calendar", 0),
            )
            return {"status": "completed", **stats}

    except Exception as exc:
        # Write error back to connection row so UI can surface it
        try:
            async with SessionLocal() as err_session:
                conn = await err_session.get(UserEmailConnection, connection_id)
                if conn:
                    if _is_invalid_grant(exc):
                        conn.is_active = False
                    conn.last_error = str(exc)[:500]
                    conn.updated_at = datetime.utcnow()
                    err_session.add(conn)
                    await err_session.commit()
        except Exception:
            pass
        raise

    finally:
        await engine.dispose()
