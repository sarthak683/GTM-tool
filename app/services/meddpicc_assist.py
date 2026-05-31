from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.claude import ClaudeClient
from app.config import settings
from app.models.activity import Activity
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal, DealContact, MEDDPICC_FIELDS
from app.models.meeting import Meeting

MEDDPICC_CONFIDENCE = {"low", "medium", "high"}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return re.sub(r"\s+", " ", text)


def _truncate(text: str, limit: int) -> str:
    compact = _clean_text(text)
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}…"


def _extract_json_object(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(raw[start : end + 1])
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _flatten_metadata(value: Any) -> list[str]:
    parts: list[str] = []
    if isinstance(value, str):
        text = _clean_text(value)
        if text:
            parts.append(text)
        return parts
    if isinstance(value, (int, float, bool)):
        parts.append(str(value))
        return parts
    if isinstance(value, list):
        for item in value:
            parts.extend(_flatten_metadata(item))
        return parts
    if isinstance(value, dict):
        for item in value.values():
            parts.extend(_flatten_metadata(item))
    return parts


def _activity_signal(activity: Activity) -> str:
    parts: list[str] = []
    for candidate in [
        activity.type,
        activity.source,
        activity.medium,
        activity.call_outcome,
        activity.email_subject,
        activity.ai_summary,
        activity.content,
    ]:
        text = _clean_text(candidate)
        if text:
            parts.append(text)

    metadata = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
    for key in [
        "summary",
        "topics",
        "action_items",
        "items",
        "next_steps",
        "risks",
        "intents",
        "meeting_outcome",
        "stage_signal",
        "suggested_existing_contacts",
        "suggested_new_participants",
    ]:
        if key in metadata:
            parts.extend(_flatten_metadata(metadata.get(key)))

    return _truncate(" | ".join(part for part in parts if part), 500)


def _contains_any(text: str, needles: list[str]) -> bool:
    normalized = text.lower()
    return any(needle in normalized for needle in needles)


def _token_count(text: str, needles: list[str]) -> int:
    normalized = text.lower()
    return sum(1 for needle in needles if needle in normalized)


def _confidence_for(level: int) -> str:
    if level >= 3:
        return "high"
    if level == 2:
        return "medium"
    return "low"


def _dimension(level: int, reason: str, confidence: str | None = None) -> dict[str, Any]:
    safe_level = min(max(int(level), 0), 3)
    normalized_confidence = (confidence or _confidence_for(safe_level)).lower()
    if normalized_confidence not in MEDDPICC_CONFIDENCE:
        normalized_confidence = _confidence_for(safe_level)
    return {
        "level": safe_level,
        "reason": _truncate(reason, 160),
        "confidence": normalized_confidence,
    }


def _fallback_dimensions(
    *,
    deal: Deal,
    company: Company | None,
    contacts: list[dict[str, str | None]],
    activity_signals: list[str],
) -> dict[str, dict[str, Any]]:
    signal_text = " ".join(activity_signals).lower()
    deal_text = " ".join(
        part for part in [
            deal.name,
            company.name if company else None,
            deal.description,
            deal.next_step,
            deal.source,
            deal.stage,
        ]
        if part
    ).lower()
    full_text = f"{deal_text} {signal_text}".strip()

    champion_contacts = [c for c in contacts if (c.get("role") or c.get("persona")) == "champion"]
    buyer_contacts = [
        c
        for c in contacts
        if (c.get("role") or c.get("persona")) in {"economic_buyer", "buyer"}
    ]

    metrics_tokens = [
        "roi", "savings", "save", "revenue", "pipeline", "conversion", "time-to-value",
        "headcount", "hours", "days", "weeks", "months", "budget", "kpi", "metric", "%",
        "$", "cost", "payback",
    ]
    criteria_tokens = [
        "requirement", "criteria", "success metric", "integration", "security", "compliance",
        "workflow", "implementation", "timeline", "technical", "legal", "procurement",
    ]
    process_tokens = [
        "approval", "approver", "committee", "timeline", "decision", "next step", "procurement",
        "legal review", "security review", "review", "sign off", "workshop",
    ]
    paper_tokens = ["legal", "procurement", "security review", "msa", "dpa", "redline", "paper process", "contract"]
    pain_tokens = [
        "pain", "problem", "challenge", "manual", "slow", "delay", "blocked", "risk", "friction",
        "bottleneck", "need", "urgent", "stalled",
    ]
    competition_tokens = [
        "competitor", "competition", "alternative", "status quo", "guidecx", "rocketlane",
        "asana", "clickup", "monday", "notion",
    ]

    metrics_hits = _token_count(full_text, metrics_tokens)
    criteria_hits = _token_count(full_text, criteria_tokens)
    process_hits = _token_count(full_text, process_tokens)
    paper_hits = _token_count(full_text, paper_tokens)
    pain_hits = _token_count(full_text, pain_tokens)
    competition_hits = _token_count(full_text, competition_tokens)

    dimensions = {
        "metrics": _dimension(
            3 if metrics_hits >= 4 and re.search(r"(\$[\d,]+|[\d]+%|[\d]+ ?hours?|[\d]+ ?days?)", full_text) else 2 if metrics_hits >= 2 else 1 if metrics_hits >= 1 else 0,
            "Quantified impact signals appear in deal notes and recent conversations." if metrics_hits else "No clear quantified business impact is documented yet.",
        ),
        "economic_buyer": _dimension(
            3 if buyer_contacts and _contains_any(full_text, ["budget", "approval", "vp", "cfo", "economic buyer"]) else 2 if buyer_contacts else 1 if _contains_any(full_text, ["budget", "approval", "buyer"]) else 0,
            f"{buyer_contacts[0]['name']} is mapped as the likely budget owner." if buyer_contacts else "No clear budget owner is mapped on the deal yet.",
        ),
        "decision_criteria": _dimension(
            3 if criteria_hits >= 4 else 2 if criteria_hits >= 2 else 1 if criteria_hits == 1 else 0,
            "Requirements, technical criteria, or review criteria are showing up in the deal evidence." if criteria_hits else "The evidence does not yet show clear evaluation criteria.",
        ),
        "decision_process": _dimension(
            3 if process_hits >= 4 and deal.next_step else 2 if process_hits >= 2 or bool(deal.next_step) else 1 if process_hits == 1 else 0,
            "Next steps and approval flow appear in recent signals." if process_hits or deal.next_step else "The internal buying and approval process is still unclear.",
        ),
        "paper_process": _dimension(
            3 if paper_hits >= 3 else 2 if paper_hits >= 1 else 0,
            "Legal, procurement, or security process has surfaced in the thread." if paper_hits else "No clear legal or procurement process has appeared yet.",
        ),
        "identify_pain": _dimension(
            3 if pain_hits >= 4 else 2 if pain_hits >= 2 else 1 if pain_hits == 1 else 0,
            "Problem statements and urgency signals are present in recent activity." if pain_hits else "The business pain is not clearly captured in the deal evidence.",
        ),
        "champion": _dimension(
            3 if champion_contacts and len(activity_signals) >= 2 else 2 if champion_contacts else 1 if _contains_any(full_text, ["champion", "internal advocate"]) else 0,
            f"{champion_contacts[0]['name']} is mapped as an internal champion." if champion_contacts else "No clear internal champion is mapped yet.",
        ),
        "competition": _dimension(
            2 if competition_hits >= 2 else 1 if competition_hits == 1 else 0,
            "Competing options or status-quo pressure showed up in recent signals." if competition_hits else "No explicit competitive pressure is documented yet.",
        ),
    }

    return dimensions


async def _recommend_with_ai(
    *,
    deal: Deal,
    company: Company | None,
    contacts: list[dict[str, str | None]],
    activity_signals: list[str],
) -> dict[str, dict[str, Any]] | None:
    if not settings.claude_api_key:
        return None

    ai = ClaudeClient()
    if ai.mock:
        return None

    contact_lines = [
        f"- {contact['name']} | title={contact.get('title') or 'unknown'} | persona={contact.get('persona') or 'unknown'} | role={contact.get('role') or 'unknown'}"
        for contact in contacts[:10]
    ] or ["- None mapped yet"]
    activity_lines = [f"- {signal}" for signal in activity_signals[:14]] or ["- No recent activity"]

    system = (
        "You are Beacon CRM's MEDDPICC qualification assistant. "
        "Score each MEDDPICC dimension conservatively using only the evidence provided. "
        "Levels: 0=not_started, 1=identified, 2=validated, 3=confirmed. "
        "Return JSON only. Do not invent facts. If evidence is weak, use a lower score. "
        "For each dimension return: level, confidence, reason. "
        "Keep each reason to one short sentence."
    )
    user = (
        "Score these MEDDPICC fields: metrics, economic_buyer, decision_criteria, decision_process, "
        "paper_process, identify_pain, champion, competition.\n\n"
        f"Deal: {deal.name}\n"
        f"Company: {company.name if company else 'Unknown'}\n"
        f"Stage: {deal.stage}\n"
        f"Next step: {_clean_text(deal.next_step) or 'None'}\n"
        f"Description: {_truncate(_clean_text(deal.description), 400) or 'None'}\n\n"
        "Mapped contacts:\n"
        f"{chr(10).join(contact_lines)}\n\n"
        "Recent signals:\n"
        f"{chr(10).join(activity_lines)}\n\n"
        "Return JSON in this shape only:\n"
        "{"
        "\"metrics\":{\"level\":0,\"confidence\":\"low\",\"reason\":\"...\"},"
        "\"economic_buyer\":{\"level\":0,\"confidence\":\"low\",\"reason\":\"...\"},"
        "\"decision_criteria\":{\"level\":0,\"confidence\":\"low\",\"reason\":\"...\"},"
        "\"decision_process\":{\"level\":0,\"confidence\":\"low\",\"reason\":\"...\"},"
        "\"paper_process\":{\"level\":0,\"confidence\":\"low\",\"reason\":\"...\"},"
        "\"identify_pain\":{\"level\":0,\"confidence\":\"low\",\"reason\":\"...\"},"
        "\"champion\":{\"level\":0,\"confidence\":\"low\",\"reason\":\"...\"},"
        "\"competition\":{\"level\":0,\"confidence\":\"low\",\"reason\":\"...\"}"
        "}"
    )

    raw = await ai.complete(system, user, max_tokens=650)
    payload = _extract_json_object(raw)
    if not payload:
        return None

    normalized: dict[str, dict[str, Any]] = {}
    for field in MEDDPICC_FIELDS:
        item = payload.get(field)
        if not isinstance(item, dict):
            continue
        normalized[field] = _dimension(
            item.get("level", 0),
            _clean_text(item.get("reason")) or "Beacon found limited supporting evidence for this dimension.",
            _clean_text(item.get("confidence")).lower() or None,
        )
    return normalized or None


async def generate_meddpicc_assist(session: AsyncSession, deal: Deal) -> dict[str, Any]:
    company = await session.get(Company, deal.company_id) if deal.company_id else None

    contact_rows = (
        await session.execute(
            select(DealContact, Contact)
            .join(Contact, DealContact.contact_id == Contact.id)
            .where(DealContact.deal_id == deal.id)
            .order_by(DealContact.added_at.desc())
        )
    ).all()
    contacts = [
        {
            "id": str(contact.id) if contact.id else None,
            "name": _clean_text(f"{contact.first_name or ''} {contact.last_name or ''}") or contact.email or "Unnamed contact",
            "title": contact.title,
            "persona": contact.persona or contact.persona_type,
            "role": dc.role,
        }
        for dc, contact in contact_rows
    ]

    activity_filters = [Activity.deal_id == deal.id]
    if deal.company_id:
        tldv_ext_ids = (
            select(("tldv:meeting:" + Meeting.external_source_id))
            .where(
                Meeting.company_id == deal.company_id,
                Meeting.external_source_id.is_not(None),
            )
        )
        tldv_transcript_ids = (
            select(("tldv:transcript:" + Meeting.external_source_id))
            .where(
                Meeting.company_id == deal.company_id,
                Meeting.external_source_id.is_not(None),
            )
        )
        activity_filters.append(Activity.external_source_id.in_(tldv_ext_ids))
        activity_filters.append(Activity.external_source_id.in_(tldv_transcript_ids))

    activity_rows = (
        await session.execute(
            select(Activity)
            .where(or_(*activity_filters))
            .order_by(Activity.created_at.desc())
            .limit(18)
        )
    ).scalars().all()
    activity_signals = [_activity_signal(activity) for activity in activity_rows]
    activity_signals = [signal for signal in activity_signals if signal]

    ai_dimensions = await _recommend_with_ai(
        deal=deal,
        company=company,
        contacts=contacts,
        activity_signals=activity_signals,
    )
    fallback_dimensions = _fallback_dimensions(
        deal=deal,
        company=company,
        contacts=contacts,
        activity_signals=activity_signals,
    )

    dimensions: dict[str, dict[str, Any]] = {}
    for field in MEDDPICC_FIELDS:
        dimensions[field] = ai_dimensions.get(field) if ai_dimensions and ai_dimensions.get(field) else fallback_dimensions[field]

    return {
        "meddpicc": {field: dimensions[field]["level"] for field in MEDDPICC_FIELDS},
        "meddpicc_ai": {
            "generated_at": datetime.utcnow().isoformat(),
            "generator": "beacon_ai",
            "dimensions": dimensions,
            "signals_used": {
                "contacts": len(contacts),
                "activities": len(activity_signals),
            },
        },
    }


def _meddpicc_has_signal(qualification: Any) -> bool:
    current = (qualification or {}).get("meddpicc") if isinstance(qualification, dict) else None
    if not isinstance(current, dict):
        return False
    return any(isinstance(current.get(f), (int, float)) and current.get(f, 0) > 0 for f in MEDDPICC_FIELDS)


async def auto_fill_empty_meddpicc(session: AsyncSession, deal: Deal) -> int:
    """Populate MEDDPICC for a deal that has NONE, straight from its transcript/
    activity evidence — reps were dismissing the incremental suggestions, so
    MEDDPICC stayed empty even on transcript-rich deals.

    Only acts when the deal currently has zero MEDDPICC signal AND has ≥1 linked
    contact (so the AI has people to ground the qualification). Fills only empty
    dimensions — never overwrites a rep-set value — and no-ops if generation
    yields nothing. Returns the count of dimensions filled.
    """
    if _meddpicc_has_signal(deal.qualification):
        return 0  # rep/auto-apply already owns it — don't touch
    has_contact = (
        await session.execute(select(DealContact.contact_id).where(DealContact.deal_id == deal.id))
    ).first()
    if has_contact is None:
        return 0

    assist = await generate_meddpicc_assist(session, deal)
    generated = assist.get("meddpicc") or {}
    qual = dict(deal.qualification or {})
    merged = dict(qual.get("meddpicc") or {})
    filled = 0
    for field in MEDDPICC_FIELDS:
        level = generated.get(field)
        already = isinstance(merged.get(field), (int, float)) and merged.get(field, 0) > 0
        if isinstance(level, (int, float)) and level > 0 and not already:
            merged[field] = level
            filled += 1
    if not filled:
        return 0

    qual["meddpicc"] = merged
    qual["meddpicc_ai"] = assist.get("meddpicc_ai")
    deal.qualification = qual
    deal.updated_at = datetime.utcnow()
    session.add(deal)
    session.add(
        Activity(
            deal_id=deal.id,
            type="qualification_update",
            source="beacon_ai",
            content=f"Beacon auto-filled MEDDPICC from deal evidence ({filled} dimension{'s' if filled != 1 else ''}).",
        )
    )
    return filled

