"""
Celery task: periodically sync Instantly campaign stats & lead statuses.

Runs every 15 minutes as a fallback for webhook delivery gaps.
Updates contact sequence_status, instantly_status, and email tracking
counts for all contacts linked to active Instantly campaigns.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import select

from app.celery_app import celery_app
from app.clients.instantly import InstantlyClient, InstantlyError
from app.config import settings
from app.database import task_session
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.outreach import OutreachSequence

logger = logging.getLogger(__name__)


def _parse_instantly_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


async def _existing_synced_event_count(session, contact_id: UUID, event_type: str) -> int:
    result = await session.execute(
        select(Activity.id).where(
            Activity.contact_id == contact_id,
            Activity.source == "instantly",
            Activity.medium == "email",
            Activity.event_metadata["event_type"].as_string() == event_type,
        )
    )
    return len(result.scalars().all())


async def _backfill_synced_email_events(
    session,
    *,
    contact: Contact,
    campaign_id: str,
    campaign_name: str | None,
    lead: dict,
    event_type: str,
    count_field: str,
    timestamp_field: str,
    content_label: str,
) -> int:
    """Persist activity rows for Instantly counters seen during polling.

    Instantly's lead polling endpoint usually exposes aggregate counters, not
    a complete event stream. The lifecycle drawer still needs a row per open
    or click so reps can inspect the cadence history. We create deterministic,
    idempotent synthetic rows for any counter value that does not yet have a
    matching activity.
    """
    if not contact.id or not contact.email:
        return 0

    target_count = _safe_int(lead.get(count_field))
    if target_count <= 0:
        return 0

    existing_count = await _existing_synced_event_count(session, contact.id, event_type)
    missing = target_count - existing_count
    if missing <= 0:
        return 0

    anchor = _parse_instantly_datetime(lead.get(timestamp_field)) or datetime.utcnow()
    created = 0
    email = contact.email.lower().strip()
    subject = lead.get("subject") or lead.get("email_subject") or None
    sender = lead.get("email_account") or lead.get("from_email") or None

    for index in range(existing_count + 1, target_count + 1):
        external_id = f"{campaign_id}:{email}:{event_type}:{index}"
        duplicate = (
            await session.execute(
                select(Activity.id).where(
                    Activity.external_source == "instantly_sync",
                    Activity.external_source_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if duplicate:
            continue

        # If several events are discovered in one poll, only the latest event's
        # exact timestamp is known. Stagger earlier synthetic events slightly so
        # ordering remains readable without inventing misleading dates.
        created_at = anchor - timedelta(minutes=max(target_count - index, 0))
        activity = Activity(
            type="email",
            source="instantly",
            medium="email",
            content=f"{content_label}: {email}",
            contact_id=contact.id,
            external_source="instantly_sync",
            external_source_id=external_id,
            event_metadata={
                "event_type": event_type,
                "synthetic_from_sync": True,
                "counter_index": index,
                "counter_total": target_count,
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "lead_email": email,
                "synced_at": datetime.utcnow().isoformat(),
                "source_timestamp": anchor.isoformat(),
            },
            email_subject=subject,
            email_from=sender,
            email_to=email,
            created_at=created_at,
        )
        session.add(activity)
        created += 1

    return created


def _run_async_task(coro):
    """Run a coroutine inside a fresh event loop with orderly shutdown.

    Without the explicit shutdown_asyncgens + pending-task cancel, aiohttp's
    connector keeps background tasks alive that try to call back into the
    closed loop on the next Celery run, raising
    "Future attached to a different loop" and silently killing every
    beat-scheduled invocation. Mirrors the helper in tldv_sync.py.
    """
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
    name="app.tasks.instantly_sync.sync_active_instantly_campaigns",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def sync_active_instantly_campaigns(self) -> dict:
    """Sync contact statuses for all sequences linked to active Instantly campaigns."""
    return _run_async_task(_async_sync_active_campaigns())


async def _async_sync_active_campaigns() -> dict:
    if not settings.INSTANTLY_API_KEY:
        return {"status": "skipped", "reason": "INSTANTLY_API_KEY not configured"}

    async with task_session() as session:
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
                                    last_open = _parse_instantly_datetime(lead.get("timestamp_last_open"))
                                    if last_open:
                                        contact.email_last_opened_at = last_open
                                if lead.get("email_click_count", 0) > (contact.email_click_count or 0):
                                    contact.email_click_count = lead["email_click_count"]

                                events_created = 0
                                events_created += await _backfill_synced_email_events(
                                    session,
                                    contact=contact,
                                    campaign_id=campaign_id,
                                    campaign_name=getattr(seq, "campaign_name", None),
                                    lead=lead,
                                    event_type="email_opened",
                                    count_field="email_open_count",
                                    timestamp_field="timestamp_last_open",
                                    content_label="Email opened (synced from Instantly)",
                                )
                                events_created += await _backfill_synced_email_events(
                                    session,
                                    contact=contact,
                                    campaign_id=campaign_id,
                                    campaign_name=getattr(seq, "campaign_name", None),
                                    lead=lead,
                                    event_type="email_link_clicked",
                                    count_field="email_click_count",
                                    timestamp_field="timestamp_last_click",
                                    content_label="Email link clicked (synced from Instantly)",
                                )

                                contact.updated_at = datetime.utcnow()
                                session.add(contact)
                                if events_created:
                                    from app.services.tasks import refresh_system_tasks_for_entity

                                    await refresh_system_tasks_for_entity(session, "contact", contact.id)
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
