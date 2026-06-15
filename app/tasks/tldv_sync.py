"""Celery task for periodic tl;dv meeting synchronization.

Runs every minute via Celery beat, but self-throttles using the
``tldv_sync_interval_minutes`` setting stored in WorkspaceSettings.
Default interval is 5 minutes.  Each successful run writes
``tldv_last_synced_at`` back so the next run only pulls meetings
newer than that timestamp (incremental mode).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async_task(coro):
    """Run a coroutine inside a fresh event loop with orderly shutdown."""
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


@celery_app.task(name="app.tasks.tldv_sync.sync_tldv_meetings")
def sync_tldv_meetings() -> dict:
    """Sync recent tl;dv meetings into Beacon CRM (incremental, self-throttled)."""
    return _run_async_task(_async_sync())


async def _async_sync() -> dict:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select
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

                result = await sync_tldv_history(
                    session,
                    page_size=page_size,
                    max_pages=max_pages,
                    since=last_synced_at,  # None on first run → full lookback
                )

                # ── Write last_synced_at back to DB ───────────────────────────────
                if row:
                    updated_cfg = dict(cfg)
                    updated_cfg["tldv_last_synced_at"] = datetime.utcnow().isoformat()
                    row.sync_schedule_settings = updated_cfg
                    session.add(row)
                    await session.commit()

                logger.info("tl;dv sync completed: %s", result)
                return result if isinstance(result, dict) else {"status": "ok"}
            except Exception as exc:
                logger.warning("tl;dv sync failed: %s", exc)
                return {"error": str(exc)}
    finally:
        await engine.dispose()
