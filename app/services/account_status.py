"""Account-level status automation.

Company ``account_status`` is normally set manually by reps on the detail page,
but the source of truth should also reflect strong engagement signals without
the rep having to touch the dropdown twice. This module owns the forward-only
advance rules and the single helper the various activity / deal funnels call.

Rules (see ``should_advance_account_status``):
  * Lifecycle only moves FORWARD: cold -> in_progress -> meeting_booked ->
    meeting_done -> in_pipeline. A later signal never downgrades an account
    that has already progressed.
  * Manual parked states (``not_a_fit``, ``dnd``, ``reach_out_later``) are
    respected by weak signals: an ``in_progress`` touch does NOT revive a
    parked account. Only strong signals (meeting_booked / meeting_done /
    in_pipeline) override them, because a booked meeting or a live deal is
    observable reality regardless of an earlier manual label.

It also owns the DISABLE cascade (``apply_account_disable_effects``): when a
rep parks an account as ``not_a_fit``/``dnd``, its prospects leave the
prospecting queue via query-level gating (see
``ContactRepository.active_account_contact_filter``) — no per-contact state is
rewritten, so re-enabling the account restores everything — but running
Instantly campaigns are an EXTERNAL side effect that query filters cannot
stop, so those are paused here.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company, INACTIVE_ACCOUNT_STATUSES
from app.models.contact import Contact

logger = logging.getLogger(__name__)

# Strong lifecycle signals, ordered weakest -> strongest. Everything in this
# map is a value we automate TOWARD. Anything absent (e.g. the manual parked
# states) can never be a target of automation.
ACCOUNT_STATUS_LIFECYCLE_RANK: dict[str, int] = {
    "cold": 0,
    "in_progress": 1,
    "meeting_booked": 2,
    "meeting_done": 3,
    "in_pipeline": 4,
}

# Manual states reps deliberately park accounts in. Weak automation signals
# must not override these; strong lifecycle signals may.
NEGATIVE_ACCOUNT_STATUSES: frozenset[str] = frozenset(
    {"not_a_fit", "dnd", "reach_out_later"}
)


def should_advance_account_status(
    current: Optional[str],
    target: str,
) -> bool:
    """Whether an automated bump from ``current`` to ``target`` is allowed.

    Forward-only lifecycle semantics + manual parked-state protection. Pure
    function so it is trivially unit-testable.
    """
    target_rank = ACCOUNT_STATUS_LIFECYCLE_RANK.get(target)
    if target_rank is None:
        return False  # never automate toward a negative/manual status
    if current == target:
        return False
    if current in NEGATIVE_ACCOUNT_STATUSES:
        return target_rank > 1  # only strong signals revive a parked account
    current_rank = ACCOUNT_STATUS_LIFECYCLE_RANK.get(current or "cold", 0)
    return target_rank > current_rank


async def bump_company_account_status(
    session: AsyncSession,
    company_id,
    target: str,
) -> Optional[str]:
    """Advance ``Company.account_status`` to ``target`` if the lifecycle allows.

    No-op when the company is missing or the move would be a downgrade or an
    override of a parked state. Returns the new status when it changed, else
    None. Caller is responsible for committing.
    """
    company = await session.get(Company, company_id)
    if company is None:
        return None
    if not should_advance_account_status(company.account_status, target):
        return None
    company.account_status = target
    session.add(company)
    return target


async def apply_account_disable_effects(
    session: AsyncSession,
    company: Company,
    *,
    reason: str,
) -> dict:
    """Stop OUTBOUND for a freshly-disabled (not_a_fit/dnd) account.

    Pauses every Instantly campaign that exclusively serves this account's
    prospects (contact-level ``instantly_campaign_id``, legacy per-contact
    ``OutreachSequence`` campaigns, and the company-level campaign). A campaign
    shared with contacts of OTHER companies is skipped and logged — pausing it
    would silently stop a whole cohort's outreach, the same guard
    ``disposition_effects._maybe_pause_instantly_campaign`` applies per contact.

    Deliberately does NOT touch per-contact fields (``sequence_status``,
    ``next_followup_at``): those describe per-prospect truth and must survive a
    re-enable. Visibility and reminder gating are query-level. Re-enabling does
    NOT auto-resume paused campaigns — restarting outreach after a park is a
    deliberate rep decision, not a status flip side effect.

    Best-effort per campaign: an Instantly API failure is logged and skipped so
    the status update itself never fails. Caller commits.
    """
    from app.models.outreach import OutreachSequence

    contacts = (
        await session.execute(select(Contact).where(Contact.company_id == company.id))
    ).scalars().all()
    contact_ids = {c.id for c in contacts}

    campaign_ids: set[str] = {c.instantly_campaign_id for c in contacts if c.instantly_campaign_id}
    if company.instantly_campaign_id:
        campaign_ids.add(company.instantly_campaign_id)
    if contact_ids:
        seq_campaigns = (
            await session.execute(
                select(OutreachSequence.instantly_campaign_id).where(
                    OutreachSequence.contact_id.in_(contact_ids),
                    OutreachSequence.instantly_campaign_id.is_not(None),
                )
            )
        ).scalars().all()
        campaign_ids.update(seq_campaigns)

    paused: list[str] = []
    skipped_shared: list[str] = []
    for campaign_id in sorted(campaign_ids):
        outside = (
            await session.execute(
                select(Contact.id)
                .where(
                    Contact.instantly_campaign_id == campaign_id,
                    Contact.company_id.is_distinct_from(company.id),
                )
                .limit(1)
            )
        ).first()
        if outside is None:
            outside = (
                await session.execute(
                    select(OutreachSequence.id)
                    .join(Contact, OutreachSequence.contact_id == Contact.id)
                    .where(
                        OutreachSequence.instantly_campaign_id == campaign_id,
                        Contact.company_id.is_distinct_from(company.id),
                    )
                    .limit(1)
                )
            ).first()
        if outside is not None:
            skipped_shared.append(campaign_id)
            logger.info(
                "Account disable (%s): skipping Instantly campaign %s for company %s — "
                "shared with contacts outside the account",
                reason, campaign_id, company.id,
            )
            continue
        try:
            from app.clients.instantly import InstantlyClient

            client = InstantlyClient()
            await client.pause_campaign(campaign_id)
            paused.append(campaign_id)
            logger.info(
                "Account disable (%s): paused Instantly campaign %s for company %s",
                reason, campaign_id, company.id,
            )
        except Exception as exc:  # pragma: no cover — network/transient
            logger.warning(
                "Account disable (%s): failed to pause Instantly campaign %s for company %s: %s",
                reason, campaign_id, company.id, exc,
            )

    if paused:
        paused_set = set(paused)
        for contact in contacts:
            if contact.instantly_campaign_id in paused_set:
                contact.instantly_status = "paused"
                session.add(contact)

    return {
        "contacts": len(contacts),
        "campaigns_paused": len(paused),
        "campaigns_skipped_shared": len(skipped_shared),
    }
