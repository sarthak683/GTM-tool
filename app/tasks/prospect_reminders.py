"""Beat-scheduled prospect follow-up reminder sender (SDR equivalent of
app.tasks.deal_reminders) — nudges the owning SDR when a prospect callback /
follow-up comes due, so the reminder loop isn't pull-only.
"""
from __future__ import annotations

import asyncio
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async_task(coro):
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
    name="app.tasks.prospect_reminders.send_due_prospect_followup_reminders",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
)
def send_due_prospect_followup_reminders(self) -> dict:
    """Notify owning SDRs about prospects whose follow-up is due/overdue."""
    from app.services.prospect_reminders import send_due_prospect_followup_reminders as run

    try:
        return _run_async_task(run())
    except Exception:
        logger.exception("prospect follow-up reminder send failed")
        raise
