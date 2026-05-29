from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.contact import ContactRead
from app.models.deal import Deal, DealContact


BLOCKED_SEQUENCE_STATUSES = {"bounced", "unsubscribed", "not_interested"}
LIVE_SEQUENCE_STATUSES = {"queued_instantly", "sent", "completed"}

_CHANNEL_LABEL: dict[str, str] = {
    "email": "Email",
    "call": "Call",
    "linkedin": "LinkedIn",
    "connector_request": "LinkedIn connect",
    "connector_follow_up": "LinkedIn follow-up",
}


def _extract_sequence_plan(contact) -> dict | None:
    """Return enrichment_data.sequence_plan if it contains valid steps, else None."""
    ed = contact.enrichment_data
    if not isinstance(ed, dict):
        return None
    plan = ed.get("sequence_plan")
    if not isinstance(plan, dict):
        return None
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    return plan


def _summarise_sequence_plan(plan: dict) -> str:
    """Return a human-readable one-liner describing the planned sequence steps."""
    steps = plan.get("steps", [])
    lane = (plan.get("lane") or "").replace("_", " ").title()
    parts: list[str] = []
    for step in steps[:5]:
        if not isinstance(step, dict):
            continue
        day = step.get("day_offset")
        ch = _CHANNEL_LABEL.get(step.get("channel", ""), step.get("channel", "touch"))
        if day is not None:
            parts.append(f"{ch} D{day}")
    if not parts:
        return "Sequence plan ready — awaiting launch."
    step_str = " → ".join(parts)
    prefix = f"{lane} plan: " if lane else "Planned: "
    return f"{prefix}{step_str}"


@dataclass
class ActivitySignal:
    created_at: datetime
    type: str
    source: str | None
    call_outcome: str | None
    content: str | None
    event_type: str | None


@dataclass
class DealSignal:
    deal_id: UUID
    deal_name: str
    stage: str
    health: str
    health_score: int | None
    updated_at: datetime


def _pretty(value: str | None) -> str:
    if not value:
        return "Unknown"
    return value.replace("_", " ").strip().title()


def _days_since(value: datetime | None) -> int | None:
    if not value:
        return None
    return max((datetime.utcnow() - value).days, 0)


def _activity_event_type(signal: ActivitySignal | None) -> str | None:
    if not signal:
        return None
    if signal.event_type:
        return signal.event_type
    content = (signal.content or "").lower()
    if "meeting booked" in content:
        return "lead_meeting_booked"
    if "marked as interested" in content:
        return "lead_interested"
    if "marked as not interested" in content:
        return "lead_not_interested"
    if "unsubscribed" in content:
        return "lead_unsubscribed"
    if "reply from" in content:
        return "reply_received"
    if "bounced" in content:
        return "email_bounced"
    if "link clicked" in content:
        return "email_link_clicked"
    if "opened" in content:
        return "email_opened"
    if "email sent" in content:
        return "email_sent"
    return None


def _score_label(stage: str, score: int) -> str:
    if stage == "Blocked":
        return "blocked"
    if score >= 75:
        return "good"
    if score >= 50:
        return "watch"
    return "at_risk"


def _deal_stage_score(stage: str) -> int:
    if stage == "closed_won":
        return 98
    if stage in {"commercial_negotiation", "proposal", "msa_review", "workshop"}:
        return 92
    if stage.startswith("poc"):
        return 88
    if stage.startswith("demo"):
        return 80
    if stage == "qualified_lead":
        return 76
    return 72


