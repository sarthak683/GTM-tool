"""Beat-scheduled pre-meeting brief sender.

Wraps app.services.meeting_automation.run_due_pre_meeting_intel_once in
a Celery task so the brief lands in the rep's inbox automatically
instead of requiring an admin to hit the manual /run-now endpoint.

The 30-minute schedule matches the meeting prep window: a brief landing
~12 hours before a meeting (default send_hours_before in the workspace
settings) gives the rep plenty of time to read it without flooding the
inbox.
"""
from __future__ import annotations

import asyncio
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async_task(coro):
    """Same orderly-shutdown helper used by tldv_sync and instantly_sync."""
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


@celery_app.task(
    name="app.tasks.pre_meeting_brief.send_due_pre_meeting_briefs",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def send_due_pre_meeting_briefs(self) -> dict:
    """Find meetings in the send window and email pre-brief to assigned rep."""
    from app.services.meeting_automation import run_due_pre_meeting_intel_once

    try:
        return _run_async_task(run_due_pre_meeting_intel_once())
    except Exception:
        logger.exception("pre-meeting brief send failed")
        raise
