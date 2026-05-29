"""Next-step due reminders for pipeline deals.

Finds deals whose `next_step_due_at` has passed and fires a one-time in-app
notification to the assigned rep. Idempotent: the notification `dedup_key`
includes the due timestamp, so a given due date notifies exactly once — but
if the rep reschedules the next step, the new due time produces a fresh
reminder.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.deal import Deal
from app.services.deal_stages import get_configured_deal_stages
from app.services.notifications import create_notification

logger = logging.getLogger(__name__)

# Only remind for steps that came due in the recent past — avoids re-scanning
# ancient overdue deals forever and bounds the query.
LOOKBACK_DAYS = 14


async def send_due_next_step_reminders() -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        now = datetime.utcnow()
        window_start = now - timedelta(days=LOOKBACK_DAYS)

        # Closed-stage ids are skipped — no point nudging a won/lost deal.
        stages = await get_configured_deal_stages(session)
        closed_ids = {s["id"] for s in stages if s.get("group") == "closed"}

        rows = (
            await session.execute(
                select(Deal).where(
                    Deal.next_step_due_at.is_not(None),
                    Deal.next_step_due_at <= now,
                    Deal.next_step_due_at >= window_start,
                    Deal.assigned_to_id.is_not(None),
                )
            )
        ).scalars().all()

        checked = 0
        notified = 0
        for deal in rows:
            if deal.stage in closed_ids:
                continue
            checked += 1
            due_iso = deal.next_step_due_at.isoformat()
            dedup_key = f"next_step_due:{deal.id}:{due_iso}"
            before = await create_notification(
                session,
                user_id=deal.assigned_to_id,
                type="next_step_due",
                title=f"Next step due — {deal.name}",
                body=deal.next_step or "Follow up on this deal.",
                action_payload={"deal_id": str(deal.id), "url": f"/pipeline?deal={deal.id}"},
                dedup_key=dedup_key,
                push=True,
            )
            # create_notification returns the existing row on dedup hit; count a
            # fresh notify only when this run actually created it.
            if before.created_at and before.created_at >= now - timedelta(minutes=5):
                notified += 1

        logger.info("next-step reminders: checked=%s notified=%s", checked, notified)
        return {"checked": checked, "notified": notified}
