"""
Celery task: periodically sync Instantly campaign stats & lead statuses.

Runs every 15 minutes as a fallback for webhook delivery gaps.
Updates contact sequence_status, instantly_status, and email tracking
counts for all contacts linked to active Instantly campaigns.
"""
import asyncio
import logging
from datetime import datetime
from uuid import UUID

from sqlmodel import select

from app.celery_app import celery_app
from app.clients.instantly import InstantlyClient, InstantlyError
from app.config import settings
from app.database import AsyncSessionLocal
from app.models.contact import Contact
from app.models.outreach import OutreachSequence

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.instantly_sync.sync_active_instantly_campaigns",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def sync_active_instantly_campaigns(self) -> dict:
    """Sync contact statuses for all sequences linked to active Instantly campaigns."""
    asyncio.set_event_loop(asyncio.new_event_loop())
    loop = asyncio.get_event_loop()
    try:
        return loop.run_until_complete(_async_sync_active_campaigns())
    finally:
        loop.close()


async def _async_sync_active_campaigns() -> dict:
    if not settings.INSTANTLY_API_KEY:
        return {"status": "skipped", "reason": "INSTANTLY_API_KEY not configured"}

    async with AsyncSessionLocal() as session:
        # Find all sequences with active Instantly campaigns
        result = await session.execute(
            select(OutreachSequence).where(
                OutreachSequence.instantly_campaign_id.isnot(None),
                OutreachSequence.instantly_campaign_status.in_(["active", "paused", None]),
            )
        )
        sequences = result.scalars().all()

        if not sequences:
            return {"status": "ok", "synced": 0, "message": "No active campaigns"}

        client = InstantlyClient()
        if client.is_mock:
            return {"status": "skipped", "reason": "mock mode"}

        synced = 0
        errors = 0

        for seq in sequences:
            try:
                campaign_id = seq.instantly_campaign_id
                analytics_list = await client.get_campaign_analytics(campaign_id=campaign_id)
                if not analytics_list:
                    continue

                analytics = analytics_list[0] if isinstance(analytics_list, list) else analytics_list

                # Update campaign status
                status_map = {0: "draft", 1: "active", 2: "paused", 3: "completed"}
                campaign_status = status_map.get(analytics.get("campaign_status"))
                if campaign_status and campaign_status != seq.instantly_campaign_status:
                    seq.instantly_campaign_status = campaign_status
                    seq.updated_at = datetime.utcnow()
                    session.add(seq)

                # Sync lead status for the contact
                contact = await session.get(Contact, seq.contact_id)
                if contact and contact.email:
                    try:
                        leads_result = await client.list_leads(
                            campaign_id=campaign_id,
                            search=contact.email,
                            limit=5,
                        )
                        if leads_result:
                            lead_items = leads_result.get("items") or []
                            for lead in lead_items:
                                lead_email = (lead.get("email") or "").lower().strip()
                                if lead_email != contact.email.lower().strip():
                                    continue

                                lead_status = lead.get("status")
                                interest = lead.get("lt_interest_status")

                                if lead_status == -1 and contact.sequence_status != "bounced":
                                    contact.sequence_status = "bounced"
                                    contact.instantly_status = "bounced"
                                    contact.email_verified = False
                                elif lead_status == -2 and contact.sequence_status != "unsubscribed":
                                    contact.sequence_status = "unsubscribed"
                                    contact.instantly_status = "unsubscribed"
                                elif interest == 2 and contact.sequence_status != "meeting_booked":
                                    contact.sequence_status = "meeting_booked"
                                    contact.instantly_status = "meeting_booked"
                                elif interest == 1 and contact.sequence_status != "interested":
                                    contact.sequence_status = "interested"
                                    contact.instantly_status = "interested"
                                elif interest == -1 and contact.sequence_status != "not_interested":
                                    contact.sequence_status = "not_interested"
                                    contact.instantly_status = "not_interested"

                                if lead.get("email_open_count", 0) > (contact.email_open_count or 0):
                                    contact.email_open_count = lead["email_open_count"]
                                    if lead.get("timestamp_last_open"):
                                        contact.email_last_opened_at = datetime.fromisoformat(
                                            lead["timestamp_last_open"].replace("Z", "+00:00")
                                        ).replace(tzinfo=None)
                                if lead.get("email_click_count", 0) > (contact.email_click_count or 0):
                                    contact.email_click_count = lead["email_click_count"]

                                contact.updated_at = datetime.utcnow()
                                session.add(contact)
                                synced += 1
                    except Exception:
                        errors += 1
                        logger.exception("Failed to sync leads for campaign %s", campaign_id)

            except InstantlyError:
                errors += 1
                logger.exception("Failed to sync campaign %s", seq.instantly_campaign_id)
            except Exception:
                errors += 1
                logger.exception("Unexpected error syncing campaign %s", seq.instantly_campaign_id)

        await session.commit()

    return {"status": "ok", "synced": synced, "errors": errors, "campaigns_checked": len(sequences)}