def compute_contact_tracking(
    contact: ContactRead,
    activity_signal: ActivitySignal | None = None,
    deal_signal: DealSignal | None = None,
) -> dict[str, object]:
    sequence_status = (contact.sequence_status or "").strip().lower()
    instantly_status = (contact.instantly_status or "").strip().lower()
    signal_type = _activity_event_type(activity_signal)
    stale_days = _days_since(activity_signal.created_at if activity_signal else None)

    if deal_signal:
        if deal_signal.stage == "closed_won":
            stage = "Customer"
            score = 98
            summary = f"{score}/100 Closed-won deal linked to this stakeholder."
        elif deal_signal.stage in {"closed_lost", "not_a_fit", "churned"}:
            stage = "Blocked"
            score = 18
            summary = f"{score}/100 Linked deal is no longer active, so this contact is blocked for now."
        else:
            score = deal_signal.health_score or _deal_stage_score(deal_signal.stage)
            stage = "Deal Active"
            summary = f"{score}/100 Active deal in {_pretty(deal_signal.stage)} with this stakeholder."
        return {
            "tracking_stage": stage,
            "tracking_summary": summary,
            "tracking_score": score,
            "tracking_label": _score_label(stage, score),
            "tracking_last_activity_at": activity_signal.created_at if activity_signal else deal_signal.updated_at,
        }

    if sequence_status == "meeting_booked" or signal_type == "lead_meeting_booked" or (activity_signal and activity_signal.type == "meeting"):
        stage = "Meeting Booked"
        score = 93
        summary = f"{score}/100 Meeting is booked, so this prospect is ready to move into deal work."
    elif sequence_status == "interested" or signal_type == "lead_interested":
        stage = "Interested"
        score = 85
        summary = f"{score}/100 Positive buyer signal captured, so this is worth fast follow-up."
    elif sequence_status == "replied" or signal_type == "reply_received":
        stage = "Engaged"
        score = 76
        summary = f"{score}/100 Reply received, so the next step should be a live conversation."
    elif activity_signal and activity_signal.type == "call" and activity_signal.call_outcome == "answered":
        stage = "Live Conversation"
        score = 74
        summary = f"{score}/100 Call connected recently, so momentum is building."
    elif sequence_status in BLOCKED_SEQUENCE_STATUSES or instantly_status == "bounced" or signal_type in {"email_bounced", "lead_unsubscribed", "lead_not_interested"}:
        stage = "Blocked"
        if sequence_status == "bounced" or instantly_status == "bounced" or signal_type == "email_bounced":
            score = 14
            summary = f"{score}/100 Email bounced, so contact data needs to be fixed before more outreach."
        elif sequence_status == "unsubscribed" or signal_type == "lead_unsubscribed":
            score = 20
            summary = f"{score}/100 Prospect unsubscribed, so this motion should stop."
        else:
            score = 24
            summary = f"{score}/100 Prospect signaled no interest, so this motion is blocked."
    elif sequence_status in LIVE_SEQUENCE_STATUSES or instantly_status == "pushed":
        if signal_type in {"email_opened", "email_link_clicked"}:
            stage = "Engaging"
            score = 64
            summary = f"{score}/100 Sequence is live and email engagement has started to appear."
        elif sequence_status == "completed":
            stage = "Sequence Complete"
            score = 40
            summary = f"{score}/100 Sequence finished without a strong conversion signal yet."
        else:
            stage = "In Sequence"
            score = 56
            summary = f"{score}/100 Sequence is running, but it is still waiting for a stronger buyer signal."
    elif sequence_status == "research_needed" or instantly_status == "missing_email" or (not contact.email and not contact.phone):
        stage = "Research Needed"
        score = 20
        summary = f"{score}/100 Missing contact data is blocking clean outreach."
    else:
        stage = "Ready"
        score = 52
        if contact.warm_intro_strength and contact.warm_intro_strength >= 3:
            score = 61
            summary = f"{score}/100 Contact is ready and has a usable warm path for outreach."
        elif contact.phone and not contact.email:
            summary = f"{score}/100 Contact is reachable by phone, but email coverage is still thin."
        else:
            summary = f"{score}/100 Contact data is usable and this prospect is ready for launch."

        # If a sequence plan exists and the contact is NOT yet launched, surface step timing
        # in the summary so the tracker shows what's planned rather than a generic message.
        if not contact.instantly_campaign_id:
            seq_plan = _extract_sequence_plan(contact)
            if seq_plan:
                stage = "Sequence Planned"
                plan_summary = _summarise_sequence_plan(seq_plan)
                summary = f"{score}/100 {plan_summary}"

    if stale_days is not None and stage in {"In Sequence", "Engaging", "Ready"}:
        if stale_days >= 14:
            score = max(score - 12, 18)
            summary = f"{score}/100 No fresh signal in {stale_days} days, so this motion is cooling off."
        elif stale_days >= 7:
            score = max(score - 6, 22)
            summary = f"{score}/100 Signals are real, but there has been no fresh movement in {stale_days} days."

    return {
        "tracking_stage": stage,
        "tracking_summary": summary,
        "tracking_score": score,
        "tracking_label": _score_label(stage, score),
        "tracking_last_activity_at": activity_signal.created_at if activity_signal else None,
    }


