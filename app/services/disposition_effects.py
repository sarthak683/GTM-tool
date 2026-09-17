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
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.visibility import unscoped_for_background_job
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


def is_meeting_booked_disposition(disposition: Optional[str]) -> bool:
    """True when this disposition means a meeting/demo was booked. Public so the
    activities endpoint can gate disposition side-effects on deal-page call logs
    without importing the private set."""
    return disposition in _MEETING_BOOKED_DISPOSITIONS

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


async def link_contact_as_meeting_booked(session: AsyncSession, contact: Contact) -> bool:
    """Link `contact` as a confirmed meeting contact on every open deal under
    their account — direct and unconditional, unlike
    `_maybe_suggest_deal_from_disposition`'s bell-notification flow below,
    which silently does nothing when the contact has no SDR/AE to notify
    (true for most prospects in this workspace). The deal drawer's Meeting
    Contact list only trusts role "primary"/"champion" (not the
    deal_linker.py backfill's "auto_linked"), so this is the one place that
    actually earns that trust once a meeting is confirmed booked.

    Idempotent and safe to call on every save, not just the first: `role in
    ("primary", "champion")` is left untouched, anything else (including no
    row at all) is created/upgraded to "primary". (deal_id, contact_id) is
    the table's primary key, so at most one row can exist per pair — an
    existing row is always updated in place, never inserted alongside.

    Permanent by design: nothing calls this to *remove* a link, so a later
    disposition/status change away from "meeting booked" does not un-link
    the contact — the deal drawer should keep showing them as historical
    proof a meeting was once booked with this person.
    """
    if not contact.company_id:
        return False

    from app.models.deal import Deal, DealContact

    deal_ids = (await session.execute(
        select(Deal.id).where(
            Deal.company_id == contact.company_id,
            Deal.pipeline_type == "deal",
            Deal.deleted_at.is_(None),
        )
    )).scalars().all()
    if not deal_ids:
        return False

    changed = False
    for deal_id in deal_ids:
        existing_link = (await session.execute(
            select(DealContact).where(
                DealContact.deal_id == deal_id,
                DealContact.contact_id == contact.id,
            )
        )).scalar_one_or_none()
        if existing_link is None:
            session.add(DealContact(deal_id=deal_id, contact_id=contact.id, role="primary"))
            changed = True
        elif existing_link.role not in ("primary", "champion"):
            existing_link.role = "primary"
            session.add(existing_link)
            changed = True
    return changed


