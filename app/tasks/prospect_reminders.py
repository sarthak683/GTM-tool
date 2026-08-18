"""Beat-scheduled prospect follow-up reminder sender (SDR equivalent of
app.tasks.deal_reminders) — nudges the owning SDR when a prospect callback /
follow-up comes due, so the reminder loop isn't pull-only.
"""
from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.tasks._runner import run_async_task as _run_async_task

logger = logging.getLogger(__name__)


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
