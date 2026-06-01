"""Follow-up due reminders for prospects (the SDR equivalent of deal_reminders).

Finds prospects whose `next_followup_at` has passed and fires a one-time in-app
(+ push) notification to the owning SDR/rep. Reps were setting follow-up dates
(or having them auto-set on a callback disposition) but nothing nudged them when
the callback came due — the reminder loop was dark on the prospect side.

Idempotent: the notification dedup_key includes the due timestamp, so a given
due date notifies exactly once; rescheduling produces a fresh reminder.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.contact import Contact
from app.services.notifications import create_notification

logger = logging.getLogger(__name__)

# Bound the scan to recently-due follow-ups (don't re-nudge ancient overdue ones).
LOOKBACK_DAYS = 14
# No point nudging a follow-up on a prospect that's already terminal/booked.
_TERMINAL_STATUSES = {"not_interested", "meeting_booked", "bounced", "unsubscribed", "do_not_contact"}


async def send_due_prospect_followup_reminders() -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        now = datetime.utcnow()
        window_start = now - timedelta(days=LOOKBACK_DAYS)

        rows = (
            await session.execute(
                select(Contact).where(
                    Contact.next_followup_at.is_not(None),
                    Contact.next_followup_at <= now,
                    Contact.next_followup_at >= window_start,
                )
            )
        ).scalars().all()

        checked = 0
        notified = 0
        for contact in rows:
            owner = contact.sdr_id or contact.assigned_to_id
            if not owner:
                continue
            if (contact.sequence_status or "").strip().lower() in _TERMINAL_STATUSES:
                continue
            checked += 1
            due_iso = contact.next_followup_at.isoformat()
            name = f"{contact.first_name or ''} {contact.last_name or ''}".strip() or contact.email or "prospect"
            note = await create_notification(
                session,
                user_id=owner,
                type="prospect_followup_due",
                title=f"Follow-up due — {name}",
                body=contact.call_notes or "Time to follow up with this prospect.",
                action_payload={"contact_id": str(contact.id), "url": "/contacts"},
                dedup_key=f"prospect_followup_due:{contact.id}:{due_iso}",
                push=True,
            )
            if note.created_at and note.created_at >= now - timedelta(minutes=5):
                notified += 1

        logger.info("prospect follow-up reminders: checked=%s notified=%s", checked, notified)
        return {"checked": checked, "notified": notified}
