"""Unified timeline events across activities + meetings.

Returns a normalized event shape so the frontend can render one
chronological stream per contact or per deal without merging multiple
lists client-side.

kind values:
  email, call, meeting, linkedin, note, transcript, field_change,
  stage_change, sequence_event, comment, deal_created, contact_linked,
  import_note, other
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import or_, select

from app.models.activity import Activity
from app.models.contact import Contact
from app.models.deal import Deal, DealContact
from app.models.meeting import Meeting
from app.models.user import User

TimelineEvent = dict[str, Any]


def _activity_title(a: Activity) -> str:
    if a.type == "email" and a.email_subject:
        return f"Email · {a.email_subject}"
    if a.type == "call":
        if a.call_outcome:
            return f"Call · {a.call_outcome}"
        return "Call logged"
    if a.type == "linkedin":
        action = None
        if isinstance(a.event_metadata, dict):
            action = a.event_metadata.get("linkedin_action")
        if action:
            return f"LinkedIn · {str(action).replace('_', ' ')}"
        return "LinkedIn touch"
    if a.type == "transcript":
        return "Meeting transcript"
    if a.type == "stage_change":
        return "Deal stage changed"
    if a.type == "field_change":
        return "Field updated"
    if a.type == "deal_created":
        return "Deal created"
    if a.type == "contact_linked":
        return "Contact linked"
    if a.type == "note":
        return "Note"
    if a.type == "import_note":
        return "Imported note"
    if a.type == "comment":
        return "Comment"
    if a.type == "meeting":
        return "Meeting event"
    return a.type.replace("_", " ").title() if a.type else "Activity"


def _activity_subtitle(a: Activity) -> Optional[str]:
    if a.content:
        snippet = a.content.strip().splitlines()[0] if a.content.strip() else None
        if snippet and len(snippet) > 220:
            snippet = snippet[:217] + "…"
        return snippet
    if a.ai_summary:
        s = a.ai_summary.strip()
        return s[:217] + "…" if len(s) > 220 else s
    return None


def _activity_to_event(a: Activity) -> TimelineEvent:
    return {
        "id": f"activity:{a.id}",
        "kind": a.type or "other",
        "occurred_at": a.created_at.isoformat() if a.created_at else None,
        "title": _activity_title(a),
        "subtitle": _activity_subtitle(a),
        "actor_user_id": str(a.created_by_id) if a.created_by_id else None,
        "deal_id": str(a.deal_id) if a.deal_id else None,
        "contact_id": str(a.contact_id) if a.contact_id else None,
        "payload": {
            "source": a.source,
            "medium": a.medium,
            "ai_summary": a.ai_summary,
            "call_duration": a.call_duration,
            "call_outcome": a.call_outcome,
            "recording_url": a.recording_url,
            "email_subject": a.email_subject,
            "email_from": a.email_from,
            "email_to": a.email_to,
            "linkedin_action": (a.event_metadata or {}).get("linkedin_action")
            if isinstance(a.event_metadata, dict)
            else None,
        },
    }


def _meeting_title(m: Meeting) -> str:
    type_label = (m.meeting_type or "meeting").replace("_", " ")
    if m.status == "completed":
        return f"Meeting completed · {type_label}"
    if m.status == "cancelled":
        return f"Meeting cancelled · {type_label}"
    return f"Meeting scheduled · {type_label}"


def _meeting_subtitle(m: Meeting) -> Optional[str]:
    if m.ai_summary:
        s = m.ai_summary.strip()
        return s[:217] + "…" if len(s) > 220 else s
    return m.title or None


def _meeting_to_event(m: Meeting) -> TimelineEvent:
    # Use scheduled_at when present so the timeline shows when the meeting
    # actually happened, not when the row was inserted by sync.
    occurred = m.scheduled_at or m.created_at
    return {
        "id": f"meeting:{m.id}",
        "kind": "meeting",
        "occurred_at": occurred.isoformat() if occurred else None,
        "title": _meeting_title(m),
        "subtitle": _meeting_subtitle(m),
        "actor_user_id": str(m.owner_user_id) if m.owner_user_id else None,
        "deal_id": str(m.deal_id) if m.deal_id else None,
        "contact_id": None,
        "payload": {
            "meeting_id": str(m.id),
            "meeting_type": m.meeting_type,
            "status": m.status,
            "meeting_url": m.meeting_url,
            "recording_url": m.recording_url,
            "meeting_score": m.meeting_score,
            "external_source": m.external_source,
        },
    }


async def _attach_actor_names(session, events: list[TimelineEvent]) -> list[TimelineEvent]:
    """Resolve each event's actor_user_id to a display name so the timeline can
    show *who* logged an activity (e.g. "Logged by Sarthak Aitha") instead of a
    faceless "manually logged"."""
    ids: set[UUID] = set()
    for event in events:
        raw = event.get("actor_user_id")
        if raw:
            try:
                ids.add(UUID(str(raw)))
            except (ValueError, TypeError):
                pass
    name_map: dict[str, str] = {}
    if ids:
        rows = (await session.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()
        name_map = {str(uid): name for uid, name in rows}
    for event in events:
        actor = event.get("actor_user_id")
        event["actor_name"] = name_map.get(str(actor)) if actor else None
    return events


async def build_contact_timeline(
    session, contact_id: UUID, limit: int = 100
) -> list[TimelineEvent]:
    """Return chronological timeline (newest first) for a single contact.

    Includes the contact's activities and meetings tied to deals on which
    the contact is the primary contact_id.
    """
    activities_result = await session.execute(
        select(Activity)
        .where(Activity.contact_id == contact_id)
        .order_by(Activity.created_at.desc())
        .limit(limit)
    )
    activities = activities_result.scalars().all()

    # Meetings for any deal this contact is linked to (via the deal_contacts
    # junction — the Deal model has no direct contact_id column).
    deal_ids_result = await session.execute(
        select(DealContact.deal_id).where(DealContact.contact_id == contact_id)
    )
    deal_ids = [row[0] for row in deal_ids_result.all()]
    meetings: list[Meeting] = []
    if deal_ids:
        meetings_result = await session.execute(
            select(Meeting).where(Meeting.deal_id.in_(deal_ids))
        )
        meetings = list(meetings_result.scalars().all())

    events: list[TimelineEvent] = [_activity_to_event(a) for a in activities]
    events.extend(_meeting_to_event(m) for m in meetings)
    events.sort(key=lambda e: e["occurred_at"] or "", reverse=True)
    return await _attach_actor_names(session, events[:limit])


async def build_deal_timeline(
    session, deal_id: UUID, limit: int = 150
) -> list[TimelineEvent]:
    """Return chronological timeline (newest first) for a single deal."""
    activities_result = await session.execute(
        select(Activity)
        .where(Activity.deal_id == deal_id)
        .order_by(Activity.created_at.desc())
        .limit(limit)
    )
    activities = activities_result.scalars().all()

    meetings_result = await session.execute(
        select(Meeting).where(Meeting.deal_id == deal_id)
    )
    meetings = list(meetings_result.scalars().all())

    events: list[TimelineEvent] = [_activity_to_event(a) for a in activities]
    events.extend(_meeting_to_event(m) for m in meetings)
    events.sort(key=lambda e: e["occurred_at"] or "", reverse=True)
    return await _attach_actor_names(session, events[:limit])


async def build_company_timeline(
    session, company_id: UUID, limit: int = 200
) -> list[TimelineEvent]:
    """Account-level rollup: activities + meetings across ALL of a company's
    contacts and deals (newest first).

    This is what makes "how many emails went to this account today" answerable in
    one place — the per-contact/per-deal timelines fragment it across many rows.
    """
    contact_ids = [
        row[0]
        for row in (
            await session.execute(select(Contact.id).where(Contact.company_id == company_id))
        ).all()
    ]
    deal_ids = [
        row[0]
        for row in (
            await session.execute(select(Deal.id).where(Deal.company_id == company_id))
        ).all()
    ]

    conds = []
    if contact_ids:
        conds.append(Activity.contact_id.in_(contact_ids))
    if deal_ids:
        conds.append(Activity.deal_id.in_(deal_ids))

    activities = []
    if conds:
        activities = (
            await session.execute(
                select(Activity).where(or_(*conds)).order_by(Activity.created_at.desc()).limit(limit)
            )
        ).scalars().all()

    meetings: list[Meeting] = []
    if deal_ids:
        meetings = list(
            (await session.execute(select(Meeting).where(Meeting.deal_id.in_(deal_ids)))).scalars().all()
        )

    events: list[TimelineEvent] = [_activity_to_event(a) for a in activities]
    events.extend(_meeting_to_event(m) for m in meetings)
    events.sort(key=lambda e: e["occurred_at"] or "", reverse=True)
    return await _attach_actor_names(session, events[:limit])
