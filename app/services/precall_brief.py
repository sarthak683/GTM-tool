"""
Pre-call brief assembly.

Package everything a rep needs to make a cold / warm call in one payload:
  - Contact + Company facts
  - The last email the rep sent (subject, snippet, whether it was opened/clicked)
  - The last 3 activities on this contact (any channel)
  - Up to 5 recent company buying signals (funding, hiring, PR)
  - Talking points (from account sourcing enrichment, if populated)
  - Objection handling playbook (persona-aware, from battlecards if any)
  - Conversation starter + personalization notes (existing Contact fields)
  - The AI-generated sequence's email_1 body (so the rep sees what the prospect
    received) plus linkedin_message

The brief is assembled from DB state only — no external calls, no AI, no
network. That keeps it instant (< 300ms) so the rep can tap the call button
and have a full brief before the first ring.

If the rep wants a sharper, persona-tuned summary on top of this, they can
hit a separate `/precall-brief/ai` endpoint that runs Claude against the
assembled payload (kept optional so call flow isn't blocked on AI).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.company import Company
from app.models.contact import Contact
from app.models.outreach import OutreachSequence
from app.models.signal import Signal


def _shorten(text: Optional[str], limit: int) -> Optional[str]:
    if not text:
        return None
    t = text.strip()
    if len(t) <= limit:
        return t
    return t[: max(limit - 1, 0)].rstrip() + "…"


def _persona_talking_points(contact: Contact, company: Optional[Company]) -> list[str]:
    """Fallback talking points when `contact.talking_points` is empty.

    These are deliberately concrete and persona-specific so the rep has
    something usable *before* any AI enrichment runs. Not meant to replace
    the AI-generated set — just to cover contacts whose sourcing never
    populated `talking_points`.
    """
    persona = (contact.persona or contact.persona_type or "unknown").lower()
    company_name = company.name if company else "your team"
    if persona in {"economic_buyer", "buyer"}:
        return [
            f"Why now for {company_name} — what's driving this quarter's priorities?",
            "Sizing the problem: how many teams / how much manual effort today?",
            "What would a successful outcome look like 90 days in?",
        ]
    if persona == "champion":
        return [
            "Who else on your side needs to see this to move forward?",
            "How are peer teams at similar companies handling this?",
            "What would it take to get you to advocate internally?",
        ]
    if persona in {"technical_evaluator", "evaluator"}:
        return [
            "What does your current stack / integration look like?",
            "What's your non-negotiable architecture or security constraint?",
            "Do you have a POC process, and what success criteria matter?",
        ]
    return [
        "What triggered looking at this now?",
        "Who else is involved in the decision?",
        "What would make this a clear win for the team?",
    ]


def _persona_objection_playbook(persona: str) -> list[dict[str, str]]:
    persona = (persona or "unknown").lower()
    common = [
        {
            "objection": "We're not looking right now / bad timing",
            "response": (
                "Totally fair — a lot of our customers said the same 3 months before they "
                "started. Worth 15 min to map the ROI so when priorities shift you already "
                "have a reference point?"
            ),
        },
        {
            "objection": "We already use [competitor]",
            "response": (
                "Great — that usually means the basics are covered. Where we see most wins is "
                "in the gap around [multichannel orchestration / AI copy / reply sentiment]. "
                "Worth a 20-min compare?"
            ),
        },
    ]
    if persona in {"economic_buyer", "buyer"}:
        return common + [
            {
                "objection": "Send me information",
                "response": (
                    "Happy to, but a 15-min call tells me what matters to you so I don't send "
                    "a generic deck. What's the best time this week?"
                ),
            },
        ]
    if persona in {"technical_evaluator", "evaluator"}:
        return common + [
            {
                "objection": "We built this in-house",
                "response": (
                    "Makes sense — the question is usually maintenance cost vs. focus. Most "
                    "teams we talk to kept the custom layer and replaced the commodity pieces. "
                    "Want to see how?"
                ),
            },
        ]
    return common


def _email_opened_from_metadata(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    event = str(metadata.get("event_type") or "").lower()
    return event in {"email_opened", "email_link_clicked"}


async def _load_last_email_to_prospect(
    session: AsyncSession, contact_id: UUID, email: Optional[str]
) -> Optional[dict[str, Any]]:
    """Return the most recent outbound email activity targeting this contact."""
    stmt = (
        select(Activity)
        .where(
            Activity.contact_id == contact_id,
            or_(Activity.type == "email", Activity.source == "instantly"),
        )
        .order_by(Activity.created_at.desc())
        .limit(10)
    )
    rows = (await session.execute(stmt)).scalars().all()

    outbound: Optional[Activity] = None
    for row in rows:
        meta = row.event_metadata if isinstance(row.event_metadata, dict) else {}
        event_type = str(meta.get("event_type") or "").lower()
        # "email_sent" is our outbound ground truth; skip opens/clicks/replies.
        if event_type and event_type != "email_sent":
            continue
        outbound = row
        break

    if not outbound:
        return None

    meta = outbound.event_metadata if isinstance(outbound.event_metadata, dict) else {}
    subject = outbound.email_subject or meta.get("subject") or "(no subject)"
    body = outbound.content or meta.get("body") or ""

    # Check if any later activity on this contact indicates the email was
    # opened or clicked — gives the rep the "is it warm?" signal instantly.
    opened = False
    clicked = False
    for row in rows:
        if row.created_at <= outbound.created_at:
            continue
        meta_later = row.event_metadata if isinstance(row.event_metadata, dict) else {}
        event_type_later = str(meta_later.get("event_type") or "").lower()
        if event_type_later == "email_opened":
            opened = True
        if event_type_later == "email_link_clicked":
            clicked = True
            opened = True

    return {
        "subject": subject,
        "sent_at": outbound.created_at.isoformat(),
        "snippet": _shorten(body, 400),
        "opened": opened,
        "clicked": clicked,
    }


async def _load_recent_activities(
    session: AsyncSession, contact_id: UUID, limit: int = 3
) -> list[dict[str, Any]]:
    stmt = (
        select(Activity)
        .where(Activity.contact_id == contact_id)
        .order_by(Activity.created_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "type": row.type,
            "medium": row.medium,
            "source": row.source,
            "content": _shorten(row.content, 200),
            "ai_summary": _shorten(row.ai_summary, 200),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


async def _load_recent_signals(
    session: AsyncSession, company_id: Optional[UUID], limit: int = 5
) -> list[dict[str, Any]]:
    if not company_id:
        return []
    cutoff = datetime.utcnow() - timedelta(days=120)
    stmt = (
        select(Signal)
        .where(Signal.company_id == company_id)
        .order_by(Signal.created_at.desc())
        .limit(limit * 2)
    )
    rows = (await session.execute(stmt)).scalars().all()
    recent = [r for r in rows if r.created_at >= cutoff][:limit]
    if not recent:
        recent = rows[:limit]
    return [
        {
            "type": r.signal_type,
            "title": r.title,
            "summary": _shorten(r.summary, 180),
            "url": r.url,
            "published_at": r.published_at.isoformat() if r.published_at else None,
        }
        for r in recent
    ]


async def build_precall_brief(
    session: AsyncSession,
    contact_id: UUID,
) -> dict[str, Any]:
    contact = await session.get(Contact, contact_id)
    if not contact:
        return {"error": "Contact not found"}

    company = (
        await session.get(Company, contact.company_id) if contact.company_id else None
    )

    last_email = await _load_last_email_to_prospect(
        session, contact_id, contact.email
    )
    recent_activities = await _load_recent_activities(session, contact_id)
    recent_signals = await _load_recent_signals(
        session, contact.company_id, limit=5
    )

    # Talking points: prefer pre-populated, fall back to persona-generic so
    # the rep never sees an empty section.
    talking_points: list[str] = []
    raw_tp = contact.talking_points
    if isinstance(raw_tp, list):
        talking_points = [str(x).strip() for x in raw_tp if str(x).strip()]
    elif isinstance(raw_tp, dict):
        # Some rows store {"points": [...]} shape
        tp = raw_tp.get("points")
        if isinstance(tp, list):
            talking_points = [str(x).strip() for x in tp if str(x).strip()]
    if not talking_points:
        talking_points = _persona_talking_points(contact, company)

    # Sequence context (what we sent / will send via email + LinkedIn) — so the
    # rep can reference the email they already sent without tabbing away.
    sequence = (
        await session.execute(
            select(OutreachSequence).where(OutreachSequence.contact_id == contact_id)
        )
    ).scalars().first()
    sequence_context: Optional[dict[str, Any]] = None
    if sequence:
        sequence_context = {
            "id": str(sequence.id),
            "status": sequence.status,
            "subject_1": sequence.subject_1,
            "email_1_snippet": _shorten(sequence.email_1, 400),
            "linkedin_message": sequence.linkedin_message,
            "instantly_campaign_status": sequence.instantly_campaign_status,
        }

    objection_playbook = _persona_objection_playbook(
        contact.persona or contact.persona_type or "unknown"
    )

    return {
        "contact": {
            "id": str(contact.id),
            "name": f"{contact.first_name or ''} {contact.last_name or ''}".strip(),
            "title": contact.title,
            "email": contact.email,
            "phone": contact.phone,
            "linkedin_url": contact.linkedin_url,
            "persona": contact.persona,
            "persona_type": contact.persona_type,
            "timezone": contact.timezone,
            "sequence_status": contact.sequence_status,
            "call_status": contact.call_status,
            "call_disposition": contact.call_disposition,
            "linkedin_status": contact.linkedin_status,
        },
        "company": {
            "id": str(company.id) if company else None,
            "name": company.name if company else None,
            "domain": company.domain if company else None,
            "industry": company.industry if company else None,
            "employees": company.employee_count if company else None,
        }
        if company
        else None,
        "conversation_starter": contact.conversation_starter,
        "personalization_notes": contact.personalization_notes,
        "talking_points": talking_points,
        "objection_playbook": objection_playbook,
        "last_email_sent": last_email,
        "recent_activities": recent_activities,
        "recent_signals": recent_signals,
        "sequence": sequence_context,
    }