async def _maybe_suggest_deal_from_disposition(
    session: AsyncSession, contact: Contact, disposition: str, *, activity_id: Optional[UUID] = None
) -> bool:
    """When a rep marks a meeting booked, raise a bell alert for the prospect
    owner (handled in the notifications endpoint, type=meeting_booked_suggest_deal).

    The call-to-action adapts to pipeline state:
      - No deal on the account yet -> "Create a deal?" (accept auto-creates one).
      - A deal already exists -> "Review the deal" (accept opens it). Reps book
        most demos on accounts that already have a deal, and they still want the
        heads-up, so we no longer swallow the alert in that case.

    Skipped only when the prospect has no owner to notify. De-duped per contact
    so repeated saves of the same disposition don't spam the bell.
    """
    if disposition not in _MEETING_BOOKED_DISPOSITIONS:
        return False

    from app.models.deal import Deal, DealContact

    existing_deal_id = None
    if contact.company_id:
        row = (await session.execute(
            select(Deal.id).where(Deal.company_id == contact.company_id).limit(1)
        )).first()
        existing_deal_id = row[0] if row else None
    if existing_deal_id is None:
        row = (await session.execute(
            select(DealContact.deal_id).where(DealContact.contact_id == contact.id).limit(1)
        )).first()
        existing_deal_id = row[0] if row else None

    recipient_id = contact.sdr_id or contact.assigned_to_id
    if not recipient_id:
        return False

    contact_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip() or (contact.email or "Prospect")
    company_name = None
    if contact.company_id:
        from app.models.company import Company

        co = (await session.execute(
            unscoped_for_background_job(Company, "disposition effects system work").where(Company.id == contact.company_id)
        )).scalar_one_or_none()
        if co:
            company_name = co.name

    at_company = f" at {company_name}" if company_name else ""
    if existing_deal_id is not None:
        body = f"You marked a meeting booked{at_company}. Review the deal in the pipeline."
        mode = "review"
    else:
        body = f"You marked a meeting booked{at_company}. Create a deal in the pipeline?"
        mode = "create"

    from app.services.notifications import create_notification

    await create_notification(
        session,
        user_id=recipient_id,
        type="meeting_booked_suggest_deal",
        title=f"Meeting booked with {contact_name}",
        body=body,
        action_payload={
            "contact_id": str(contact.id),
            "contact_name": contact_name,
            "company_id": str(contact.company_id) if contact.company_id else None,
            "company_name": company_name,
            "source": "call_disposition",
            "mode": mode,
            "deal_id": str(existing_deal_id) if existing_deal_id is not None else None,
            # Lets the accept handler backfill deal_id back onto the call that
            # triggered this alert — without it, the call Activity stays
            # deal_id=NULL forever even after a deal exists, which is why the
            # sales report's "Meeting date" showed "Pending" despite the deal
            # having a Date of Meeting.
            "activity_id": str(activity_id) if activity_id else None,
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

    # Campaign-level pause assumes the legacy 1-campaign-per-contact launch
    # path. With bulk enroll-in-campaign, MANY contacts share one campaign —
    # pausing it because ONE prospect said "not interested" would silently
    # stop outreach for the entire cohort. Only pause when no other contact
    # is enrolled in this campaign.
    others = (
        await session.execute(
            select(func.count(Contact.id)).where(
                Contact.instantly_campaign_id == campaign_id,
                Contact.id != contact.id,
            )
        )
    ).scalar() or 0
    if others:
        logger.info(
            "Skipping Instantly campaign pause for %s — campaign %s is shared "
            "by %d other contact(s) (reason=%s)",
            contact.id,
            campaign_id,
            others,
            reason,
        )
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
    activity_id: Optional[UUID] = None,
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

    # Meeting booked on the phone → link the contact to any open deal(s) on
    # the account directly, right now — no dependency on an SDR/AE existing
    # to notify (most prospects here have neither, which silently starved
    # the bell-alert path below).
    if disposition in _MEETING_BOOKED_DISPOSITIONS:
        if await link_contact_as_meeting_booked(session, contact):
            changes["meeting_contact_link"] = "linked"

    # Meeting booked on the phone → suggest creating a deal (bell alert; accept
    # auto-creates the deal). Deduped + skipped when a deal already exists.
    if await _maybe_suggest_deal_from_disposition(session, contact, disposition, activity_id=activity_id):
        changes["deal_suggestion"] = "created"

    # Meeting booked on the phone → reflect it on the parent account's status.
    # Server-side source of truth so the deal-page call logger (which records
    # the disposition in event_metadata and never PATCHes the contact) still
    # advances the account. Forward-only; a parked account may be revived by a
    # booked meeting.
    if disposition in _MEETING_BOOKED_DISPOSITIONS and contact.company_id:
        from app.services.account_status import bump_company_account_status

        new_status = await bump_company_account_status(
            session, contact.company_id, "meeting_booked"
        )
        if new_status:
            changes["account_status"] = f"{contact.account_status} -> {new_status}"

    if refresh_tasks:
        # Delayed import to avoid a circular dependency with app.services.tasks
        from app.services.tasks import refresh_system_tasks_for_entity

        await refresh_system_tasks_for_entity(session, "contact", contact.id)

    return changes


async def backfill_orphaned_meeting_booked_calls(
    session: AsyncSession, *, company_id: UUID, deal_id: UUID, deal_created_at: datetime
) -> None:
    """Link any orphaned meeting-booked call activities on this account to
    the just-created deal.

    Covers the manual create-deal path (POST /deals), which — unlike
    accepting the meeting_booked_suggest_deal bell notification — has no
    single activity_id to backfill directly: a rep can create the deal
    straight from the Pipeline UI after logging the call, bypassing the
    notification entirely. Without this, the call Activity that triggered
    the booking keeps deal_id=NULL forever, so the sales report's meeting
    date shows "Pending" even though a deal with a Date of Meeting exists.
    Same contact/company matching and 7-day window as migration 130's
    historical backfill and notifications._backfill_call_activity_deal_id;
    caller is responsible for commit.
    """
    from app.models.activity import Activity

    window_start = deal_created_at - timedelta(days=7)
    orphans = (
        await session.execute(
            select(Activity)
            .join(Contact, Contact.id == Activity.contact_id)
            .where(
                Activity.type == "call",
                Activity.deal_id.is_(None),
                Activity.contact_id.is_not(None),
                Contact.company_id == company_id,
                Activity.event_metadata["call_disposition"].astext.in_(
                    ["demo_scheduled_booked", "meeting_confirmed"]
                ),
                Activity.created_at >= window_start,
                Activity.created_at <= deal_created_at,
            )
        )
    ).scalars().all()
    for activity in orphans:
        activity.deal_id = deal_id
        session.add(activity)


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

    # A meeting booked over LinkedIn should advance the parent account's status
    # just like the call path does (LogLinkedInDialog writes the activity + the
    # contact, and the activity hook bumps the account to in_progress, but the
    # booked-meeting itself only lives in the effects function).
    if linkedin_status == "meeting_booked" and contact.company_id:
        from app.services.account_status import bump_company_account_status

        new_status = await bump_company_account_status(
            session, contact.company_id, "meeting_booked"
        )
        if new_status:
            changes["account_status"] = f"{contact.account_status} -> {new_status}"

    if refresh_tasks:
        from app.services.tasks import refresh_system_tasks_for_entity

        await refresh_system_tasks_for_entity(session, "contact", contact.id)

    return changes
