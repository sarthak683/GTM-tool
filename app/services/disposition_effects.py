"""
Channel-disposition side effects.

A single place that mirrors the rep's manual channel actions (call disposition,
LinkedIn touch) back into the contact's machine state: sequence_status,
follow-up tasks, Instantly campaign state.

Why this exists
---------------
Before this module, derivation lived in the frontend (`prospectWorkflow.ts`)
and the client sent the updated sequence_status along with the PATCH. That
worked for the happy path but:
  1. Trusted the client for state transitions.
  2. Didn't auto-create follow-up tasks (rep logs "interested" but no "book a
     call" task appeared until the periodic system-task refresh ran).
  3. Didn't pause the Instantly campaign on DNC — the prospect kept receiving
     emails for days after the rep said "do not contact".

Putting the logic in one backend helper means every caller — PUT /contacts,
Instantly webhook, Aircall webhook, CSV import future actions — gets the
same behavior for free.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.outreach import OutreachSequence

logger = logging.getLogger(__name__)

# Terminal statuses: once a contact is here we never regress to an earlier
# state automatically. Rep can still override manually.
_TERMINAL_STATES = {
    "meeting_booked",
    "not_interested",
    "unsubscribed",
    "bounced",
    "completed",
}

# Call dispositions → target sequence_status. Mirrors the frontend map in
# `prospectWorkflow.ts::CALL_DISPOSITION_OPTIONS` so both stay in sync.
CALL_DISPOSITION_TO_STATUS: dict[str, str] = {
    "demo_scheduled_booked": "meeting_booked",
    "meeting_confirmed": "meeting_booked",
    "interested_follow_up_required": "interested",
    "call_back_later_rescheduled": "interested",
    "redirected_other_icp": "interested",
    "connected_not_interested": "not_interested",
    "do_not_contact_dnc": "not_interested",
    "contact_poor_fit": "not_interested",
}

# Dispositions that should pause the Instantly campaign (stop future emails).
# We pause on any "dead end" signal, not just DNC, because continuing to send
# looks unprofessional and can trigger spam complaints.
_PAUSE_CAMPAIGN_DISPOSITIONS = {
    "do_not_contact_dnc",
    "contact_poor_fit",
    "connected_not_interested",
}

# Dispositions that indicate the rep landed a meeting live on the phone.
# We also pause the campaign here so no follow-up email hits after the demo
# is booked — reps have seen Instantly send step 2 *after* the demo, which is
# awkward. Better to pause; the rep can always resume manually if needed.
_MEETING_BOOKED_DISPOSITIONS = {
    "demo_scheduled_booked",
    "meeting_confirmed",
}

# Dispositions for which the rep is expected to attach a follow-up datetime
# (next_followup_at). When the disposition is anything else we clear the
# stored follow-up so a stale date doesn't keep showing on the prospect row.
_FOLLOWUP_DISPOSITIONS = {
    "interested_follow_up_required",
    "call_back_later_rescheduled",
}


def _default_followup_utc() -> datetime:
    """Tomorrow at 10am PST, stored as naive UTC for DB consistency."""
    now = datetime.utcnow()
    return (now + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)


async def _maybe_suggest_deal_from_disposition(
    session: AsyncSession, contact: Contact, disposition: str
) -> bool:
    """When a rep marks a meeting booked, raise a 'create a deal?' bell alert
    for the prospect owner. Accepting it auto-creates the deal (handled in the
    notifications endpoint, type=meeting_booked_suggest_deal).

    Skipped when the account already has a deal (no duplicate pipeline) or the
    prospect has no owner to notify. De-duped per contact so repeated saves of
    the same disposition don't spam the bell.
    """
    if disposition not in _MEETING_BOOKED_DISPOSITIONS:
        return False

    from app.models.deal import Deal, DealContact

    has_deal = False
    if contact.company_id:
        has_deal = (await session.execute(
            select(Deal.id).where(Deal.company_id == contact.company_id).limit(1)
        )).first() is not None
    if not has_deal:
        has_deal = (await session.execute(
            select(DealContact.deal_id).where(DealContact.contact_id == contact.id).limit(1)
        )).first() is not None
    if has_deal:
        return False

    recipient_id = contact.sdr_id or contact.assigned_to_id
    if not recipient_id:
        return False

    contact_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip() or (contact.email or "Prospect")
    company_name = None
    if contact.company_id:
        from app.models.company import Company

        co = (await session.execute(
            select(Company).where(Company.id == contact.company_id)
        )).scalar_one_or_none()
        if co:
            company_name = co.name

    from app.services.notifications import create_notification

    await create_notification(
        session,
        user_id=recipient_id,
        type="meeting_booked_suggest_deal",
        title=f"Meeting booked with {contact_name}",
        body=(
            f"You marked a meeting booked{f' at {company_name}' if company_name else ''}. "
            "Create a deal in the pipeline?"
        ),
        action_payload={
            "contact_id": str(contact.id),
            "contact_name": contact_name,
            "company_id": str(contact.company_id) if contact.company_id else None,
            "company_name": company_name,
            "source": "call_disposition",
        },
        dedup_key=f"suggest_deal:{contact.id}",
    )
    return True


def _should_advance(current: Optional[str], target: str) -> bool:
    """Decide whether to move from `current` to `target`.

    Rules:
      - Always advance if current is None/empty or a pre-outreach state.
      - `meeting_booked` can be set from anywhere (it's the happiest outcome).
      - Don't regress from one terminal state to a different one.
      - Don't overwrite `replied` with `interested` (replied is richer signal).
    """
    if target == "meeting_booked":
        return current != "meeting_booked"  # no-op if already booked
    if current in _TERMINAL_STATES:
        return False  # don't change terminal → different terminal
    if current == "replied" and target == "interested":
        return False  # replied implies interested already
    return current != target


def derive_status_from_call_disposition(
    disposition: Optional[str],
    current_status: Optional[str],
) -> Optional[str]:
    """Return the sequence_status that should follow this disposition, or None
    if no transition is warranted. Does NOT mutate anything."""
    if not disposition:
        return None
    target = CALL_DISPOSITION_TO_STATUS.get(disposition)
    if not target:
        return None
    return target if _should_advance(current_status, target) else None


def derive_status_from_linkedin(
    linkedin_status: Optional[str],
    current_status: Optional[str],
) -> Optional[str]:
    """Mirror the client mapping (deriveSequenceStatusFromLinkedinStatus) so a
    LinkedIn outcome advances the funnel server-side too — even when an API
    caller or older client doesn't also send sequence_status. Connect-request
    sent/accepted are early touches and don't move the funnel on their own."""
    if not linkedin_status or linkedin_status == "none":
        return None
    if linkedin_status == "meeting_booked":
        target = "meeting_booked"
    elif current_status == "meeting_booked":
        return None  # never downgrade a booked meeting
    elif linkedin_status == "meeting_rejected":
        target = "not_interested"
    elif linkedin_status in ("follow_up", "replied"):
        target = "replied"  # active conversation
    else:
        return None  # sent / accepted
    return target if _should_advance(current_status, target) else None


async def _maybe_pause_instantly_campaign(
    session: AsyncSession,
    contact: Contact,
    reason: str,
) -> bool:
    """Pause the contact's active Instantly campaign so queued emails stop.

    Safe to call when there's no campaign — it just no-ops. We also tolerate
    Instantly API failures (logged, not raised) because the rep-facing state
    change on the contact is more important than the remote pause; a retry
    worker or manual clean-up can catch stragglers.
    """
    campaign_id = contact.instantly_campaign_id
    if not campaign_id:
        # Fall back to the sequence record in case the contact's cached
        # campaign id was cleared
        seq = (
            await session.execute(
                select(OutreachSequence).where(
                    OutreachSequence.contact_id == contact.id
                )
            )
        ).scalars().first()
        if seq and seq.instantly_campaign_id:
            campaign_id = seq.instantly_campaign_id

    if not campaign_id:
        return False

    try:
        from app.clients.instantly import InstantlyClient

        client = InstantlyClient()
        await client.pause_campaign(campaign_id)
        logger.info(
            "Paused Instantly campaign %s for contact %s (reason=%s)",
            campaign_id,
            contact.id,
            reason,
        )
        return True
    except Exception as exc:  # pragma: no cover — network/transient
        logger.warning(
            "Failed to pause Instantly campaign %s for contact %s: %s",
            campaign_id,
            contact.id,
            exc,
        )
        return False


async def apply_call_disposition_effects(
    session: AsyncSession,
    contact: Contact,
    *,
    disposition: Optional[str],
    refresh_tasks: bool = True,
) -> dict[str, str]:
    """Apply all side effects of a manual call disposition on a contact.

    Returns a dict describing what changed, useful for logging + responses.
    Caller is expected to commit.
    """
    changes: dict[str, str] = {}
    if not disposition:
        return changes

    # If the rep moved the contact off of a follow-up disposition (or to a
    # terminal one like "not_interested"), drop any previously-saved
    # next_followup_at so the prospect-page no longer shows a stale date.
    if disposition not in _FOLLOWUP_DISPOSITIONS and contact.next_followup_at is not None:
        changes["next_followup_at"] = f"{contact.next_followup_at} -> None"
        contact.next_followup_at = None
        session.add(contact)

    # Follow-up/callback outcomes are not complete without a next date. The
    # UI normally sends one, but this backend default protects API callers,
    # older clients, and failed browser state from leaving reps with a blue /
    # white progress marker and no due date.
    if disposition in _FOLLOWUP_DISPOSITIONS and contact.next_followup_at is None:
        contact.next_followup_at = _default_followup_utc()
        changes["next_followup_at"] = f"None -> {contact.next_followup_at}"
        contact.updated_at = datetime.utcnow()
        session.add(contact)

    new_status = derive_status_from_call_disposition(
        disposition, contact.sequence_status
    )
    if new_status and new_status != contact.sequence_status:
        changes["sequence_status"] = f"{contact.sequence_status} -> {new_status}"
        contact.sequence_status = new_status
        contact.updated_at = datetime.utcnow()
        session.add(contact)

    if disposition in _PAUSE_CAMPAIGN_DISPOSITIONS or disposition in _MEETING_BOOKED_DISPOSITIONS:
        paused = await _maybe_pause_instantly_campaign(
            session, contact, reason=f"disposition={disposition}"
        )
        if paused:
            changes["instantly"] = "paused"
            contact.instantly_status = "paused"
            session.add(contact)

    # Meeting booked on the phone → suggest creating a deal (bell alert; accept
    # auto-creates the deal). Deduped + skipped when a deal already exists.
    if await _maybe_suggest_deal_from_disposition(session, contact, disposition):
        changes["deal_suggestion"] = "created"

    if refresh_tasks:
        # Delayed import to avoid a circular dependency with app.services.tasks
        from app.services.tasks import refresh_system_tasks_for_entity

        await refresh_system_tasks_for_entity(session, "contact", contact.id)

    return changes


async def apply_linkedin_status_effects(
    session: AsyncSession,
    contact: Contact,
    *,
    linkedin_status: Optional[str],
    refresh_tasks: bool = True,
) -> dict[str, str]:
    """Mirror of apply_call_disposition_effects for LinkedIn."""
    changes: dict[str, str] = {}
    if not linkedin_status:
        return changes

    new_status = derive_status_from_linkedin(
        linkedin_status, contact.sequence_status
    )
    if new_status and new_status != contact.sequence_status:
        changes["sequence_status"] = f"{contact.sequence_status} -> {new_status}"
        contact.sequence_status = new_status
        contact.updated_at = datetime.utcnow()
        session.add(contact)

    # A meeting booked (or hard-rejected) over LinkedIn must stop the Instantly
    # sequence too — otherwise the prospect keeps getting cadence emails after
    # the meeting is set, the same awkwardness the call path already guards.
    if linkedin_status in {"meeting_booked", "meeting_rejected"}:
        paused = await _maybe_pause_instantly_campaign(
            session, contact, reason=f"linkedin={linkedin_status}"
        )
        if paused:
            changes["instantly"] = "paused"
            contact.instantly_status = "paused"
            session.add(contact)

    if refresh_tasks:
        from app.services.tasks import refresh_system_tasks_for_entity

        await refresh_system_tasks_for_entity(session, "contact", contact.id)

    return changes