async def load_activity_signals(
    session: AsyncSession,
    contact_ids: Sequence[UUID],
) -> dict[UUID, ActivitySignal]:
    if not contact_ids:
        return {}

    latest_subquery = (
        select(
            Activity.contact_id.label("contact_id"),
            func.max(Activity.created_at).label("latest_created_at"),
        )
        .where(Activity.contact_id.in_(contact_ids))
        .group_by(Activity.contact_id)
        .subquery()
    )

    rows = (
        await session.execute(
            select(Activity)
            .join(
                latest_subquery,
                and_(
                    Activity.contact_id == latest_subquery.c.contact_id,
                    Activity.created_at == latest_subquery.c.latest_created_at,
                ),
            )
            .order_by(Activity.contact_id, Activity.created_at.desc())
        )
    ).scalars().all()

    result: dict[UUID, ActivitySignal] = {}
    for activity in rows:
        if not activity.contact_id or activity.contact_id in result:
            continue
        event_type = None
        if isinstance(activity.event_metadata, dict):
            event_type = activity.event_metadata.get("event_type") or activity.event_metadata.get("eventType")
        result[activity.contact_id] = ActivitySignal(
            created_at=activity.created_at,
            type=activity.type,
            source=activity.source,
            call_outcome=activity.call_outcome,
            content=activity.content,
            event_type=str(event_type) if event_type else None,
        )
    return result


async def load_deal_signals(
    session: AsyncSession,
    contact_ids: Sequence[UUID],
) -> dict[UUID, DealSignal]:
    if not contact_ids:
        return {}

    rows = (
        await session.execute(
            select(
                DealContact.contact_id,
                Deal.id,
                Deal.name,
                Deal.stage,
                Deal.health,
                Deal.health_score,
                Deal.updated_at,
            )
            .join(Deal, Deal.id == DealContact.deal_id)
            .where(DealContact.contact_id.in_(contact_ids))
            .order_by(DealContact.contact_id, Deal.updated_at.desc())
        )
    ).all()

    result: dict[UUID, DealSignal] = {}
    for contact_id, deal_id, deal_name, stage, health, health_score, updated_at in rows:
        if contact_id in result:
            continue
        result[contact_id] = DealSignal(
            deal_id=deal_id,
            deal_name=deal_name,
            stage=stage,
            health=health,
            health_score=health_score,
            updated_at=updated_at,
        )
    return result


async def apply_contact_tracking(
    session: AsyncSession,
    contacts: Sequence[ContactRead],
) -> list[ContactRead]:
    if not contacts:
        return list(contacts)

    contact_ids = [contact.id for contact in contacts if contact.id]
    activity_signals = await load_activity_signals(session, contact_ids)
    deal_signals = await load_deal_signals(session, contact_ids)

    for contact in contacts:
        tracking = compute_contact_tracking(
            contact,
            activity_signal=activity_signals.get(contact.id),
            deal_signal=deal_signals.get(contact.id),
        )
        for key, value in tracking.items():
            setattr(contact, key, value)

    return list(contacts)


async def to_contact_read(
    session: AsyncSession,
    contact,
    *,
    company_name: str | None = None,
) -> ContactRead:
    read = ContactRead.model_validate(contact)
    if company_name:
        read.company_name = company_name
    # Populate the call-attempt aggregate (count of type='call' activities) so
    # single-contact reads (GET/{id}, create, update, enrich) match the list
    # endpoint, which computes it via a correlated subquery. Without this the
    # single-contact response always reports 0 calls.
    if getattr(contact, "id", None) is not None:
        call_count = (
            await session.execute(
                select(func.count(Activity.id))
                .where(Activity.contact_id == contact.id)
                .where(Activity.type == "call")
            )
        ).scalar_one()
        read.call_attempt_count = int(call_count or 0)
    await apply_contact_tracking(session, [read])
    return read
