"""Beat-scheduled next-step reminder sender.

Wraps app.services.deal_reminders.send_due_next_step_reminders in a Celery task
so reps get an in-app (and push) nudge when a deal's next step comes due,
without anyone having to watch the board.
"""
from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.tasks._runner import run_async_task as _run_async_task

logger = logging.getLogger(__name__)


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
