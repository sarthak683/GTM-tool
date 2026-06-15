from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from app.clients.claude import ClaudeClient
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.deal import DEAL_STAGES, Deal
from app.services.activity_signal_classifier import ActivitySignal, classify_activity_text

BlockerState = Literal["none", "negated", "active"]
CommercialState = Literal["none", "directional", "pricing_request", "pushback", "agreed"]
LegalState = Literal["none", "early_mention", "active_review"]
WorkshopState = Literal["none", "mentioned", "scheduling"]
RescheduleState = Literal["none", "single", "repeated"]


@dataclass(frozen=True)
class DealActivityInterpretation:
    stage_cue: str | None
    stage_confidence: float
    evidence_activity_id: str | None
    blocker_state: BlockerState
    commercial_state: CommercialState
    legal_state: LegalState
    workshop_state: WorkshopState
    reschedule_state: RescheduleState
    source: str = "heuristic"
    rationale: str | None = None


SYSTEM_PROMPT = """You are Beacon's deal-activity interpreter.
Your job is to read recent deal activity and extract structured commercial state, not to invent tasks.

Rules:
- Prefer "none" over guessing.
- Negation matters. "No issues on my end" means blocker_state=negated, not active.
- Distinguish POC prep from POC execution and POC completion.
- Sending a POC video, requirements sample, checklist, or prep material does NOT mean poc_done.
- A casual mention of legal/security during a POC does NOT automatically mean active legal review.
- A pricing mention can be directional without becoming a proposal review.
- Use the latest relevant evidence and cite the strongest evidence_activity_id when possible.
- Only set stage_cue when the recent activity clearly implies the deal has materially moved.
"""

USER_PROMPT_TEMPLATE = """Interpret this deal bundle and return exactly one structured tool call.

Current stage: {stage}
Allowed stages: {allowed_stages}

Return the best interpretation of the recent activity state.

Deal bundle:
{bundle_json}
"""

TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "stage_cue": {"type": ["string", "null"], "enum": [*DEAL_STAGES, None]},
        "stage_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_activity_id": {"type": ["string", "null"]},
        "blocker_state": {"type": "string", "enum": ["none", "negated", "active"]},
        "commercial_state": {"type": "string", "enum": ["none", "directional", "pricing_request", "pushback", "agreed"]},
        "legal_state": {"type": "string", "enum": ["none", "early_mention", "active_review"]},
        "workshop_state": {"type": "string", "enum": ["none", "mentioned", "scheduling"]},
        "reschedule_state": {"type": "string", "enum": ["none", "single", "repeated"]},
        "rationale": {"type": ["string", "null"]},
    },
    "required": [
        "stage_cue",
        "stage_confidence",
        "evidence_activity_id",
        "blocker_state",
        "commercial_state",
        "legal_state",
        "workshop_state",
        "reschedule_state",
        "rationale",
    ],
}


def _normalize(text: str | None) -> str:
    return " ".join((text or "").strip().lower().split())


def _activity_text(activity: Activity) -> str:
    metadata = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
    parts: list[str] = []
    for value in (
        activity.email_subject,
        activity.ai_summary,
        activity.content,
        metadata.get("thread_latest_message_text"),
        metadata.get("thread_context_excerpt"),
        metadata.get("google_doc_transcript"),
        metadata.get("summary"),
        metadata.get("text"),
    ):
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return _normalize("\n".join(parts))


def _build_bundle(deal: Deal, activities: list[Activity], contacts: list[Contact]) -> dict:
    recent_activities = []
    for activity in activities[:10]:
        text = _activity_text(activity)
        if not text:
            continue
        recent_activities.append(
            {
                "id": str(activity.id) if activity.id else None,
                "type": activity.type,
                "source": activity.source,
                "medium": activity.medium,
                "created_at": activity.created_at.isoformat() if activity.created_at else None,
                "email_from": activity.email_from,
                "text": text,
            }
        )
    return {
        "deal": {
            "id": str(deal.id),
            "name": deal.name,
            "stage": deal.stage,
            "value": float(deal.value) if deal.value is not None else None,
            "close_date_est": deal.close_date_est.isoformat() if deal.close_date_est else None,
            "days_in_stage": deal.days_in_stage,
            "next_step": deal.next_step,
        },
        "contacts": [
            {
                "id": str(contact.id),
                "email": contact.email,
                "name": f"{contact.first_name or ''} {contact.last_name or ''}".strip() or None,
                "title": contact.title,
                "persona_type": contact.persona_type,
            }
            for contact in contacts[:20]
        ],
        "recent_activities": recent_activities,
    }


