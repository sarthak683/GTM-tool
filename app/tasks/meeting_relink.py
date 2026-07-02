"""Periodic backfill: re-link orphaned meetings.

The live tl;dv/calendar sync links each meeting once, at ingest. Meetings whose
company/deal/contact did not exist yet stay permanently unlinked. This task
re-runs the precision matcher (``app.services.meeting_relink``) daily so those
strays self-heal once the CRM catches up — keeping rep analytics complete.

Safe to run repeatedly: it only touches meetings with company_id AND deal_id
both NULL, and only attaches when exactly one sourced company matches.
"""
from __future__ import annotations

import asyncio
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async_task(coro):
    """Run a coroutine in a fresh event loop with orderly shutdown."""
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


@celery_app.task(name="app.tasks.meeting_relink.relink_unlinked_meetings_task")
def relink_unlinked_meetings_task() -> dict:
    """Re-link unlinked meetings (writes). Returns counts only (no payloads)."""
    return _run_async_task(_async_relink())


async def _async_relink() -> dict:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.config import settings
    from app.services.meeting_relink import relink_unlinked_meetings

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            result = await relink_unlinked_meetings(session, dry_run=False)
    finally:
        await engine.dispose()

    # Drop the per-meeting proposals from the Celery result blob — keep it small.
    return {
        "scanned": result["scanned"],
        "matched": result["matched"],
        "linked_company": result["linked_company"],
        "linked_deal": result["linked_deal"],
    }
