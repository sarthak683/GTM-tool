"""Celery task for periodic tl;dv meeting synchronization.

Runs every minute via Celery beat, but self-throttles using the
``tldv_sync_interval_minutes`` setting stored in WorkspaceSettings.
Default interval is 5 minutes.  Each successful run writes
``tldv_last_synced_at`` back so the next run only pulls meetings
newer than that timestamp (incremental mode).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.celery_app import celery_app
from app.tasks._runner import run_async_task as _run_async_task

logger = logging.getLogger(__name__)

# How far behind the stored cursor each run re-scans. Covers tl;dv's own
# recording upload + processing delay, during which a finished meeting exists
# but is not yet returned by the API. Deliberately much wider than the 5-minute
# sync interval: the cost of overlap is a few deduped rows, the cost of a gap is
# a permanently missing meeting.
CURSOR_OVERLAP = timedelta(hours=6)


@celery_app.task(name="app.tasks.tldv_sync.sync_tldv_meetings")
def sync_tldv_meetings() -> dict:
    """Sync recent tl;dv meetings into Beacon CRM (incremental, self-throttled)."""
    return _run_async_task(_async_sync())


async def _async_sync() -> dict:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select
    from app.clients.tldv import TldvClient
    from app.config import settings
    from app.models.settings import WorkspaceSettings
    from app.services.tldv_sync import sync_tldv_history

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with SessionLocal() as session:
            try:
                row = (await session.execute(select(WorkspaceSettings).where(WorkspaceSettings.id == 1))).scalar_one_or_none()
                cfg: dict = row.sync_schedule_settings if row and isinstance(row.sync_schedule_settings, dict) else {}

                if not cfg.get("tldv_sync_enabled", True):
                    logger.info("tl;dv sync is disabled in settings, skipping")
                    return {"status": "disabled"}

                # Missing credentials is a configuration state, not a failure —
                # report it as a skip so it shows as "Idle: tl;dv API key not
                # configured" rather than a permanent red badge an operator
                # learns to ignore. sync_tldv_history raises ValueError for this
                # (TldvClient.mock), which is indistinguishable from a real
                # error once it reaches the except below, so check it up front.
                if TldvClient().mock:
                    logger.info("tl;dv sync skipped — API key not configured")
                    return {"status": "skipped", "reason": "tl;dv API key not configured"}

                # ── Self-throttle: skip if not enough time has passed ─────────────
                interval_minutes: int = int(cfg.get("tldv_sync_interval_minutes") or 5)
                last_synced_raw = cfg.get("tldv_last_synced_at")
                last_synced_at: datetime | None = None
                if last_synced_raw:
                    try:
                        last_synced_at = datetime.fromisoformat(str(last_synced_raw))
                    except ValueError:
                        last_synced_at = None

                if last_synced_at and datetime.utcnow() - last_synced_at < timedelta(minutes=interval_minutes):
                    logger.debug(
                        "tl;dv sync skipped — last ran %s, interval %d min",
                        last_synced_at.isoformat(), interval_minutes,
                    )
                    return {"status": "throttled", "next_run_in_minutes": interval_minutes}

                page_size: int = int(cfg.get("tldv_page_size") or 10)
                max_pages: int = int(cfg.get("tldv_max_pages") or 2)

                # Re-scan a window behind the cursor rather than starting exactly
                # where the last run stopped. A tl;dv meeting becomes visible to
                # the API only after the recording finishes uploading and
                # processing, so its own timestamp can already be older than the
                # cursor by the time we could have seen it. With an exact cursor
                # that meeting is skipped once and then never looked at again —
                # a silently lost call recording, with the task still reporting
                # success. Re-scanning is free: sync_tldv_history dedupes on
                # external_source_id, so a re-seen meeting is a no-op update.
                scan_from = (
                    last_synced_at - CURSOR_OVERLAP if last_synced_at else None
                )

                result = await sync_tldv_history(
                    session,
                    page_size=page_size,
                    max_pages=max_pages,
                    since=scan_from,  # None on first run → full lookback
                )

                # ── Advance the cursor only past work that actually happened ──
                # Base target is when the scan STARTED (a meeting appearing
                # mid-scan was never seen). Two caps pull it further back:
                #  * truncated: the page budget stopped us with unfetched
                #    meetings still inside the window — advancing past
                #    oldest_seen_at would skip them permanently (this is the
                #    mechanism behind "tl;dv imported nothing since July").
                #  * failed: a poisoned meeting was skipped this run; keep it
                #    inside the next run's window so it gets retried.
                def _parse_iso(value):
                    try:
                        return datetime.fromisoformat(str(value)) if value else None
                    except ValueError:
                        return None

                cursor_target = _parse_iso(result.get("sync_started_at")) or datetime.utcnow()
                if result.get("truncated"):
                    oldest_seen = _parse_iso(result.get("oldest_seen_at"))
                    if oldest_seen is not None:
                        cursor_target = min(cursor_target, oldest_seen)
                first_failed = _parse_iso(result.get("first_failed_at"))
                if first_failed is not None:
                    cursor_target = min(cursor_target, first_failed)

                # ── Write the cursor back WITHOUT clobbering concurrent edits ──
                # `cfg` is a snapshot from task start, and this sync can run for
                # minutes. Writing that stale snapshot back erased anything
                # committed in between — report send-keys (causing duplicate
                # report sends) and admin settings edits (silently reverted).
                # Re-read the row fresh under FOR UPDATE and patch ONLY our key.
                if row:
                    fresh = (
                        await session.execute(
                            select(WorkspaceSettings)
                            .where(WorkspaceSettings.id == 1)
                            .with_for_update()
                            # Without populate_existing the identity map hands
                            # back the stale `row` object from task start and
                            # the whole re-read is theater.
                            .execution_options(populate_existing=True)
                        )
                    ).scalar_one_or_none()
                    if fresh is not None:
                        updated_cfg = dict(fresh.sync_schedule_settings or {})
                        updated_cfg["tldv_last_synced_at"] = cursor_target.isoformat()
                        fresh.sync_schedule_settings = updated_cfg
                        session.add(fresh)
                    await session.commit()

                logger.info("tl;dv sync completed: %s", result)
                return result if isinstance(result, dict) else {"status": "ok"}
            except Exception:
                # Re-raise rather than returning {"error": ...}. Swallowing the
                # exception into a return value makes Celery mark the task
                # SUCCESS, which is what job_health records — so a tl;dv sync
                # that has been failing for weeks shows a green badge on the
                # System Health panel. Note the cursor is NOT advanced on this
                # path, so the next run retries the same window.
                logger.exception("tl;dv sync failed")
                raise
    finally:
        await engine.dispose()