def _heuristic_interpretation(deal: Deal, activities: list[Activity]) -> DealActivityInterpretation:
    stage_cue: str | None = None
    stage_confidence = 0.0
    evidence_activity_id: str | None = None
    blocker_state: BlockerState = "none"
    commercial_state: CommercialState = "none"
    legal_state: LegalState = "none"
    workshop_state: WorkshopState = "none"
    reschedule_count = 0

    for activity in activities:
        text = _activity_text(activity)
        if not text:
            continue
        signal: ActivitySignal = classify_activity_text(text)
        if signal.stage_cue and stage_cue is None:
            stage_cue = signal.stage_cue
            stage_confidence = 0.75
            evidence_activity_id = str(activity.id) if activity.id else None
        if signal.blocker == "negated" and blocker_state == "none":
            blocker_state = "negated"
        elif signal.blocker == "present":
            blocker_state = "active"
            if evidence_activity_id is None and activity.id:
                evidence_activity_id = str(activity.id)
        if signal.pricing_pushback:
            commercial_state = "pushback"
            if evidence_activity_id is None and activity.id:
                evidence_activity_id = str(activity.id)
        elif signal.pricing_request and commercial_state in {"none", "directional"}:
            commercial_state = "pricing_request"
            if evidence_activity_id is None and activity.id:
                evidence_activity_id = str(activity.id)
        elif signal.stage_cue == "commercial_negotiation" and commercial_state == "none":
            commercial_state = "directional"
        if signal.legal_review and legal_state == "none":
            legal_state = "active_review"
        if signal.workshop and workshop_state == "none":
            workshop_state = "mentioned"
        if signal.workshop and any(token in signal.normalized_text for token in ("schedule", "scheduled", "book", "set up", "calendar invite")):
            workshop_state = "scheduling"
        if signal.reschedule:
            reschedule_count += 1

    if stage_cue == "commercial_negotiation" and deal.stage not in {"poc_done", "commercial_negotiation", "msa_review", "workshop", "closed_won"}:
        stage_cue = None
        stage_confidence = 0.0

    return DealActivityInterpretation(
        stage_cue=stage_cue,
        stage_confidence=stage_confidence,
        evidence_activity_id=evidence_activity_id,
        blocker_state=blocker_state,
        commercial_state=commercial_state,
        legal_state=legal_state,
        workshop_state=workshop_state,
        reschedule_state="repeated" if reschedule_count >= 2 else "single" if reschedule_count == 1 else "none",
        source="heuristic",
        rationale="heuristic fallback",
    )


def _validated_interpretation(payload: dict, fallback: DealActivityInterpretation) -> DealActivityInterpretation:
    stage_cue = payload.get("stage_cue")
    if stage_cue not in DEAL_STAGES:
        stage_cue = None
    try:
        stage_confidence = float(payload.get("stage_confidence") or 0)
    except Exception:
        stage_confidence = 0.0
    if not 0 <= stage_confidence <= 1:
        stage_confidence = 0.0
    blocker_state = payload.get("blocker_state") if payload.get("blocker_state") in {"none", "negated", "active"} else fallback.blocker_state
    commercial_state = payload.get("commercial_state") if payload.get("commercial_state") in {"none", "directional", "pricing_request", "pushback", "agreed"} else fallback.commercial_state
    legal_state = payload.get("legal_state") if payload.get("legal_state") in {"none", "early_mention", "active_review"} else fallback.legal_state
    workshop_state = payload.get("workshop_state") if payload.get("workshop_state") in {"none", "mentioned", "scheduling"} else fallback.workshop_state
    reschedule_state = payload.get("reschedule_state") if payload.get("reschedule_state") in {"none", "single", "repeated"} else fallback.reschedule_state
    evidence_activity_id = str(payload.get("evidence_activity_id") or "").strip() or fallback.evidence_activity_id
    rationale = str(payload.get("rationale") or "").strip() or fallback.rationale

    return DealActivityInterpretation(
        stage_cue=stage_cue,
        stage_confidence=stage_confidence,
        evidence_activity_id=evidence_activity_id,
        blocker_state=blocker_state,
        commercial_state=commercial_state,
        legal_state=legal_state,
        workshop_state=workshop_state,
        reschedule_state=reschedule_state,
        source="ai",
        rationale=rationale,
    )


async def interpret_deal_activity(
    deal: Deal,
    activities: list[Activity],
    contacts: list[Contact],
) -> DealActivityInterpretation:
    fallback = _heuristic_interpretation(deal, activities)
    if not activities:
        return fallback

    client = ClaudeClient()
    if client.mock:
        return fallback

    bundle = _build_bundle(deal, activities, contacts)
    payload = await client.complete_structured(
        system=SYSTEM_PROMPT,
        user=USER_PROMPT_TEMPLATE.format(
            stage=deal.stage,
            allowed_stages=", ".join(DEAL_STAGES),
            bundle_json=json.dumps(bundle, default=str),
        ),
        tool_name="record_deal_activity_interpretation",
        tool_description="Record the structured state interpretation of the recent deal activity bundle.",
        input_schema=TOOL_SCHEMA,
        max_tokens=700,
    )
    if not isinstance(payload, dict):
        return fallback
    return _validated_interpretation(payload, fallback)
