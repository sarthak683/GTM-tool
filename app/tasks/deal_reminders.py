"""Beat-scheduled next-step reminder sender.

Wraps app.services.deal_reminders.send_due_next_step_reminders in a Celery task
so reps get an in-app (and push) nudge when a deal's next step comes due,
without anyone having to watch the board.
"""
from __future__ import annotations

import asyncio
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async_task(coro):
    """Same orderly-shutdown helper used by pre_meeting_brief / tldv_sync."""
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
    name="app.tasks.deal_reminders.send_due_next_step_reminders",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def send_due_next_step_reminders(self) -> dict:
    """Notify assigned reps about deals whose next step is due/overdue."""
    from app.services.deal_reminders import send_due_next_step_reminders as run

    try:
        return _run_async_task(run())
    except Exception:
        logger.exception("next-step reminder send failed")
        raise
