from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Callable, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.activity import Activity
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import DEAL_STAGES, Deal, DealContact
from app.models.meeting import Meeting
from app.models.task import Task
from app.models.user import User
from app.repositories.deal import DealRepository
from app.services.account_sourcing import append_company_activity_log
from app.services.activity_signal_classifier import ActivitySignal, classify_activity_text
from app.services.ai_task_emitter import TaskProposal, emit_ai_tasks
from app.services.company_stage_milestones import record_deal_stage_milestone
from app.services.critical_task_rules import CriticalFinding, evaluate_critical_rules
from app.services.deal_activity_interpreter import DealActivityInterpretation, interpret_deal_activity
from app.services.deal_stage_playbook import stage_allows_stage_move, stage_allows_system_key
from app.services.deal_health import compute_health
from app.services.meddpicc_updates import field_has_capture, get_meddpicc_details
from app.services.system_task_actions import get_system_task_action_spec
from app.services.task_codes import CODE_TO_ACTION, track_for_code

logger = logging.getLogger(__name__)

STAGE_INDEX = {stage: idx for idx, stage in enumerate(DEAL_STAGES)}
AI_TASK_REFRESH_TTL = timedelta(minutes=10)
AI_TASK_REFRESH_DEBOUNCE = timedelta(seconds=45)
AI_TASK_SIGNAL_WINDOW = 25
AI_TASK_POLICY_VERSION = "2026-04-21-contextual-v2"
SYSTEM_TASK_DISMISSAL_SNOOZE = timedelta(days=14)
STAGE_OWNER_MATRIX: dict[str, tuple[str, str, str]] = {
    "reprospect": ("SDR", "AE shadow; Marketing for trigger content", "sdr"),
    "demo_scheduled": ("AE", "SDR for rescheduling; Rakesh for strategic accounts", "ae"),
    "demo_done": ("AE", "Rakesh / Product on unanswered questions", "ae"),
    "qualified_lead": ("AE", "Rakesh for deep tech / strategic; SE for architecture", "ae"),
    "poc_agreed": ("AE", "SE for environment; Legal for NDA only", "ae"),
    "poc_wip": ("AE", "Product on blockers; Rakesh on scope issues", "ae"),
    "poc_done": ("AE", "Rakesh for commercials framing", "ae"),
    "commercial_negotiation": ("AE", "Finance on terms; Legal on custom clauses", "ae"),
    "msa_review": ("AE", "Rakesh for stalled redlines; Delivery for workshop", "ae"),
    "workshop": ("AE", "Rakesh for stalled redlines; Delivery for workshop", "ae"),
    "closed_won": ("AE -> Delivery", "Finance for invoice; CS for onboarding", "ae"),
    "churned": ("AE + CS", "Rakesh for exit learnings", "ae"),
    "not_a_fit": ("AE", "Marketing if later triggers appear", "ae"),
    "cold": ("SDR", "AE on trigger event", "sdr"),
    "closed_lost": ("AE", "Rakesh + Product for win-loss", "ae"),
    "on_hold": ("AE (light touch)", "Marketing for nurture content", "ae"),
    "nurture": ("Marketing", "AE on inbound reply", "ae"),
}


def _normalize(text: str | None) -> str:
    return (text or "").strip().lower()


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _has_blocker_signal(texts: list[str]) -> bool:
    return any(classify_activity_text(text).blocker == "present" for text in texts if text)


def _stable_json_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def compute_deal_task_input_hash(session: AsyncSession, deal: Deal) -> str:
    activities = (
        await session.execute(
            select(Activity)
            .where(Activity.deal_id == deal.id)
            .order_by(Activity.created_at.desc())
            .limit(AI_TASK_SIGNAL_WINDOW)
        )
    ).scalars().all()
    contacts = (
        await session.execute(
            select(Contact)
            .join(DealContact, DealContact.contact_id == Contact.id)
            .where(DealContact.deal_id == deal.id)
        )
    ).scalars().all()

    payload = {
        "policy_version": AI_TASK_POLICY_VERSION,
        "deal": {
            "id": str(deal.id),
            "stage": deal.stage,
            "pipeline_type": deal.pipeline_type,
            "value": str(deal.value) if deal.value is not None else None,
            "close_date_est": deal.close_date_est.isoformat() if deal.close_date_est else None,
            "assigned_to_id": str(deal.assigned_to_id) if deal.assigned_to_id else None,
            "owner_id": deal.owner_id,
            "commit_to_deal": deal.commit_to_deal,
            "qualification": deal.qualification or {},
            "next_step": deal.next_step,
            "days_in_stage": deal.days_in_stage,
            "stage_entered_at": deal.stage_entered_at.isoformat() if deal.stage_entered_at else None,
            "last_activity_at": deal.last_activity_at.isoformat() if deal.last_activity_at else None,
        },
        "activities": [
            {
                "id": str(activity.id),
                "type": activity.type,
                "source": activity.source,
                "medium": activity.medium,
                "content": activity.content,
                "ai_summary": activity.ai_summary,
                "contact_id": str(activity.contact_id) if activity.contact_id else None,
                "created_at": activity.created_at.isoformat() if activity.created_at else None,
                "email_subject": activity.email_subject,
                "email_from": activity.email_from,
                "email_to": activity.email_to,
                "email_cc": activity.email_cc,
                "call_outcome": activity.call_outcome,
                "event_metadata": activity.event_metadata,
            }
            for activity in activities
        ],
        "contacts": [
            {
                "id": str(contact.id),
                "email": contact.email,
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "title": contact.title,
                "persona": contact.persona,
                "persona_type": contact.persona_type,
                "assigned_to_id": str(contact.assigned_to_id) if contact.assigned_to_id else None,
                "updated_at": contact.updated_at.isoformat() if contact.updated_at else None,
            }
            for contact in sorted(contacts, key=lambda item: str(item.id))
        ],
    }
    return _stable_json_hash(payload)


def should_queue_deal_task_refresh(
    deal: Deal,
    *,
    input_hash: str,
    force: bool = False,
    now: datetime | None = None,
) -> bool:
    current_time = now or datetime.utcnow()
    if force:
        return True
    if not deal.ai_tasks_refreshed_at or not deal.ai_tasks_input_hash:
        return True
    if deal.ai_tasks_input_hash != input_hash:
        pending_since = deal.ai_tasks_refresh_requested_at
        if pending_since and pending_since > deal.ai_tasks_refreshed_at and pending_since >= current_time - AI_TASK_REFRESH_DEBOUNCE:
            return False
        return True
    if deal.ai_tasks_refreshed_at <= current_time - AI_TASK_REFRESH_TTL:
        pending_since = deal.ai_tasks_refresh_requested_at
        if pending_since and pending_since >= current_time - AI_TASK_REFRESH_DEBOUNCE:
            return False
        return True
    return False


def mark_deal_task_refresh_requested(
    deal: Deal,
    *,
    requested_at: datetime | None = None,
) -> None:
    deal.ai_tasks_refresh_requested_at = requested_at or datetime.utcnow()


def mark_deal_task_refresh_completed(
    deal: Deal,
    *,
    input_hash: str,
    refreshed_at: datetime | None = None,
) -> None:
    completed_at = refreshed_at or datetime.utcnow()
    deal.ai_tasks_input_hash = input_hash
    deal.ai_tasks_refreshed_at = completed_at
    deal.ai_tasks_refresh_requested_at = completed_at


def _stage_allows_pricing_package(stage: str | None) -> bool:
    return bool(stage and _stage_reached(stage, "poc_done"))


def _stage_allows_workshop_booking(stage: str | None) -> bool:
    return bool(stage and _stage_reached(stage, "commercial_negotiation"))


def _priority_label_for_task(
    *,
    system_key: str | None,
    recommended_action: str | None,
    task_track: str | None,
) -> str:
    key = (system_key or "").lower()
    action = (recommended_action or "").lower()
    track = (task_track or "").lower()
    if track == "critical" or action == "t_critical_apply":
        return "P0"
    if action == "t_stage_apply" or action == "move_deal_stage":
        return "P0"
    if any(token in key for token in ("nda", "reschedule", "blocker", "stuck", "redline", "invoice")):
        return "P0"
    if action in {"t_amount_apply", "t_close_apply", "t_medpicc_apply", "t_contact_apply"}:
        return "P1"
    if any(
        token in key
        for token in (
            "roi",
            "proposal",
            "stakeholder",
            "kickoff",
            "readout",
            "results",
            "setup",
            "security",
            "procurement",
            "terms",
            "workshop",
            "handoff",
            "commercial",
            "redline",
        )
    ):
        return "P1"
    return "P2"


def _default_due_at_for_priority(label: str, now: datetime) -> datetime:
    if label == "P0":
        return now + timedelta(hours=8)
    if label == "P1":
        return now + timedelta(hours=36)
    return now + timedelta(days=5)


def _sla_label_for_priority(label: str) -> str:
    if label == "P0":
        return "Same day"
    if label == "P1":
        return "24-48h"
    return "3-7 days"


def _owner_hint_for_task(
    *,
    stage: str | None,
    system_key: str | None,
    assigned_role: str | None,
) -> tuple[str | None, str | None, str | None]:
    key = (system_key or "").lower()
    owner_hint: str | None = None
    escalation_hint: str | None = None
    schema_role = assigned_role

    if stage and stage in STAGE_OWNER_MATRIX:
        owner_hint, escalation_hint, default_role = STAGE_OWNER_MATRIX[stage]
        if schema_role is None:
            schema_role = default_role

    if "invoice" in key:
        owner_hint = "Finance + AE"
    elif "handoff" in key or "kickoff" in key and stage == "closed_won":
        owner_hint = "AE -> Delivery"
    elif "nda" in key:
        owner_hint = "AE"
    elif "redline" in key or "legal" in key:
        owner_hint = "AE"
    elif "blocker" in key:
        owner_hint = "AE + SE"

    return schema_role, owner_hint, escalation_hint


async def _find_open_system_task(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: UUID,
    system_key: str,
) -> Task | None:
    result = await session.execute(
        select(Task).where(
            Task.entity_type == entity_type,
            Task.entity_id == entity_id,
            Task.system_key == system_key,
            Task.task_type == "system",
            Task.status == "open",
        )
        # Concurrent refreshers can race in duplicate open rows (no unique
        # constraint); pick the oldest deterministically instead of raising
        # MultipleResultsFound and 500ing every task refresh thereafter.
        .order_by(Task.created_at.asc())
        .limit(1)
    )
    return result.scalars().first()


async def _recent_system_task_exists(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: UUID,
    system_key: str,
    days: int,
) -> bool:
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await session.execute(
        select(Task.id).where(
            Task.entity_type == entity_type,
            Task.entity_id == entity_id,
            Task.system_key == system_key,
            Task.task_type == "system",
            Task.created_at >= cutoff,
        )
    )
    return result.first() is not None


async def _recently_dismissed_system_task_exists(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: UUID,
    system_key: str,
) -> bool:
    cutoff = datetime.utcnow() - SYSTEM_TASK_DISMISSAL_SNOOZE
    result = await session.execute(
        select(Task.id).where(
            Task.entity_type == entity_type,
            Task.entity_id == entity_id,
            Task.system_key == system_key,
            Task.task_type == "system",
            Task.status == "dismissed",
            Task.updated_at >= cutoff,
        )
    )
    return result.first() is not None


async def _resolve_task_assignee(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: UUID,
    preferred_role: str | None = None,
) -> tuple[UUID | None, str | None]:
    user_id: UUID | None = None
    if entity_type == "company":
        company = await session.get(Company, entity_id)
        if company:
            user_id = company.assigned_to_id or company.sdr_id
    elif entity_type == "contact":
        contact = await session.get(Contact, entity_id)
        if contact:
            user_id = contact.assigned_to_id or contact.sdr_id
    elif entity_type == "deal":
        deal = await session.get(Deal, entity_id)
        if deal:
            user_id = deal.assigned_to_id

    if not user_id:
        return None, None

    user = await session.get(User, user_id)
    if not user or not user.is_active:
        return None, None
    return user.id, user.role


async def _upsert_system_task(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: UUID,
    system_key: str,
    title: str,
    description: str,
    priority: str,
    source: str,
    recommended_action: str | None,
    action_payload: dict | None = None,
    assigned_role: str | None = None,
    task_track: str = "hygiene",
    due_at: datetime | None = None,
) -> Task | None:
    deal_stage: str | None = None
    if entity_type == "deal":
        deal = await session.get(Deal, entity_id)
        deal_stage = deal.stage if deal else None

    priority_label = _priority_label_for_task(
        system_key=system_key,
        recommended_action=recommended_action,
        task_track=task_track,
    )
    effective_due_at = due_at or _default_due_at_for_priority(priority_label, datetime.utcnow())
    resolved_assigned_role, owner_hint, escalation_hint = _owner_hint_for_task(
        stage=deal_stage,
        system_key=system_key,
        assigned_role=assigned_role,
    )

    assigned_to_id, resolved_role = await _resolve_task_assignee(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        preferred_role=resolved_assigned_role,
    )
    effective_payload = dict(action_payload or {})
    effective_payload.setdefault("priority_label", priority_label)
    effective_payload.setdefault("sla_label", _sla_label_for_priority(priority_label))
    if owner_hint:
        effective_payload.setdefault("owner_hint", owner_hint)
    if escalation_hint:
        effective_payload.setdefault("escalation_hint", escalation_hint)

    existing = await _find_open_system_task(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        system_key=system_key,
    )
    if existing:
        existing.title = title
        existing.description = description
        existing.priority = priority
        existing.source = source
        existing.recommended_action = recommended_action
        existing.action_payload = effective_payload
        existing.assigned_role = resolved_role
        existing.assigned_to_id = assigned_to_id
        existing.task_track = task_track
        existing.due_at = effective_due_at
        existing.updated_at = datetime.utcnow()
        session.add(existing)
        return existing

    if await _recently_dismissed_system_task_exists(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        system_key=system_key,
    ):
        logger.info(
            "suppressing recently dismissed system task: entity_type=%s entity_id=%s system_key=%s",
            entity_type,
            entity_id,
            system_key,
        )
        return None

    task = Task(
        entity_type=entity_type,
        entity_id=entity_id,
        task_type="system",
        title=title,
        description=description,
        priority=priority,
        source=source,
        recommended_action=recommended_action,
        action_payload=effective_payload,
        system_key=system_key,
        assigned_role=resolved_role,
        assigned_to_id=assigned_to_id,
        task_track=task_track,
        due_at=effective_due_at,
    )
    session.add(task)
    return task


async def backfill_open_task_assignments(
    session: AsyncSession,
    *,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
) -> None:
    """Re-resolve assignees for open tasks.

    When ``entity_type``/``entity_id`` are given, only that entity's open tasks
    are scanned — correct *and* cheap for the hot per-write paths (creating an
    activity on contact X can only change task assignments for contact X). With
    no scope it falls back to a full workspace sweep, kept for the task list /
    count self-heal callers.
    """
    stmt = select(Task).where(Task.status == "open")
    if entity_type is not None and entity_id is not None:
        stmt = stmt.where(Task.entity_type == entity_type, Task.entity_id == entity_id)
    open_tasks = (await session.execute(stmt)).scalars().all()

    for task in open_tasks:
        if task.task_type == "system":
            assigned_to_id, resolved_role = await _resolve_task_assignee(
                session,
                entity_type=task.entity_type,
                entity_id=task.entity_id,
                preferred_role=task.assigned_role,
            )
            if (
                assigned_to_id != task.assigned_to_id
                or resolved_role != task.assigned_role
            ):
                task.assigned_to_id = assigned_to_id
                task.assigned_role = resolved_role
                task.updated_at = datetime.utcnow()
                session.add(task)
            continue

        if task.assigned_to_id or not task.created_by_id:
            continue
        task.assigned_to_id = task.created_by_id
        task.updated_at = datetime.utcnow()
        session.add(task)


async def _resolve_system_task(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: UUID,
    system_key: str,
    status: str = "completed",
) -> None:
    task = await _find_open_system_task(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        system_key=system_key,
    )
    if not task:
        return
    task.status = status
    task.completed_at = datetime.utcnow()
    task.updated_at = datetime.utcnow()
    session.add(task)


async def complete_system_task(
    session: AsyncSession,
    task: Task,
    user: User,
) -> dict[str, str]:
    await apply_task_action(session, task, user)
    now = datetime.utcnow()
    task.status = "completed"
    task.accepted_at = now
    task.completed_at = now
    task.updated_at = now
    session.add(task)
    return {"message": "completed"}


def _stage_reached(current_stage: str | None, target_stage: str) -> bool:
    if not current_stage:
        return False
    return STAGE_INDEX.get(current_stage, -1) >= STAGE_INDEX.get(target_stage, 999)


def _activity_signal_text(activity: Activity) -> str:
    metadata = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
    metadata_text: list[str] = []
    for key in ("summary", "content", "text", "transcription", "thread_latest_message_text", "thread_context_excerpt", "google_doc_transcript"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            metadata_text.append(_normalize(value))

    for comment in metadata.get("comments") or []:
        if isinstance(comment, dict):
            value = comment.get("content")
            if isinstance(value, str) and value.strip():
                metadata_text.append(_normalize(value))

    fetched_call = metadata.get("fetched_call") if isinstance(metadata.get("fetched_call"), dict) else {}
    for comment in fetched_call.get("comments") or []:
        if isinstance(comment, dict):
            value = comment.get("content")
            if isinstance(value, str) and value.strip():
                metadata_text.append(_normalize(value))
    tag_names = [str(tag.get("name") or "").strip() for tag in fetched_call.get("tags") or [] if isinstance(tag, dict)]
    if tag_names:
        metadata_text.append(_normalize(" ".join(tag_names)))

    for entry in metadata.get("topics") or []:
        if isinstance(entry, dict):
            value = entry.get("name") or entry.get("label") or entry.get("topic")
        else:
            value = entry
        if isinstance(value, str) and value.strip():
            metadata_text.append(_normalize(value))

    for entry in metadata.get("action_items") or metadata.get("items") or []:
        if isinstance(entry, dict):
            value = entry.get("text") or entry.get("title") or entry.get("content")
        else:
            value = entry
        if isinstance(value, str) and value.strip():
            metadata_text.append(_normalize(value))

    conversation_intelligence = metadata.get("conversation_intelligence") if isinstance(metadata.get("conversation_intelligence"), dict) else {}
    if conversation_intelligence:
        for key in ("summary", "transcription"):
            value = conversation_intelligence.get(key)
            if isinstance(value, str) and value.strip():
                metadata_text.append(_normalize(value))
        for entry in conversation_intelligence.get("topics") or []:
            if isinstance(entry, str) and entry.strip():
                metadata_text.append(_normalize(entry))
        for entry in conversation_intelligence.get("action_items") or []:
            if isinstance(entry, str) and entry.strip():
                metadata_text.append(_normalize(entry))
        for entry in conversation_intelligence.get("sentiments") or []:
            if isinstance(entry, str) and entry.strip():
                metadata_text.append(_normalize(entry))

    return " ".join(
        filter(
            None,
            [
                _normalize(activity.ai_summary),
                _normalize(activity.content),
                _normalize(activity.email_subject),
                *metadata_text,
            ],
        )
    )


def _activity_has_conversation_intelligence(activity: Activity) -> bool:
    metadata = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
    bundle = metadata.get("conversation_intelligence") if isinstance(metadata.get("conversation_intelligence"), dict) else {}
    return bool(
        bundle
        and (
            bundle.get("summary")
            or bundle.get("transcription")
            or bundle.get("topics")
            or bundle.get("action_items")
            or bundle.get("sentiments")
        )
    )


def _health_bucket(score: int) -> str:
    if score >= 70:
        return "green"
    if score >= 40:
        return "yellow"
    return "red"


def _email_domain(value: str | None) -> str:
    email = (value or "").strip().lower()
    if "@" not in email:
        return ""
    return email.split("@", 1)[1]


def _has_security_stakeholder(contacts: list[Contact]) -> bool:
    for contact in contacts:
        signal = " ".join(
            filter(
                None,
                [
                    _normalize(contact.title),
                    _normalize(contact.persona),
                    _normalize(contact.persona_type),
                ],
            )
        )
        if _contains_any(signal, ["security", "procurement", "legal", "compliance", "it"]):
            return True
    return False


def _has_internal_email_after(
    activities: list[Activity],
    *,
    after: datetime | None,
    internal_domain: str,
) -> bool:
    if not after or not internal_domain:
        return False
    return any(
        activity.type == "email"
        and _email_domain(activity.email_from) == internal_domain
        and activity.created_at >= after
        for activity in activities
    )


def _has_external_email_after(
    activities: list[Activity],
    *,
    after: datetime | None,
    internal_domain: str,
) -> bool:
    if not after:
        return False
    return any(
        activity.type == "email"
        and _email_domain(activity.email_from) != internal_domain
        and activity.created_at >= after
        for activity in activities
    )


def _recent_buyer_thread_texts(
    activities: list[Activity],
    *,
    max_items: int = 5,
) -> list[str]:
    texts: list[str] = []
    seen_threads: set[str] = set()
    for activity in activities:
        if activity.type != "email":
            continue
        metadata = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
        thread_id = str(metadata.get("gmail_thread_id") or activity.email_message_id or "").strip()
        if thread_id:
            if thread_id in seen_threads:
                continue
            seen_threads.add(thread_id)
        text = _activity_signal_text(activity)
        if text:
            texts.append(text)
        if len(texts) >= max_items:
            break
    return texts


def _text_contains_any(texts: list[str], terms: Iterable[str]) -> bool:
    return any(_contains_any(text, terms) for text in texts)


def _latest_matching_activity(
    activities: list[Activity],
    predicate: Callable[[Activity], bool],
) -> Activity | None:
    for activity in activities:
        if predicate(activity):
            return activity
    return None


def _detect_competitor_signal(text: str) -> str | None:
    known = ["rocketlane", "arrows", "guidecx", "monday", "asana", "wrike", "clickup"]
    for name in known:
        if name in text:
            return name.title()
    if _contains_any(text, ["competitor", "alternative", "vs ", "other vendor"]):
        return "Competitor"
    return None


async def _refresh_company_tasks(session: AsyncSession, entity_id: UUID) -> None:
    company = await session.get(Company, entity_id)
    if not company:
        return

    cache = company.enrichment_cache if isinstance(company.enrichment_cache, dict) else {}
    has_icp = isinstance(cache.get("icp_analysis"), dict)
    contacts = (
        await session.execute(select(Contact.id).where(Contact.company_id == company.id))
    ).scalars().all()

    if not company.enriched_at or company.domain.endswith(".unknown"):
        await _upsert_system_task(
            session,
            entity_type="company",
            entity_id=company.id,
            system_key="company_re_enrich",
            title="Refresh company enrichment",
            description="This account still has missing or incomplete firmographic data. Re-run enrichment to update the account profile.",
            priority="high" if company.domain.endswith(".unknown") else "medium",
            source="system",
            recommended_action="re_enrich_company",
            action_payload={"company_id": str(company.id)},
            assigned_role="ae",
        )
    else:
        await _resolve_system_task(session, entity_type="company", entity_id=company.id, system_key="company_re_enrich")

    if not has_icp or not company.icp_score:
        await _upsert_system_task(
            session,
            entity_type="company",
            entity_id=company.id,
            system_key="company_refresh_icp",
            title="Refresh ICP research",
            description="Beacon does not yet have a usable ICP decision layer for this account. Refresh research to update fit, timing, and messaging.",
            priority="medium",
            source="system",
            recommended_action="refresh_icp_research",
            action_payload={"company_id": str(company.id)},
            assigned_role="ae",
        )
    else:
        await _resolve_system_task(session, entity_type="company", entity_id=company.id, system_key="company_refresh_icp")

    if not contacts:
        await _resolve_system_task(session, entity_type="company", entity_id=company.id, system_key="company_find_stakeholders", status="dismissed")
        await _upsert_system_task(
            session,
            entity_type="company",
            entity_id=company.id,
            system_key="company_upload_prospects",
            title="Upload prospects for this account",
            description="Company research is temporarily not sourcing contacts automatically. Upload a prospect CSV from Prospecting to attach the right stakeholders to this account.",
            priority="medium",
            source="system",
            recommended_action=None,
            action_payload={"company_id": str(company.id), "company_name": company.name, "next_step": "upload_prospect_csv"},
            assigned_role="sdr",
        )
    else:
        await _resolve_system_task(session, entity_type="company", entity_id=company.id, system_key="company_find_stakeholders", status="dismissed")
        await _resolve_system_task(session, entity_type="company", entity_id=company.id, system_key="company_upload_prospects")


async def _refresh_contact_tasks(session: AsyncSession, entity_id: UUID) -> None:
    contact = await session.get(Contact, entity_id)
    if not contact:
        return

    linked_deal = (
        await session.execute(select(DealContact.deal_id).where(DealContact.contact_id == contact.id))
    ).first()
    has_deal = bool(linked_deal)

    sequence_text = " ".join(
        part for part in [
            _normalize(contact.sequence_status),
            _normalize(contact.instantly_status),
            _normalize(contact.enrichment_data.get("tracking_stage") if isinstance(contact.enrichment_data, dict) else None),
        ]
        if part
    )

    # ── Pull recent Instantly email activity ──────────────────────────────────
    instantly_rows = (
        await session.execute(
            select(Activity)
            .where(
                Activity.contact_id == contact.id,
                Activity.source == "instantly",
            )
            .order_by(Activity.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    latest_reply = next((a for a in instantly_rows if "reply" in (a.content or "").lower() or a.type == "email_reply"), None)
    latest_open = next((a for a in instantly_rows if "opened" in (a.content or "").lower()), None)
    latest_bounce = next((a for a in instantly_rows if "bounce" in (a.content or "").lower()), None)
    latest_interested = next((a for a in instantly_rows if "interested" in (a.content or "").lower() and "not interested" not in (a.content or "").lower()), None)
    latest_unsubscribed = next((a for a in instantly_rows if "unsubscribed" in (a.content or "").lower()), None)

    seq_status = _normalize(contact.sequence_status)
    has_recent_reply = bool(latest_reply and latest_reply.created_at >= datetime.utcnow() - timedelta(days=7))
    has_recent_open = bool(latest_open and latest_open.created_at >= datetime.utcnow() - timedelta(days=3))
    has_recent_interested = bool(latest_interested and latest_interested.created_at >= datetime.utcnow() - timedelta(days=5))
    is_bounced = bool(latest_bounce or seq_status == "bounced")
    is_unsubscribed = bool(latest_unsubscribed or seq_status == "unsubscribed")
    # EMAIL negative only — the "Instantly flagged this prospect as Not Interested"
    # system task must key off the email-sourced marker, not the overloaded
    # sequence_status (which a negative CALL/LinkedIn disposition also sets). Else
    # a phone "Connected - Not Interested" wrongly raises an Instantly-attributed task.
    is_not_interested = _normalize(contact.instantly_status) == "not_interested"

    if not contact.phone and bool(contact.email):
        await _upsert_system_task(
            session,
            entity_type="contact",
            entity_id=contact.id,
            system_key="contact_refresh_data",
            title="Refresh prospect contact data",
            description="This prospect is active but still missing a phone number. Re-enrich the record to try to pull fresh contact details.",
            priority="medium",
            source="system",
            recommended_action="re_enrich_contact",
            action_payload={"contact_id": str(contact.id)},
            assigned_role="sdr",
        )
    else:
        await _resolve_system_task(session, entity_type="contact", entity_id=contact.id, system_key="contact_refresh_data")

    aircall_rows = (
        await session.execute(
            select(Activity)
            .where(
                Activity.contact_id == contact.id,
                Activity.source == "aircall",
                Activity.type.in_(["call", "note", "transcript"]),
            )
            .order_by(Activity.created_at.desc())
            .limit(12)
        )
    ).scalars().all()
    tldv_rows = (
        await session.execute(
            select(Activity)
            .where(
                Activity.contact_id == contact.id,
                Activity.source == "tldv",
                Activity.type.in_(["meeting", "transcript", "note"]),
            )
            .order_by(Activity.created_at.desc())
            .limit(8)
        )
    ).scalars().all()
    latest_answered_call = next((activity for activity in aircall_rows if activity.call_outcome == "answered"), None)
    latest_missed_call = next((activity for activity in aircall_rows if activity.call_outcome == "missed"), None)
    latest_voicemail = next((activity for activity in aircall_rows if activity.call_outcome == "voicemail"), None)
    aircall_signal_text = " ".join(filter(None, (_activity_signal_text(activity) for activity in aircall_rows[:6])))
    tldv_signal_text = " ".join(filter(None, (_activity_signal_text(activity) for activity in tldv_rows[:4])))
    recent_tldv_meeting = next(
        (activity for activity in tldv_rows if activity.created_at >= datetime.utcnow() - timedelta(days=7)),
        None,
    )
    latest_answered_call_has_intelligence = bool(
        latest_answered_call
        and any(
            activity.call_id
            and latest_answered_call.call_id
            and activity.call_id == latest_answered_call.call_id
            and _activity_has_conversation_intelligence(activity)
            for activity in aircall_rows
        )
    )

    has_recent_answered_call = bool(
        latest_answered_call
        and latest_answered_call.created_at >= datetime.utcnow() - timedelta(days=3)
        and (
            (latest_answered_call.call_duration or 0) >= 90
            or latest_answered_call_has_intelligence
        )
    )
    has_recent_missed_call = bool(
        latest_missed_call
        and latest_missed_call.created_at >= datetime.utcnow() - timedelta(days=2)
        and (not latest_answered_call or latest_answered_call.created_at < latest_missed_call.created_at)
    )
    has_recent_voicemail = bool(
        latest_voicemail
        and latest_voicemail.created_at >= datetime.utcnow() - timedelta(days=2)
        and (not latest_answered_call or latest_answered_call.created_at < latest_voicemail.created_at)
    )
    has_call_progress_signal = bool(
        aircall_signal_text
        and _contains_any(aircall_signal_text, ["meeting", "demo", "workshop", "poc", "pilot"])
        and _contains_any(aircall_signal_text, ["schedule", "scheduled", "agreed", "book", "next week", "move forward"])
    )
    has_tldv_progress_signal = bool(
        recent_tldv_meeting
        and (
            _contains_any(tldv_signal_text, ["meeting", "demo", "workshop", "poc", "pilot", "pricing", "proposal"])
            or _activity_has_conversation_intelligence(recent_tldv_meeting)
        )
    )

    if (
        (("meeting_booked" in sequence_text or "meeting booked" in sequence_text) or has_call_progress_signal or has_tldv_progress_signal)
        and not has_deal
    ):
        await _upsert_system_task(
            session,
            entity_type="contact",
            entity_id=contact.id,
            system_key="contact_convert_to_deal",
            title="Convert meeting-ready prospect into a deal",
            description="Beacon sees a strong meeting, demo, or POC signal on this prospect. Convert it into a deal so the opportunity can move through the board.",
            priority="high",
            source="tldv" if has_tldv_progress_signal else ("aircall" if has_call_progress_signal else "instantly"),
            recommended_action="convert_contact_to_deal",
            action_payload={"contact_id": str(contact.id), "stage": "demo_done"},
            assigned_role="sdr",
        )
    else:
        await _resolve_system_task(session, entity_type="contact", entity_id=contact.id, system_key="contact_convert_to_deal")

    # Reactive call/voicemail tasks should clear automatically once the
    # sequence has moved past the point of cold follow-up — otherwise reps
    # keep getting nagged to "retry the missed call" after the meeting is
    # already booked, the prospect unsubscribed, or the email bounced.
    terminal_sequence_states = {"meeting_booked", "interested", "not_interested", "unsubscribed", "bounced"}
    is_terminal_seq = seq_status in terminal_sequence_states

    if not has_deal and not is_terminal_seq and has_recent_missed_call:
        await _upsert_system_task(
            session,
            entity_type="contact",
            entity_id=contact.id,
            system_key="contact_retry_call",
            title="Retry the missed call",
            description="Aircall logged a missed connection with this prospect. Retry the call or follow up while the context is still fresh.",
            priority="high",
            source="aircall",
            recommended_action="retry_contact_call",
            action_payload={"contact_id": str(contact.id), "next_step": "retry_call"},
            assigned_role="sdr",
        )
    else:
        await _resolve_system_task(session, entity_type="contact", entity_id=contact.id, system_key="contact_retry_call", status="dismissed")

    if not has_deal and not is_terminal_seq and has_recent_voicemail:
        await _upsert_system_task(
            session,
            entity_type="contact",
            entity_id=contact.id,
            system_key="contact_voicemail_follow_up",
            title="Follow up on the voicemail",
            description="A voicemail was left for this prospect. Send a short follow-up note that references the call and proposes the next step.",
            priority="medium",
            source="aircall",
            recommended_action="follow_up_voicemail",
            action_payload={"contact_id": str(contact.id), "next_step": "follow_up_voicemail"},
            assigned_role="sdr",
        )
    else:
        await _resolve_system_task(session, entity_type="contact", entity_id=contact.id, system_key="contact_voicemail_follow_up", status="dismissed")

    if not has_deal and (has_recent_answered_call or recent_tldv_meeting):
        recap_description = "Beacon sees a connected buyer conversation here. Send a short recap and confirm the next step while the context is still fresh."
        recap_source = "tldv" if recent_tldv_meeting else "aircall"
        recap_title = "Send post-meeting recap" if recent_tldv_meeting else "Send a post-call recap"
        if latest_answered_call_has_intelligence:
            latest_signal = _activity_signal_text(latest_answered_call)
            if latest_signal:
                recap_description = (
                    "Beacon pulled Aircall call intelligence for this conversation. "
                    "Send a recap and confirm the next step while the details are fresh."
                )
        elif recent_tldv_meeting:
            recap_description = (
                "Beacon pulled tl;dv meeting intelligence for this prospect. "
                "Send a post-meeting recap and confirm the next step while the details are still fresh."
            )
        await _upsert_system_task(
            session,
            entity_type="contact",
            entity_id=contact.id,
            system_key="contact_send_call_recap",
            title=recap_title,
            description=recap_description,
            priority="medium",
            source=recap_source,
            recommended_action="send_contact_call_recap",
            action_payload={"contact_id": str(contact.id), "next_step": "send_call_recap"},
            assigned_role="sdr",
        )
    else:
        await _resolve_system_task(session, entity_type="contact", entity_id=contact.id, system_key="contact_send_call_recap", status="dismissed")

    # ── Email / Instantly signal tasks ───────────────────────────────────────

    # Reply received → follow up on the reply thread (high priority, Beacon can draft)
    if not has_deal and has_recent_reply and seq_status not in {"meeting_booked", "interested"}:
        reply_content = (latest_reply.content or "") if latest_reply else ""
        await _upsert_system_task(
            session,
            entity_type="contact",
            entity_id=contact.id,
            system_key="contact_follow_up_reply",
            title="Follow up on email reply",
            description=(
                f"This prospect replied to an outreach email. Review their reply and send a personalised follow-up to keep momentum."
                + (f"\n\nReply excerpt: {reply_content[:300]}" if reply_content else "")
            ),
            priority="high",
            source="instantly",
            recommended_action="draft_reply_follow_up",
            action_payload={"contact_id": str(contact.id), "reply_excerpt": reply_content[:300], "next_step": "reply_to_prospect"},
            assigned_role="ae",
        )
    else:
        await _resolve_system_task(session, entity_type="contact", entity_id=contact.id, system_key="contact_follow_up_reply", status="dismissed")

    # Interested signal → book a call / meeting (very high priority, convert to deal soon)
    if not has_deal and has_recent_interested:
        await _upsert_system_task(
            session,
            entity_type="contact",
            entity_id=contact.id,
            system_key="contact_book_call_from_interest",
            title="Book a call — prospect expressed interest",
            description="Instantly flagged this prospect as Interested. Strike while it's hot — propose a discovery or demo call.",
            priority="high",
            source="instantly",
            recommended_action="book_call_from_interest",
            action_payload={"contact_id": str(contact.id), "next_step": "book_call"},
            assigned_role="ae",
        )
    else:
        await _resolve_system_task(session, entity_type="contact", entity_id=contact.id, system_key="contact_book_call_from_interest", status="dismissed")

    # Email opened (no reply yet) → send a targeted follow-up while attention is there
    if (
        not has_deal
        and has_recent_open
        and not has_recent_reply
        and seq_status not in {"replied", "meeting_booked", "interested", "not_interested", "unsubscribed", "bounced"}
    ):
        await _upsert_system_task(
            session,
            entity_type="contact",
            entity_id=contact.id,
            system_key="contact_follow_up_after_open",
            title="Send a follow-up — prospect opened the email",
            description="This prospect opened an outreach email but hasn't replied yet. Send a short, personalised follow-up to spark a conversation.",
            priority="medium",
            source="instantly",
            recommended_action="draft_open_follow_up",
            action_payload={"contact_id": str(contact.id), "next_step": "send_follow_up"},
            assigned_role="sdr",
        )
    else:
        await _resolve_system_task(session, entity_type="contact", entity_id=contact.id, system_key="contact_follow_up_after_open", status="dismissed")

    # Bounced email → fix the email address
    if is_bounced:
        await _upsert_system_task(
            session,
            entity_type="contact",
            entity_id=contact.id,
            system_key="contact_fix_bounced_email",
            title="Fix bounced email address",
            description="An outreach email bounced for this prospect. Re-enrich the contact to find a valid email or mark the record as unreachable.",
            priority="high",
            source="instantly",
            recommended_action="re_enrich_contact",
            action_payload={"contact_id": str(contact.id)},
            assigned_role="sdr",
        )
    else:
        await _resolve_system_task(session, entity_type="contact", entity_id=contact.id, system_key="contact_fix_bounced_email")

    # Unsubscribed → update CRM status, stop all outreach
    if is_unsubscribed:
        await _upsert_system_task(
            session,
            entity_type="contact",
            entity_id=contact.id,
            system_key="contact_mark_unsubscribed",
            title="Mark prospect as unsubscribed and pause outreach",
            description="This prospect unsubscribed from the email sequence. Update the CRM status and ensure no further automated outreach is sent.",
            priority="medium",
            source="instantly",
            recommended_action="mark_contact_unsubscribed",
            action_payload={"contact_id": str(contact.id)},
            assigned_role="sdr",
        )
    else:
        await _resolve_system_task(session, entity_type="contact", entity_id=contact.id, system_key="contact_mark_unsubscribed")

    # Not interested → log it and decide whether to nurture or close
    if is_not_interested and not has_deal:
        await _upsert_system_task(
            session,
            entity_type="contact",
            entity_id=contact.id,
            system_key="contact_handle_not_interested",
            title="Prospect is not interested — review and decide next step",
            description="Instantly flagged this prospect as Not Interested. Decide whether to nurture long-term, reassign, or close the record.",
            priority="low",
            source="instantly",
            recommended_action="close_not_interested_contact",
            action_payload={"contact_id": str(contact.id)},
            assigned_role="ae",
        )
    else:
        await _resolve_system_task(session, entity_type="contact", entity_id=contact.id, system_key="contact_handle_not_interested")


def _deal_signal_task_from_signal(signal: ActivitySignal, current_stage: str) -> tuple[str, str, str] | None:
    target_stage = signal.stage_cue
    if not target_stage:
        return None
    if target_stage == "commercial_negotiation" and not _stage_reached(current_stage, "poc_done"):
        return None
    if target_stage in {"msa_review", "workshop"} and not _stage_reached(current_stage, "commercial_negotiation"):
        return None
    stage_copy = {
        "poc_agreed": (
            "Move deal to POC Agreed",
            "The latest buyer language clearly aligns on a POC or pilot. Move the deal forward so the board stays accurate.",
        ),
        "poc_wip": (
            "Move deal to POC WIP",
            "The latest buyer language indicates the POC is actively underway. Update the deal stage so execution is visible.",
        ),
        "poc_done": (
            "Move deal to POC Done",
            "The latest buyer language indicates the POC is complete. Reflect that progress in the pipeline before the next commercial step.",
        ),
        "commercial_negotiation": (
            "Move deal to Commercial Negotiation",
            "The latest buyer language is now commercial in nature. Move the deal so pricing and terms are tracked in the right stage.",
        ),
        "msa_review": (
            "Move deal to MSA Review",
            "The latest buyer language indicates legal, security, or procurement review is underway. Update the stage to match the current motion.",
        ),
        "workshop": (
            "Move deal to Workshop",
            "The latest buyer language is coordinating a workshop or working session. Move the deal so the next motion is visible.",
        ),
        "closed_won": (
            "Move deal to Closed Won",
            "The latest buyer language signals the deal is moving forward with Beacon. Mark it correctly so the board and forecast stay trustworthy.",
        ),
        "not_a_fit": (
            "Move deal to Not a Fit",
            "The latest buyer language indicates this is no longer moving forward. Update the deal so the pipeline reflects reality.",
        ),
    }
    if target_stage not in stage_copy or _stage_reached(current_stage, target_stage):
        return None
    title, description = stage_copy[target_stage]
    return target_stage, title, description


def _deal_signal_task(text: str, current_stage: str) -> tuple[str, str, str] | None:
    return _deal_signal_task_from_signal(classify_activity_text(text), current_stage)


async def _apply_stage_playbook_tasks(
    session: AsyncSession,
    deal: Deal,
    activity_rows: list[Activity],
    linked_contacts: list[Contact],
    *,
    ai_interpretation: DealActivityInterpretation | None,
    created_keys: set[str],
) -> None:
    now = datetime.utcnow()
    internal_domain = _email_domain(settings.GMAIL_SHARED_INBOX or "zippy@beacon.li")
    recent_texts = _recent_buyer_thread_texts(activity_rows)

    if deal.stage == "reprospect":
        latest_external = next(
            (
                activity
                for activity in activity_rows
                if activity.type == "email" and _email_domain(activity.email_from) != internal_domain
            ),
            None,
        )
        internal_after_reply = [
            activity
            for activity in activity_rows
            if activity.type == "email"
            and _email_domain(activity.email_from) == internal_domain
            and (latest_external is None or activity.created_at > latest_external.created_at)
        ]
        if (
            len(internal_after_reply) >= 2
            and internal_after_reply[0].created_at <= now - timedelta(hours=48)
        ):
            created_keys.add("deal_reprospect_reengage")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_reprospect_reengage",
                title="Re-engage this cold account with a new angle",
                description="This reprospect thread has already had 2+ outreach attempts without a buyer reply. Use a fresh hook or a break-up style touch instead of repeating the same message.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="sdr",
            )

        if _text_contains_any(recent_texts, ["not the right person", "wrong person", "reach out to", "better contact", "best person"]):
            created_keys.add("deal_reprospect_redirect")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_reprospect_redirect",
                title="Map the redirected stakeholder and re-engage",
                description="A buyer reply indicates this is not the right person. Update the stakeholder map, capture the redirect, and restart outreach with the right owner.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="sdr",
            )

        if _text_contains_any(recent_texts, ["left the company", "moved on", "no longer with", "changed roles", "new role"]):
            created_keys.add("deal_reprospect_successor")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_reprospect_successor",
                title="Map the successor stakeholder",
                description="Recent outreach suggests the prior contact left or changed roles. Update CRM ownership and find the next relevant stakeholder before continuing outreach.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="sdr",
            )
        return

    if deal.stage == "qualified_lead":
        pricing_or_budget_signal = (
            ai_interpretation is not None
            and ai_interpretation.commercial_state in {"directional", "pricing_request", "pushback"}
        ) or _text_contains_any(
            recent_texts,
            [
                "pricing",
                "price",
                "cost",
                "budget",
                "commercial terms",
                "roi",
                "business case",
            ],
        )
        if pricing_or_budget_signal:
            created_keys.add("deal_qualified_lead_roi")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_qualified_lead_roi",
                title="Build or refresh the ROI case",
                description="The buyer is asking about pricing, budget, or cost. Stay in qualification mode: tighten the ROI model, anchor the value case, and line up an ROI review instead of jumping straight to a proposal.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if _text_contains_any(
            recent_texts,
            [
                "architecture",
                "technical question",
                "integration",
                "api",
                "security questionnaire",
                "security review",
                "soc2",
                "sso",
                "implementation question",
            ],
        ) and not await _recent_system_task_exists(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_qualified_lead_tech_follow_up",
            days=7,
        ):
            created_keys.add("deal_qualified_lead_tech_follow_up")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_qualified_lead_tech_follow_up",
                title="Coordinate the technical follow-up",
                description="The buyer asked a technical or architecture question. Pull in Product, Rakesh, or an SE as needed and send the right technical asset while the thread is active.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if _text_contains_any(
            recent_texts,
            [
                "cto",
                "chief technology officer",
                "cfo",
                "chief financial officer",
                "coo",
                "chief operating officer",
                "head of ps",
                "head of professional services",
                "vp of professional services",
                "vp professional services",
            ],
        ):
            created_keys.add("deal_qualified_lead_stakeholder")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_qualified_lead_stakeholder",
                title="Map the new stakeholder and tailor the story",
                description="A senior stakeholder showed up in the buyer thread. Add them to the stakeholder map, research their lens, and tailor the follow-up for their priorities before the next meeting.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        poc_signal = _text_contains_any(
            recent_texts,
            [
                "let's do a poc",
                "lets do a poc",
                "let's do a pilot",
                "lets do a pilot",
                "move forward with a poc",
                "move forward with a pilot",
                "start the poc",
                "start the pilot",
            ],
        )
        if poc_signal:
            created_keys.add("deal_move_poc_agreed")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_move_poc_agreed",
                title="Move deal to POC Agreed",
                description="The buyer is explicitly asking to start a POC or pilot. Move the deal forward so the board reflects the motion and the next execution step is visible.",
                priority="high",
                source="playbook",
                recommended_action="move_deal_stage",
                action_payload={"deal_id": str(deal.id), "stage": "poc_agreed"},
                assigned_role="ae",
            )
            created_keys.add("deal_qualified_lead_poc_kickoff")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_qualified_lead_poc_kickoff",
                title="Schedule the POC kickoff",
                description="The buyer is aligned on a POC or pilot. Get the kickoff on the calendar quickly so the deal can move from agreement into execution without drift.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if _text_contains_any(recent_texts, ["reference", "references", "case study", "customer story"]) and not await _recent_system_task_exists(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_qualified_lead_case_study",
            days=7,
        ):
            created_keys.add("deal_qualified_lead_case_study")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_qualified_lead_case_study",
                title="Send a relevant reference or case study",
                description="The buyer asked for proof from a similar customer. Send the most relevant case study or reference so the value story feels concrete for this account.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if (
            ai_interpretation is not None and ai_interpretation.legal_state != "none"
        ) or _text_contains_any(
            recent_texts,
            [
                "procurement",
                "security team",
                "security review",
                "security questionnaire",
                "soc2",
                "vendor onboarding",
                "compliance",
            ],
        ):
            created_keys.add("deal_qualified_lead_security_follow_up")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_qualified_lead_security_follow_up",
                title="Coordinate the security or procurement response",
                description="Security or procurement entered the thread while the deal is still qualified lead. Pull in the right internal owner and send the security one-pager or supporting material instead of treating this like full paper process work.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        recent_internal = next(
            (
                activity
                for activity in activity_rows
                if activity.type == "email" and _email_domain(activity.email_from) == internal_domain
            ),
            None,
        )
        recent_external = next(
            (
                activity
                for activity in activity_rows
                if activity.type == "email" and _email_domain(activity.email_from) != internal_domain
            ),
            None,
        )
        if (
            recent_internal
            and (recent_external is None or recent_external.created_at < recent_internal.created_at)
            and recent_internal.created_at < now - timedelta(days=7)
        ):
            created_keys.add("deal_qualified_lead_follow_up")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_qualified_lead_follow_up",
                title="Send a value-forward follow-up",
                description="This qualified lead has gone quiet for 7+ days after the latest outbound touch. Follow up with a value reminder and a crisp CTA rather than defaulting to a generic nudge or break-up.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )
        return

    if deal.stage == "poc_agreed":
        meetings = (
            await session.execute(
                select(Meeting)
                .where(Meeting.deal_id == deal.id)
                .order_by(Meeting.scheduled_at.desc(), Meeting.updated_at.desc())
            )
        ).scalars().all()
        upcoming_poc = next(
            (
                meeting
                for meeting in meetings
                if meeting.meeting_type == "poc"
                and meeting.status == "scheduled"
                and meeting.scheduled_at is not None
                and meeting.scheduled_at >= now
            ),
            None,
        )
        activity_texts = [text for text in (_activity_signal_text(activity) for activity in activity_rows) if text]

        nda_sent = _latest_matching_activity(
            activity_rows,
            lambda activity: activity.type == "email"
            and _contains_any(_activity_signal_text(activity), ["nda", "non-disclosure", "non disclosure"])
            and _contains_any(_activity_signal_text(activity), ["sent", "attached", "please sign", "for signature", "docusign"]),
        )
        nda_signed = _latest_matching_activity(
            activity_rows,
            lambda activity: activity.type == "email"
            and (nda_sent is None or activity.created_at >= nda_sent.created_at)
            and _contains_any(_activity_signal_text(activity), ["nda", "non-disclosure", "non disclosure", "docusign"])
            and _contains_any(_activity_signal_text(activity), ["signed", "executed", "countersigned", "completed"]),
        )

        if nda_sent and not nda_signed and nda_sent.created_at <= now - timedelta(days=5):
            created_keys.add("deal_poc_agreed_nda_chase")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_agreed_nda_chase",
                title="Chase the unsigned NDA",
                description="The NDA appears to have been sent 5+ days ago with no signed confirmation back yet. Follow up directly and offer a quick unblock call so the POC does not stall before kickoff.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if nda_signed and not upcoming_poc:
            created_keys.add("deal_poc_agreed_kickoff")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_agreed_kickoff",
                title="Schedule the POC kickoff",
                description="The NDA looks signed, but Beacon does not see a POC kickoff on the calendar yet. Lock in the kickoff quickly so the POC starts with a clear owner and timeline.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )
            created_keys.add("deal_poc_agreed_plan")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_agreed_plan",
                title="Draft the scoped POC plan",
                description="The deal is ready to kick off the POC, but Beacon does not see a scoped plan yet. Send the POC plan or 2-pager so the customer, AE, and SE align on success criteria and ownership.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if _text_contains_any(
            recent_texts,
            [
                "what data do you need",
                "what do you need from us",
                "what data do you need from us",
                "data checklist",
                "technical requirements",
                "what access do you need",
            ],
        ) and not await _recent_system_task_exists(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_poc_agreed_setup",
            days=7,
        ):
            created_keys.add("deal_poc_agreed_setup")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_agreed_setup",
                title="Send the POC setup checklist",
                description="The buyer is asking what Beacon needs to start the POC. Send the data checklist, access requirements, and technical setup notes so the team can get moving.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if (
            deal.stage_entered_at
            and deal.stage_entered_at <= now - timedelta(hours=48)
            and not _text_contains_any(
                activity_texts,
                [
                    "rakesh approved",
                    "internal approval",
                    "clickup",
                    "poc approval",
                    "approved internally",
                    "se is ready",
                    "environment ready",
                ],
            )
        ):
            created_keys.add("deal_poc_agreed_internal_approval")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_agreed_internal_approval",
                title="Raise internal POC approval",
                description="This deal has been in POC Agreed for 48+ hours and Beacon does not see the internal approval motion yet. Pull in Rakesh, raise the internal approval, and make sure the execution handoff is actually underway.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if (
            ai_interpretation is not None
            and ai_interpretation.commercial_state in {"directional", "pricing_request", "pushback"}
        ) or _text_contains_any(
            recent_texts,
            [
                "pricing",
                "price",
                "commercials",
                "proposal",
                "quote",
                "budget",
            ],
        ):
            created_keys.add("deal_poc_agreed_commercial_hold")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_agreed_commercial_hold",
                title="Reply with a post-POC commercial hold",
                description="Commercial questions are coming up during POC setup. Respond with a holding answer and keep the full proposal for after the POC rather than jumping early into final commercials.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        criteria_documented = field_has_capture(deal.qualification, "decision_criteria") or _contains_any(
            " ".join(filter(None, [deal.description, deal.next_step])),
            ["success criteria", "success metric", "business case", "poc plan"],
        )
        if (
            deal.stage_entered_at
            and deal.stage_entered_at <= now - timedelta(hours=72)
            and not criteria_documented
        ):
            created_keys.add("deal_poc_agreed_success_criteria")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_agreed_success_criteria",
                title="Document measurable POC success criteria",
                description="The deal is in POC Agreed, but Beacon still does not see measurable success criteria documented. Draft the business case or POC brief so everyone agrees on what success looks like.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )
        return

    if deal.stage == "poc_wip":
        meetings = (
            await session.execute(
                select(Meeting)
                .where(Meeting.deal_id == deal.id)
                .order_by(Meeting.scheduled_at.desc(), Meeting.updated_at.desc())
            )
        ).scalars().all()
        poc_meetings = [meeting for meeting in meetings if meeting.meeting_type == "poc" and meeting.scheduled_at is not None]
        recent_or_upcoming_checkpoint = next(
            (
                meeting
                for meeting in poc_meetings
                if now - timedelta(days=7) <= meeting.scheduled_at <= now + timedelta(days=7)
            ),
            None,
        )
        if not recent_or_upcoming_checkpoint:
            created_keys.add("deal_poc_wip_checkpoint")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_wip_checkpoint",
                title="Schedule the weekly POC checkpoint",
                description="Beacon does not see a recent or upcoming POC checkpoint. Put a 30-minute weekly checkpoint on the calendar so the POC stays active and the customer sees steady progress.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if (
            ai_interpretation is not None and ai_interpretation.blocker_state == "active"
        ) or _has_blocker_signal(recent_texts):
            created_keys.add("deal_poc_wip_blocker")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_wip_blocker",
                title="Escalate the POC blocker internally",
                description="The customer reported a blocker or bug during the POC. Pull in Product or the SE immediately, get an ETA, and close the loop with the buyer while the issue is fresh.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        criteria_documented = field_has_capture(deal.qualification, "decision_criteria") or _contains_any(
            " ".join(filter(None, [deal.description, deal.next_step])),
            ["success criteria", "success metric", "business case", "poc plan"],
        )
        recent_scored_poc = next(
            (
                meeting
                for meeting in poc_meetings
                if meeting.status == "completed" and any([meeting.raw_notes, meeting.ai_summary, meeting.next_steps])
            ),
            None,
        )
        if (
            deal.stage_entered_at
            and deal.stage_entered_at <= now - timedelta(days=7)
            and (not criteria_documented or recent_scored_poc is None)
        ):
            created_keys.add("deal_poc_wip_midpoint_review")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_wip_midpoint_review",
                title="Run the mid-POC review",
                description="This POC is underway, but Beacon does not yet see strong validation against success criteria. Schedule the midpoint review and send a progress update so the buyer sees concrete movement.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        champion_emails = {
            (contact.email or "").strip().lower()
            for contact in linked_contacts
            if (contact.persona_type or "").strip().lower() == "champion" and contact.email
        }
        if champion_emails:
            latest_champion_inbound = _latest_matching_activity(
                activity_rows,
                lambda activity: activity.type == "email" and (activity.email_from or "").strip().lower() in champion_emails,
            )
            last_champion_touch = latest_champion_inbound.created_at if latest_champion_inbound else deal.stage_entered_at
            if last_champion_touch and last_champion_touch <= now - timedelta(days=7):
                created_keys.add("deal_poc_wip_champion")
                await _upsert_system_task(
                    session,
                    entity_type="deal",
                    entity_id=deal.id,
                    system_key="deal_poc_wip_champion",
                    title="Re-engage the champion and widen coverage",
                    description="The champion has been quiet for 7+ days during the POC. Re-engage them directly and consider widening coverage to the economic buyer before momentum slips.",
                    priority="high",
                    source="playbook",
                    recommended_action=None,
                    action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                    assigned_role="ae",
                )

        if (
            ai_interpretation is not None
            and ai_interpretation.commercial_state in {"directional", "pricing_request", "pushback"}
        ) or _text_contains_any(
            recent_texts,
            [
                "pricing",
                "price",
                "commercials",
                "proposal",
                "quote",
                "budget",
            ],
        ):
            created_keys.add("deal_poc_wip_pricing_reply")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_wip_pricing_reply",
                title="Reply with directional pricing guidance",
                description="The buyer is asking about pricing while the POC is still running. Answer directionally and line up the post-POC commercial conversation rather than jumping to a final proposal now.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )
        return

    if deal.stage == "poc_done":
        meetings = (
            await session.execute(
                select(Meeting)
                .where(Meeting.deal_id == deal.id)
                .order_by(Meeting.scheduled_at.desc(), Meeting.updated_at.desc())
            )
        ).scalars().all()
        activity_texts = [text for text in (_activity_signal_text(activity) for activity in activity_rows) if text]
        readout_scheduled = next(
            (
                meeting
                for meeting in meetings
                if meeting.scheduled_at is not None
                and meeting.scheduled_at >= now
                and (
                    meeting.meeting_type in {"poc", "qbr"}
                    or _contains_any((meeting.title or "").lower(), ["readout", "results", "commercial", "decision"])
                )
            ),
            None,
        )
        results_deck_built = _text_contains_any(
            activity_texts,
            ["results deck", "poc results", "roi v2", "before and after", "before/after", "readout deck"],
        ) or _contains_any(
            " ".join(filter(None, [deal.description, deal.next_step])),
            ["results deck", "poc results", "roi v2", "readout"],
        )
        if deal.stage_entered_at and deal.stage_entered_at <= now - timedelta(hours=48) and not results_deck_built:
            created_keys.add("deal_poc_done_results_deck")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_done_results_deck",
                title="Build the POC results deck",
                description="The deal is in POC Done, but Beacon does not yet see a results deck or refreshed ROI readout. Build the before-and-after story quickly so the team can convert proof into a commercial next step.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if results_deck_built and not readout_scheduled and deal.stage_entered_at and deal.stage_entered_at <= now - timedelta(hours=72):
            created_keys.add("deal_poc_done_readout")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_done_readout",
                title="Schedule the POC results readout",
                description="The results story looks ready, but Beacon does not see a readout or commercial conversation scheduled yet. Get the economic buyer and the decision team into the readout quickly.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        criteria_met = _text_contains_any(
            activity_texts,
            ["success criteria met", "criteria met", "successful poc", "poc successful", "met the goals", "met our goals", "worked as expected"],
        )
        criteria_missed = _text_contains_any(
            activity_texts,
            ["success criteria not met", "criteria not met", "missed the criteria", "missed the goal", "did not meet", "didn't meet", "partial success", "partially met"],
        )
        if criteria_met and not deal.next_step and not readout_scheduled:
            created_keys.add("deal_poc_done_close_motion")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_done_close_motion",
                title="Push for the commercial next step",
                description="The POC looks successful, but Beacon does not see a clear next step on the deal yet. Send the direct close question and put the commercial readout on the calendar.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if criteria_missed:
            created_keys.add("deal_poc_done_internal_decision")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_done_internal_decision",
                title="Make the post-POC decision with Rakesh",
                description="The POC outcome looks partial or unsuccessful. Pull in Rakesh immediately to decide whether to extend, descope, or disqualify rather than drifting in place.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        recent_internal = next(
            (
                activity
                for activity in activity_rows
                if activity.type == "email" and _email_domain(activity.email_from) == internal_domain
            ),
            None,
        )
        recent_external = next(
            (
                activity
                for activity in activity_rows
                if activity.type == "email" and _email_domain(activity.email_from) != internal_domain
            ),
            None,
        )
        if (
            recent_internal
            and (recent_external is None or recent_external.created_at < recent_internal.created_at)
            and recent_internal.created_at <= now - timedelta(days=5)
        ):
            created_keys.add("deal_poc_done_quiet")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_poc_done_quiet",
                title="Re-engage after the POC",
                description="The buyer has gone quiet after the POC. Follow up with the champion, loop in the decision maker if needed, and push toward the commercial conversation before the proof cools off.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )
        return

    if deal.stage == "commercial_negotiation":
        activity_texts = [text for text in (_activity_signal_text(activity) for activity in activity_rows) if text]
        proposal_sent = _latest_matching_activity(
            activity_rows,
            lambda activity: activity.type == "email"
            and _contains_any(_activity_signal_text(activity), ["proposal", "pricing", "quote", "commercial terms"])
            and _contains_any(_activity_signal_text(activity), ["attached", "sent", "for your review", "please find", "shared"]),
        )
        proposal_reply = _latest_matching_activity(
            activity_rows,
            lambda activity: proposal_sent is not None
            and activity.created_at > proposal_sent.created_at
            and activity.type in {"email", "call", "meeting"},
        )
        if proposal_sent and not proposal_reply and proposal_sent.created_at <= now - timedelta(days=5):
            created_keys.add("deal_commercial_negotiation_follow_up")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_commercial_negotiation_follow_up",
                title="Follow up on the proposal and schedule review",
                description="Beacon sees a proposal or pricing package was sent 5+ days ago with no buyer reply yet. Follow up and try to lock a proposal review call instead of silently waiting.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if (
            ai_interpretation is not None and ai_interpretation.commercial_state == "pushback"
        ) or _text_contains_any(
            recent_texts,
            ["too expensive", "budget is tight", "budget constraint", "price is high", "discount", "cost concern", "expensive"],
        ):
            created_keys.add("deal_commercial_negotiation_value_reanchor")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_commercial_negotiation_value_reanchor",
                title="Re-anchor on ROI before discounting",
                description="The buyer is pushing back on price. Re-anchor the conversation on value and pull in Rakesh before offering any discount so the commercial motion stays disciplined.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if _text_contains_any(
            recent_texts,
            ["payment terms", "payment schedule", "milestones", "carve-out", "carve out", "custom terms", "net 30", "net30", "net 45", "net45"],
        ):
            created_keys.add("deal_commercial_negotiation_terms")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_commercial_negotiation_terms",
                title="Review custom commercial terms internally",
                description="The buyer is asking for custom payment or scope terms. Pull in Rakesh and finance, then respond with a controlled proposal revision rather than editing terms ad hoc.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        procurement_signal = (
            (ai_interpretation is not None and ai_interpretation.legal_state == "active_review")
            or _text_contains_any(
                recent_texts,
                ["procurement", "vendor onboarding", "vendor form", "security questionnaire", "company info", "w-9", "w9", "insurance certificate"],
            )
        )
        if procurement_signal and not await _recent_system_task_exists(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_commercial_negotiation_procurement",
            days=7,
        ):
            created_keys.add("deal_commercial_negotiation_procurement")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_commercial_negotiation_procurement",
                title="Coordinate procurement and vendor docs",
                description="Procurement or vendor onboarding is entering the thread. Pull in the right internal owner and send the vendor onboarding package without derailing the core commercial discussion.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        commercials_agreed = _text_contains_any(
            recent_texts,
            ["commercials agreed", "pricing works", "approved the pricing", "we are aligned on pricing", "looks good from our side", "ready for contract", "send the msa", "move to contract"],
        )
        if commercials_agreed:
            created_keys.add("deal_move_msa_review")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_move_msa_review",
                title="Move deal to MSA Review",
                description="The commercial discussion sounds aligned and the thread is ready for contract motion. Move the deal into MSA review so legal and implementation planning are tracked in the right place.",
                priority="high",
                source="playbook",
                recommended_action="move_deal_stage",
                action_payload={"deal_id": str(deal.id), "stage": "msa_review"},
                assigned_role="ae",
            )
            created_keys.add("deal_commercial_negotiation_workshop")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_commercial_negotiation_workshop",
                title="Schedule the implementation workshop",
                description="Commercials look agreed. Lock in the workshop or implementation alignment session quickly so the deal does not sit between pricing and execution.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        legal_early = _text_contains_any(
            recent_texts,
            ["legal review", "msa", "master services agreement", "redline", "our legal team", "send the contract"],
        )
        if legal_early and not commercials_agreed:
            created_keys.add("deal_commercial_negotiation_sequence_legal")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_commercial_negotiation_sequence_legal",
                title="Sequence legal behind pricing confirmation",
                description="Legal is surfacing before commercials are clearly settled. Pull in the right internal owner and reply with sequencing guidance so the deal does not jump into full legal motion too early.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )
        return

    if deal.stage in {"msa_review", "workshop"}:
        meetings = (
            await session.execute(
                select(Meeting)
                .where(Meeting.deal_id == deal.id)
                .order_by(Meeting.scheduled_at.desc(), Meeting.updated_at.desc())
            )
        ).scalars().all()
        activity_texts = [text for text in (_activity_signal_text(activity) for activity in activity_rows) if text]
        workshop_scheduled = next(
            (
                meeting
                for meeting in meetings
                if meeting.scheduled_at is not None
                and meeting.scheduled_at >= now
                and _contains_any((meeting.title or "").lower(), ["workshop", "working session", "implementation", "kickoff"])
            ),
            None,
        )
        if deal.stage_entered_at and deal.stage_entered_at <= now - timedelta(days=7) and not workshop_scheduled:
            created_keys.add("deal_workshop_msa_schedule")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_workshop_msa_schedule",
                title="Schedule the workshop and prep the deck",
                description="This deal has been sitting in workshop or MSA motion for 7+ days without a visible workshop on the calendar. Schedule it and prepare the workshop deck or SOW draft so the stage keeps moving.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        redline_received = _latest_matching_activity(
            activity_rows,
            lambda activity: _contains_any(_activity_signal_text(activity), ["redline", "redlined", "track changes", "markup", "msa comments"]),
        )
        if redline_received:
            created_keys.add("deal_workshop_msa_redline")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_workshop_msa_redline",
                title="Run the MSA redline cycle",
                description="Beacon sees MSA redlines in the thread. Pull in legal, keep a clean decision log, and turn the next version quickly so paper process does not stall.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if _text_contains_any(
            recent_texts,
            ["security questionnaire", "infosec questionnaire", "security review", "vendor questionnaire", "soc2", "infosec"],
        ) and not await _recent_system_task_exists(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_workshop_msa_security",
            days=7,
        ):
            created_keys.add("deal_workshop_msa_security")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_workshop_msa_security",
                title="Send the security and infosec pack",
                description="Security or infosec requests are active on this deal. Pull in the right internal owner and send the security pack so the paper process keeps moving.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if redline_received and redline_received.created_at <= now - timedelta(days=7):
            newer_after_redline = _latest_matching_activity(
                activity_rows,
                lambda activity: activity.created_at > redline_received.created_at and activity.type in {"email", "call", "meeting"},
            )
            if newer_after_redline is None:
                created_keys.add("deal_workshop_msa_stuck")
                await _upsert_system_task(
                    session,
                    entity_type="deal",
                    entity_id=deal.id,
                    system_key="deal_workshop_msa_stuck",
                    title="Escalate the stuck legal redlines",
                    description="The redline cycle appears stalled for 7+ days. Pull in Rakesh and get the legal call scheduled before the deal sits in contract limbo.",
                    priority="high",
                    source="playbook",
                    recommended_action=None,
                    action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                    assigned_role="ae",
                )

        workshop_done_signal = _latest_matching_activity(
            activity_rows,
            lambda activity: activity.type in {"meeting", "transcript"}
            and _contains_any(_activity_signal_text(activity), ["workshop", "working session"])
            and activity.created_at >= now - timedelta(hours=48),
        )
        if workshop_done_signal and not _text_contains_any(activity_texts, ["outcomes", "decision log", "sow v2", "recap", "next steps"]):
            created_keys.add("deal_workshop_msa_outcomes")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_workshop_msa_outcomes",
                title="Document the workshop outcomes",
                description="The workshop appears to have happened, but Beacon does not see documented outcomes or a recap yet. Capture the decisions and send the next version of the scope artifact quickly.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if _text_contains_any(recent_texts, ["pricing", "price", "discount", "commercials", "proposal", "quote"]):
            created_keys.add("deal_workshop_msa_hold_pricing")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_workshop_msa_hold_pricing",
                title="Hold firm on reopened pricing",
                description="The buyer is trying to reopen pricing while the deal is in workshop or MSA motion. Reply with the agreed commercial frame and pull in Rakesh only if the ask is materially changing the deal.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )
        return

    if deal.stage == "closed_won":
        meetings = (
            await session.execute(
                select(Meeting)
                .where(Meeting.deal_id == deal.id)
                .order_by(Meeting.scheduled_at.desc(), Meeting.updated_at.desc())
            )
        ).scalars().all()
        activity_texts = [text for text in (_activity_signal_text(activity) for activity in activity_rows) if text]
        kickoff_scheduled = next(
            (
                meeting
                for meeting in meetings
                if meeting.scheduled_at is not None
                and meeting.scheduled_at >= now
                and _contains_any((meeting.title or "").lower(), ["kickoff", "implementation", "onboarding"])
            ),
            None,
        )
        handoff_signal = _text_contains_any(activity_texts, ["handoff", "implementation owner", "delivery intro", "internal handoff"])
        if deal.stage_entered_at and deal.stage_entered_at <= now - timedelta(hours=48) and not handoff_signal:
            created_keys.add("deal_closed_won_handoff")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_closed_won_handoff",
                title="Schedule the internal handoff",
                description="The deal is closed won, but Beacon does not yet see a clear internal handoff motion. Set the handoff quickly and prepare the handoff artifact so delivery and finance are aligned.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if handoff_signal and not kickoff_scheduled and deal.stage_entered_at and deal.stage_entered_at <= now - timedelta(hours=72):
            created_keys.add("deal_closed_won_kickoff")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_closed_won_kickoff",
                title="Schedule the client kickoff",
                description="The internal handoff is underway, but Beacon does not see the client kickoff on the calendar yet. Get delivery and the customer booked so the win turns into implementation momentum.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        go_live_signal = _latest_matching_activity(
            activity_rows,
            lambda activity: _contains_any(_activity_signal_text(activity), ["go-live", "go live", "went live", "live now", "launch date confirmed"]),
        )
        if go_live_signal:
            created_keys.add("deal_closed_won_invoice")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_closed_won_invoice",
                title="Coordinate invoicing after go-live",
                description="The customer appears to be live. Coordinate with finance and update the CRM notes so invoicing and implementation tracking stay accurate.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

            if go_live_signal.created_at <= now - timedelta(days=14):
                created_keys.add("deal_closed_won_reference")
                await _upsert_system_task(
                    session,
                    entity_type="deal",
                    entity_id=deal.id,
                    system_key="deal_closed_won_reference",
                    title="Ask for the reference or case study",
                    description="It has been at least two weeks since the customer appears to have gone live. If the rollout is healthy, this is a good time to ask for a reference or case study conversation.",
                    priority="medium",
                    source="playbook",
                    recommended_action=None,
                    action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                    assigned_role="ae",
                )

        if _text_contains_any(recent_texts, ["clickup account", "create the client account", "clickup space", "clickup workspace"]):
            created_keys.add("deal_closed_won_clickup")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_closed_won_clickup",
                title="Create the ClickUp client account",
                description="The delivery tooling setup is still outstanding. Create the ClickUp client account and populate the basics so the post-sale handoff is complete.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )
        return

    if deal.stage == "churned":
        activity_texts = [text for text in (_activity_signal_text(activity) for activity in activity_rows) if text]
        reason_captured = _contains_any(
            " ".join(filter(None, [deal.description, deal.next_step])),
            ["churn", "cancelled", "reason", "renewal", "what we could have done"],
        ) or any(
            activity.type == "note"
            and (deal.stage_entered_at is None or activity.created_at >= deal.stage_entered_at)
            for activity in activity_rows
        )
        if deal.stage_entered_at and deal.stage_entered_at <= now - timedelta(hours=72) and not reason_captured:
            created_keys.add("deal_churned_exit_notes")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_churned_exit_notes",
                title="Capture the churn reason and learnings",
                description="The deal is marked churned, but Beacon does not yet see clear exit notes. Capture the churn reason, what Beacon could have done better, and anything worth preserving for a future return.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if _text_contains_any(activity_texts, ["moved to", "joined", "new company", "at my new company"]):
            created_keys.add("deal_churned_champion_move")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_churned_champion_move",
                title="Track the champion at the new company",
                description="A champion appears to have moved companies. Research the new company and re-engage there rather than spending more effort on the churned account.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if deal.stage_entered_at and deal.stage_entered_at <= now - timedelta(days=180):
            created_keys.add("deal_churned_nurture")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_churned_nurture",
                title="Restart nurture for the churned account",
                description="It has been roughly six months since this customer churned. If the relationship is still warm enough, restart a light nurture motion with product updates or a value reminder.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )
        return

    if deal.stage == "not_a_fit":
        activity_texts = [text for text in (_activity_signal_text(activity) for activity in activity_rows) if text]
        reason_captured = bool((deal.description or "").strip() or (deal.next_step or "").strip()) or any(
            activity.type == "note"
            and (deal.stage_entered_at is None or activity.created_at >= deal.stage_entered_at)
            for activity in activity_rows
        )
        if deal.stage_entered_at and deal.stage_entered_at <= now - timedelta(hours=24) and not reason_captured:
            created_keys.add("deal_not_fit_reason")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_not_fit_reason",
                title="Capture the disqualification reason",
                description="This deal is marked not a fit, but Beacon does not yet see a clear reason captured. Add the reason code and short notes so the pipeline stays honest and reusable.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if _text_contains_any(activity_texts, ["pivoted", "new use case", "different use case", "new initiative", "new priority"]):
            created_keys.add("deal_not_fit_reopen")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_not_fit_reopen",
                title="Re-evaluate fit and reopen if warranted",
                description="The buyer situation appears to have changed. Re-evaluate the ICP fit, and if Beacon is now relevant, move the deal back to Reprospect instead of leaving it buried as not-a-fit.",
                priority="high",
                source="playbook",
                recommended_action="move_deal_stage",
                action_payload={"deal_id": str(deal.id), "stage": "reprospect"},
                assigned_role="ae",
            )
        return

    if deal.stage == "cold":
        activity_texts = [text for text in (_activity_signal_text(activity) for activity in activity_rows) if text]
        trigger_signal = _text_contains_any(
            activity_texts,
            ["funding", "series a", "series b", "rfp", "new cto", "new cio", "new exec", "product launch", "hiring", "expansion"],
        )
        if trigger_signal:
            created_keys.add("deal_cold_trigger_reopen")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_cold_trigger_reopen",
                title="Reopen the cold deal on the trigger",
                description="A new trigger event is showing up on this cold deal. Move it back to Reprospect and use the trigger as the re-engagement hook instead of leaving it dormant.",
                priority="high",
                source="playbook",
                recommended_action="move_deal_stage",
                action_payload={"deal_id": str(deal.id), "stage": "reprospect"},
                assigned_role="sdr",
            )
            created_keys.add("deal_cold_trigger_reengage")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_cold_trigger_reengage",
                title="Re-engage with the trigger-specific hook",
                description="The account has a fresh signal. Send a concise re-engagement rooted in the new event instead of recycling the old outreach thread.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="sdr",
            )

        last_touch = deal.last_activity_at or deal.stage_entered_at
        if last_touch and last_touch <= now - timedelta(days=30):
            created_keys.add("deal_cold_monthly_reengage")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_cold_monthly_reengage",
                title="Send the monthly cold re-engagement",
                description="This cold deal has had no activity for 30+ days. If it is still worth touching, send a low-frequency break-up or value-add touch rather than letting it silently decay.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="sdr",
            )

        recent_external = _latest_matching_activity(
            activity_rows,
            lambda activity: activity.created_at >= now - timedelta(days=2)
            and activity.type in {"email", "call", "meeting"}
            and _email_domain(activity.email_from) != internal_domain,
        )
        if recent_external:
            target_stage = "demo_scheduled" if _contains_any(_activity_signal_text(recent_external), ["demo", "calendar invite", "schedule", "meeting"]) else "reprospect"
            created_keys.add("deal_cold_reopen_engagement")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_cold_reopen_engagement",
                title="Reopen the cold deal on new engagement",
                description="The prospect has engaged again. Move the deal back into active motion so the team can respond at the right stage instead of treating it as still cold.",
                priority="high",
                source="playbook",
                recommended_action="move_deal_stage",
                action_payload={"deal_id": str(deal.id), "stage": target_stage},
                assigned_role="ae",
            )
        return

    if deal.stage == "closed_lost":
        activity_texts = [text for text in (_activity_signal_text(activity) for activity in activity_rows) if text]
        reason_captured = bool((deal.description or "").strip() or (deal.next_step or "").strip()) or any(
            activity.type == "note"
            and (deal.stage_entered_at is None or activity.created_at >= deal.stage_entered_at)
            for activity in activity_rows
        )
        if deal.stage_entered_at and deal.stage_entered_at <= now - timedelta(hours=48) and not reason_captured:
            created_keys.add("deal_closed_lost_reason")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_closed_lost_reason",
                title="Capture the loss reason",
                description="This deal is closed lost, but Beacon does not yet see the loss reason documented. Capture the reason and the revisit logic so the outcome is reusable instead of anecdotal.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        competitor = next((name for text in activity_texts for name in [_detect_competitor_signal(text)] if name), None)
        if competitor:
            created_keys.add("deal_closed_lost_competitor")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_closed_lost_competitor",
                title="Run the competitive loss debrief",
                description=f"The deal appears to have gone to {competitor}. Capture the competitive intel and pull in Rakesh or product so the team learns from the loss rather than just logging it.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "competitor": competitor, "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        future_fit = not _contains_any(" ".join(filter(None, [deal.description, deal.next_step])), ["never fit", "not future fit", "bad fit"])
        if deal.stage_entered_at and deal.stage_entered_at <= now - timedelta(days=180) and future_fit:
            created_keys.add("deal_closed_lost_nurture")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_closed_lost_nurture",
                title="Restart the closed-lost nurture motion",
                description="It has been roughly six months since the deal was lost. If the account is still future-fit, restart a low-intensity nurture with what is new and why the timing may now be different.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if _text_contains_any(activity_texts, ["moved to", "joined", "new company", "at my new company"]):
            created_keys.add("deal_closed_lost_champion_move")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_closed_lost_champion_move",
                title="Re-engage the champion at the new company",
                description="A former champion appears to have moved companies. Follow the person to the new account instead of trying to revive the already-lost one.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )
        return

    if deal.stage == "on_hold":
        activity_texts = [text for text in (_activity_signal_text(activity) for activity in activity_rows) if text]
        if deal.close_date_est and deal.close_date_est <= now.date():
            created_keys.add("deal_on_hold_revisit_due")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_on_hold_revisit_due",
                title="Revisit the paused deal",
                description="The revisit date for this on-hold deal appears to have arrived. Refresh the context, check for new triggers, and send the right low-pressure follow-up.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if _text_contains_any(
            activity_texts,
            ["funding", "series a", "series b", "rfp", "new cto", "new cio", "new exec", "product launch", "hiring", "expansion"],
        ):
            created_keys.add("deal_on_hold_trigger_reopen")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_on_hold_trigger_reopen",
                title="Reopen the paused deal on the trigger",
                description="A new trigger event is showing up on this on-hold deal. Move it back into active motion and follow up while the trigger is still relevant.",
                priority="high",
                source="playbook",
                recommended_action="move_deal_stage",
                action_payload={"deal_id": str(deal.id), "stage": "reprospect"},
                assigned_role="ae",
            )

        revisit_documented = bool(deal.close_date_est) or _contains_any(
            " ".join(filter(None, [deal.description, deal.next_step])),
            ["revisit", "circle back", "check back", "next review"],
        )
        if not revisit_documented:
            created_keys.add("deal_on_hold_revisit_date")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_on_hold_revisit_date",
                title="Set the revisit date and pause reason",
                description="This deal is on hold, but Beacon does not see a clear revisit date yet. Set the date and short pause reason so the pause is intentional rather than indefinite.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )
        return

    if deal.stage == "nurture":
        activity_texts = [text for text in (_activity_signal_text(activity) for activity in activity_rows) if text]
        last_touch = deal.last_activity_at or deal.stage_entered_at
        if last_touch and last_touch <= now - timedelta(days=90):
            created_keys.add("deal_nurture_quarterly")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_nurture_quarterly",
                title="Send the quarterly nurture touch",
                description="This nurture-stage deal has gone roughly a quarter without a touch. Send a light asset such as a case study, product update, or relevant insight rather than a hard sales nudge.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        trigger_signal = _text_contains_any(
            activity_texts,
            ["funding", "series a", "series b", "rfp", "new cto", "new cio", "new exec", "product launch", "hiring", "expansion"],
        )
        if trigger_signal:
            created_keys.add("deal_nurture_trigger_reopen")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_nurture_trigger_reopen",
                title="Reopen nurture on the trigger event",
                description="A real trigger event is appearing on this nurture deal. Move it back to Reprospect and restart an active outreach motion while the timing looks better.",
                priority="high",
                source="playbook",
                recommended_action="move_deal_stage",
                action_payload={"deal_id": str(deal.id), "stage": "reprospect"},
                assigned_role="ae",
            )

        recent_external = _latest_matching_activity(
            activity_rows,
            lambda activity: activity.created_at >= now - timedelta(days=2)
            and activity.type in {"email", "call", "meeting"}
            and _email_domain(activity.email_from) != internal_domain,
        )
        if recent_external:
            target_stage = "demo_scheduled" if _contains_any(_activity_signal_text(recent_external), ["demo", "calendar invite", "schedule", "meeting"]) else "reprospect"
            created_keys.add("deal_nurture_reopen_reply")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_nurture_reopen_reply",
                title="Reopen nurture on the reply",
                description="The prospect replied to a nurture motion. Move the deal back into active pipeline work so the team responds with the right urgency.",
                priority="high",
                source="playbook",
                recommended_action="move_deal_stage",
                action_payload={"deal_id": str(deal.id), "stage": target_stage},
                assigned_role="ae",
            )
        return

    if deal.stage not in {"demo_scheduled", "demo_done"}:
        return

    meetings = (
        await session.execute(
            select(Meeting)
            .where(Meeting.deal_id == deal.id)
            .order_by(Meeting.scheduled_at.desc(), Meeting.updated_at.desc())
        )
    ).scalars().all()
    upcoming_demo = next(
        (
            meeting
            for meeting in meetings
            if meeting.meeting_type == "demo"
            and meeting.status == "scheduled"
            and meeting.scheduled_at is not None
            and meeting.scheduled_at >= now
            and meeting.scheduled_at <= now + timedelta(hours=48)
        ),
        None,
    )
    latest_completed_demo = next(
        (
            meeting
            for meeting in meetings
            if meeting.meeting_type == "demo"
            and meeting.status == "completed"
            and meeting.scheduled_at is not None
        ),
        None,
    )

    if deal.stage == "demo_scheduled":
        if upcoming_demo and not (upcoming_demo.pre_brief or upcoming_demo.research_data):
            created_keys.add("deal_demo_prep")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_demo_prep",
                title="Prep for the upcoming demo",
                description="A demo is scheduled inside 48 hours and Beacon does not see a pre-brief or prep work yet. Research the account, tighten the agenda, and make sure the demo is tailored.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "meeting_id": str(upcoming_demo.id) if upcoming_demo.id else None},
                assigned_role="ae",
            )

        if upcoming_demo:
            attendee_payloads = upcoming_demo.attendees if isinstance(upcoming_demo.attendees, list) else []
            linked_emails = {(contact.email or "").strip().lower() for contact in linked_contacts if contact.email}
            has_new_attendees = any(
                isinstance(attendee, dict)
                and str(attendee.get("email") or "").strip().lower()
                and str(attendee.get("email") or "").strip().lower() not in linked_emails
                for attendee in attendee_payloads
            )
            if has_new_attendees:
                created_keys.add("deal_demo_attendees")
                await _upsert_system_task(
                    session,
                    entity_type="deal",
                    entity_id=deal.id,
                    system_key="deal_demo_attendees",
                    title="Research the newly added demo attendees",
                    description="The scheduled demo has attendees who are not yet mapped on the deal. Add the stakeholders and do a quick role-based read before the meeting.",
                    priority="medium",
                    source="playbook",
                    recommended_action=None,
                    action_payload={"deal_id": str(deal.id), "meeting_id": str(upcoming_demo.id) if upcoming_demo.id else None},
                    assigned_role="ae",
                )

        if _text_contains_any(recent_texts, ["send the deck", "send the deck before", "send the one pager", "1-pager", "one-pager", "pre-read", "before the demo", "architecture diagram"]) and not await _recent_system_task_exists(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_demo_send_asset",
            days=7,
        ):
            created_keys.add("deal_demo_send_asset")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_demo_send_asset",
                title="Send the requested pre-demo asset",
                description="The buyer asked for pre-demo material. Send the deck, one-pager, or short pre-read so the meeting starts with the right context.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if _text_contains_any(recent_texts, ["reschedul", "move the demo", "push the demo", "can we do thursday", "can we move", "new time"]):
            created_keys.add("deal_demo_reschedule")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_demo_reschedule",
                title="Reschedule the demo and update CRM",
                description="The buyer asked to move the demo. Lock the new time quickly and update the meeting date so prep and follow-up stay aligned.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )
        return

    if latest_completed_demo:
        demo_moment = latest_completed_demo.scheduled_at
        if (
            demo_moment is not None
            and demo_moment >= now - timedelta(hours=24)
            and not any([latest_completed_demo.raw_notes, latest_completed_demo.ai_summary, latest_completed_demo.next_steps])
        ):
            created_keys.add("deal_demo_notes")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_demo_notes",
                title="Log demo notes and next step",
                description="The demo finished recently, but Beacon does not see notes or a committed next step on the meeting yet. Capture the outcome while it is still fresh.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "meeting_id": str(latest_completed_demo.id) if latest_completed_demo.id else None},
                assigned_role="ae",
            )

        if (
            demo_moment is not None
            and demo_moment >= now - timedelta(hours=24)
            and not _has_internal_email_after(activity_rows, after=demo_moment, internal_domain=internal_domain)
            and "deal_send_meeting_recap" not in created_keys
        ):
            created_keys.add("deal_demo_follow_up")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_demo_follow_up",
                title="Send the post-demo recap",
                description="The demo is done and Beacon does not see a recap or next-step email yet. Send the recap within 24 hours so the qualification motion stays crisp.",
                priority="high",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "meeting_id": str(latest_completed_demo.id) if latest_completed_demo.id else None},
                assigned_role="ae",
            )

        if demo_moment is not None and _text_contains_any(recent_texts, ["tech faq", "architecture diagram", "architecture", "security doc", "technical faq"]) and not await _recent_system_task_exists(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_demo_tech_asset",
            days=7,
        ):
            created_keys.add("deal_demo_tech_asset")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_demo_tech_asset",
                title="Send the requested technical asset",
                description="The buyer asked for technical follow-up material after the demo. Send the FAQ, architecture diagram, or the right technical artifact while the context is still active.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

        if (
            demo_moment is not None
            and demo_moment <= now - timedelta(days=5)
            and not _has_external_email_after(activity_rows, after=demo_moment, internal_domain=internal_domain)
        ):
            created_keys.add("deal_demo_nudge")
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key="deal_demo_nudge",
                title="Nudge the buyer after the demo",
                description="It has been 5+ days since the demo with no buyer response. Send a light next-step nudge rather than jumping straight to commercial tasks.",
                priority="medium",
                source="playbook",
                recommended_action=None,
                action_payload={"deal_id": str(deal.id), "playbook_stage": deal.stage},
                assigned_role="ae",
            )

    if _text_contains_any(recent_texts, ["next year budget", "budget next year", "no budget this year", "6 months", "six months", "more than 6 months", "not this quarter"]):
        created_keys.add("deal_move_nurture")
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_move_nurture",
            title="Move deal to Nurture",
            description="The buyer signaled a long timeline or no near-term budget. Move the deal to Nurture so the pipeline reflects future-fit reality.",
            priority="high",
            source="playbook",
            recommended_action="move_deal_stage",
            action_payload={"deal_id": str(deal.id), "stage": "nurture"},
            assigned_role="ae",
        )
async def _refresh_deal_tasks(session: AsyncSession, entity_id: UUID) -> None:
    deal = await session.get(Deal, entity_id)
    if not deal:
        return

    activity_rows = (
        await session.execute(
            select(Activity)
            .where(
                Activity.deal_id == deal.id,
                Activity.type.in_(["email", "call", "note", "transcript", "meeting"]),
            )
            .order_by(Activity.created_at.desc())
            .limit(25)
        )
    ).scalars().all()

    linked_contacts = (
        await session.execute(
            select(Contact)
            .join(DealContact, DealContact.contact_id == Contact.id)
            .where(DealContact.deal_id == deal.id)
        )
    ).scalars().all()
    deal.stakeholder_count = len({contact.id for contact in linked_contacts if contact.id})
    deal.last_activity_at = max((activity.created_at for activity in activity_rows), default=deal.last_activity_at)
    ai_interpretation = await interpret_deal_activity(deal, activity_rows, linked_contacts)

    created_keys: set[str] = set()
    legal_signal = ai_interpretation.legal_state == "active_review"
    pricing_pushback = ai_interpretation.commercial_state == "pushback"
    legal_signal_source = "ai_interpreter" if legal_signal else "system"
    pricing_pushback_source = "ai_interpreter" if pricing_pushback else "system"
    competitor_name: str | None = None
    competitor_source = "system"
    reschedule_mentions = 2 if ai_interpretation.reschedule_state == "repeated" else 1 if ai_interpretation.reschedule_state == "single" else 0
    pricing_request = ai_interpretation.commercial_state in {"directional", "pricing_request", "agreed"}
    pricing_request_source = "ai_interpreter" if pricing_request else "system"
    workshop_signal = ai_interpretation.workshop_state == "scheduling"
    workshop_signal_source = "ai_interpreter" if workshop_signal else "system"
    latest_answered_call = next((activity for activity in activity_rows if activity.source == "aircall" and activity.call_outcome == "answered"), None)
    latest_missed_call = next((activity for activity in activity_rows if activity.source == "aircall" and activity.call_outcome == "missed"), None)
    latest_voicemail = next((activity for activity in activity_rows if activity.source == "aircall" and activity.call_outcome == "voicemail"), None)
    latest_tldv_meeting = next((activity for activity in activity_rows if activity.source == "tldv" and activity.type in ["meeting", "transcript"]), None)
    latest_answered_call_has_intelligence = bool(
        latest_answered_call
        and any(
            activity.call_id
            and latest_answered_call.call_id
            and activity.call_id == latest_answered_call.call_id
            and _activity_has_conversation_intelligence(activity)
            for activity in activity_rows
        )
    )
    if ai_interpretation.stage_cue:
        suggestion = _deal_signal_task_from_signal(
            ActivitySignal(normalized_text="", stage_cue=ai_interpretation.stage_cue),
            deal.stage,
        )
        if suggestion and stage_allows_stage_move(deal.stage, suggestion[0]):
            target_stage, title, description = suggestion
            system_key = f"deal_move_{target_stage}"
            created_keys.add(system_key)
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key=system_key,
                title=title,
                description=description,
                priority="high" if target_stage in {"poc_agreed", "msa_review"} else "medium",
                source="ai_interpreter",
                recommended_action="move_deal_stage",
                action_payload={
                    "deal_id": str(deal.id),
                    "stage": target_stage,
                    "activity_id": ai_interpretation.evidence_activity_id,
                    "confidence": ai_interpretation.stage_confidence,
                },
                assigned_role="ae",
            )
    latest_email_activity_ids_by_thread: set[UUID] = set()
    seen_email_threads: set[str] = set()
    for activity in activity_rows:
        if activity.type != "email" or not activity.id:
            continue
        metadata = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
        thread_id = str(metadata.get("gmail_thread_id") or "").strip()
        if not thread_id or thread_id in seen_email_threads:
            continue
        seen_email_threads.add(thread_id)
        latest_email_activity_ids_by_thread.add(activity.id)

    for activity in activity_rows:
        metadata = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
        thread_id = str(metadata.get("gmail_thread_id") or "").strip()
        if activity.type == "email" and thread_id and activity.id not in latest_email_activity_ids_by_thread:
            continue

        text = _activity_signal_text(activity)
        if not text:
            continue
        detected_competitor = _detect_competitor_signal(text)
        if not competitor_name and detected_competitor:
            competitor_name = detected_competitor
            competitor_source = activity.source or competitor_source

        for item in metadata.get("suggested_existing_contacts") or []:
            contact_id = str(item.get("contact_id") or "").strip()
            email = str(item.get("email") or "").strip().lower()
            if not contact_id:
                continue
            system_key = f"deal_attach_contact_{contact_id}"
            if system_key in created_keys:
                continue
            created_keys.add(system_key)
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key=system_key,
                title="Attach stakeholder already seen in email",
                description=f"{email} is already in Beacon and appeared on a buyer thread. Attach them to this deal to keep stakeholder mapping current.",
                priority="medium",
                source="gmail_sync",
                recommended_action="attach_contact_to_deal",
                action_payload={
                    "deal_id": str(deal.id),
                    "contact_id": contact_id,
                    "activity_id": str(activity.id),
                },
                assigned_role="ae",
            )

        for participant in metadata.get("suggested_new_participants") or []:
            email = str(participant.get("email") or "").strip().lower()
            if not email:
                continue
            system_key = f"deal_add_contact_{email.replace('@', '_at_').replace('.', '_dot_')}"
            if system_key in created_keys:
                continue
            created_keys.add(system_key)
            await _upsert_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key=system_key,
                title="Add new stakeholder from buyer thread",
                description=f"{participant.get('display_name') or email} appeared on a synced email thread. Create the contact and attach them to this deal so the team keeps full stakeholder coverage.",
                priority="high",
                source="gmail_sync",
                recommended_action="create_contact_and_attach_to_deal",
                action_payload={
                    "deal_id": str(deal.id),
                    "company_id": str(deal.company_id) if deal.company_id else None,
                    "email": email,
                    "first_name": participant.get("first_name"),
                    "last_name": participant.get("last_name"),
                    "display_name": participant.get("display_name"),
                    "activity_id": str(activity.id),
                },
                assigned_role="ae",
            )

    if pricing_request and stage_allows_system_key(deal.stage, "deal_send_pricing") and _stage_allows_pricing_package(deal.stage) and not await _recent_system_task_exists(
        session,
        entity_type="deal",
        entity_id=deal.id,
        system_key="deal_send_pricing",
        days=1,
    ):
        created_keys.add("deal_send_pricing")
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_send_pricing",
            title="Send pricing or commercial package",
            description="Recent buyer language suggests they want pricing, a quote, or commercial terms. Send the commercial package and keep the thread moving.",
            priority="high",
            source=pricing_request_source,
            recommended_action="send_pricing_package",
            action_payload={"deal_id": str(deal.id), "next_step": "send_pricing"},
            assigned_role="ae",
        )

    if workshop_signal and not _stage_reached(deal.stage, "workshop") and stage_allows_system_key(deal.stage, "deal_book_workshop") and _stage_allows_workshop_booking(deal.stage) and not await _recent_system_task_exists(
        session,
        entity_type="deal",
        entity_id=deal.id,
        system_key="deal_book_workshop",
        days=1,
    ):
        created_keys.add("deal_book_workshop")
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_book_workshop",
            title="Book the technical workshop",
            description="Buyer activity points to a workshop or working session. Lock in the session and make sure the right technical stakeholders are present.",
            priority="medium",
            source=workshop_signal_source,
            recommended_action="book_workshop_session",
            action_payload={"deal_id": str(deal.id), "next_step": "book_workshop"},
            assigned_role="ae",
        )

    if legal_signal and not _has_security_stakeholder(linked_contacts) and stage_allows_system_key(deal.stage, "deal_add_security_contact"):
        created_keys.add("deal_add_security_contact")
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_add_security_contact",
            title="Add a legal or security stakeholder",
            description="The thread mentions legal, procurement, or security review, but this deal does not yet show a clear owner for that motion. Add the right stakeholder before the paper process slows down.",
            priority="high",
            source=legal_signal_source,
            recommended_action=None,
            action_payload={"deal_id": str(deal.id), "next_step": "add_security_contact"},
            assigned_role="ae",
        )

    if latest_missed_call and latest_missed_call.created_at >= datetime.utcnow() - timedelta(days=2) and (
        not latest_answered_call or latest_answered_call.created_at < latest_missed_call.created_at
    ):
        created_keys.add("deal_retry_call")
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_retry_call",
            title="Retry the missed call",
            description="Aircall logged a missed buyer call on this deal. Retry the call or send a short follow-up before the thread goes cold.",
            priority="high",
            source="aircall",
            recommended_action="retry_deal_call",
            action_payload={"deal_id": str(deal.id), "next_step": "retry_call"},
            assigned_role="ae",
        )

    if latest_voicemail and latest_voicemail.created_at >= datetime.utcnow() - timedelta(days=2) and (
        not latest_answered_call or latest_answered_call.created_at < latest_voicemail.created_at
    ):
        created_keys.add("deal_voicemail_follow_up")
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_voicemail_follow_up",
            title="Follow up on the voicemail",
            description="A voicemail was left on this deal. Follow up while the call attempt is still fresh and reference the message if needed.",
            priority="medium",
            source="aircall",
            recommended_action="follow_up_deal_voicemail",
            action_payload={"deal_id": str(deal.id), "next_step": "follow_up_voicemail"},
            assigned_role="ae",
        )

    if latest_answered_call and latest_answered_call.created_at >= datetime.utcnow() - timedelta(days=3) and (
        (latest_answered_call.call_duration or 0) >= 90
        or latest_answered_call_has_intelligence
    ):
        created_keys.add("deal_send_call_recap")
        recap_description = "Beacon sees a connected Aircall conversation on this deal. Send a recap, confirm the next step, and capture momentum while the call is fresh."
        if latest_answered_call_has_intelligence:
            recap_description = (
                "Beacon pulled Aircall call intelligence for this deal. "
                "Send a recap, confirm the buyer's next step, and keep the momentum explicit."
            )
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_send_call_recap",
            title="Send a post-call recap",
            description=recap_description,
            priority="medium",
            source="aircall",
            recommended_action="send_deal_call_recap",
            action_payload={"deal_id": str(deal.id), "next_step": "send_call_recap"},
            assigned_role="ae",
        )

    if latest_tldv_meeting and latest_tldv_meeting.created_at >= datetime.utcnow() - timedelta(days=4):
        created_keys.add("deal_send_meeting_recap")
        tldv_metadata = latest_tldv_meeting.event_metadata if isinstance(latest_tldv_meeting.event_metadata, dict) else {}
        follow_up_email_draft = str(tldv_metadata.get("follow_up_email_draft") or "").strip() or None
        meeting_summary = str(tldv_metadata.get("summary") or latest_tldv_meeting.ai_summary or "").strip() or None
        action_items = [
            str(item).strip()
            for item in (tldv_metadata.get("action_items") or [])
            if isinstance(item, str) and str(item).strip()
        ]
        meeting_title = str(tldv_metadata.get("meeting_title") or "").strip() or None
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_send_meeting_recap",
            title="Send post-meeting follow-up",
            description=(
                "tl;dv shows a recent customer meeting on this deal. "
                "Beacon prepared a follow-up draft from the meeting summary and transcript so the rep can send it faster."
            ),
            priority="high",
            source="tldv",
            recommended_action="send_meeting_follow_up",
            action_payload={
                "deal_id": str(deal.id),
                "next_step": "send_meeting_follow_up",
                "meeting_title": meeting_title,
                "meeting_summary": meeting_summary,
                "action_items": action_items,
                "follow_up_email_draft": follow_up_email_draft,
            },
            assigned_role="ae",
        )

    internal_domain = _email_domain(settings.GMAIL_SHARED_INBOX or "zippy@beacon.li")
    recent_internal = next((activity for activity in activity_rows if _email_domain(activity.email_from) == internal_domain), None)
    recent_external = next((activity for activity in activity_rows if _email_domain(activity.email_from) != internal_domain), None)
    if recent_internal and (
        recent_external is None or recent_external.created_at < recent_internal.created_at
    ) and recent_internal.created_at < datetime.utcnow() - timedelta(days=5) and stage_allows_system_key(deal.stage, "deal_follow_up"):
        created_keys.add("deal_follow_up")
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_follow_up",
            title="Follow up on the buyer thread",
            description="Beacon sees an outbound email with no newer buyer reply for 5+ days. Send a follow-up so the deal does not stall quietly.",
            priority="high",
            source="system",
            recommended_action="follow_up_buyer_thread",
            action_payload={"deal_id": str(deal.id), "next_step": "follow_up"},
            assigned_role="ae",
        )

    if pricing_pushback and stage_allows_system_key(deal.stage, "deal_pricing_pushback"):
        created_keys.add("deal_pricing_pushback")
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_pricing_pushback",
            title="Handle pricing pushback",
            description="Buyer language indicates budget or pricing resistance. Adjust the commercial narrative or bring stronger ROI proof before the deal cools.",
            priority="high",
            source=pricing_pushback_source,
            recommended_action=None,
            action_payload={"deal_id": str(deal.id), "next_step": "handle_pricing_pushback"},
            assigned_role="ae",
        )

    if competitor_name and stage_allows_system_key(deal.stage, "deal_competitor_risk"):
        created_keys.add("deal_competitor_risk")
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_competitor_risk",
            title="Prepare competitor response",
            description=f"Buyer activity mentions {competitor_name}. Queue the right battlecard or differentiation points before the next reply.",
            priority="medium",
            source=competitor_source,
            recommended_action=None,
            action_payload={"deal_id": str(deal.id), "competitor": competitor_name},
            assigned_role="ae",
        )

    await _apply_stage_playbook_tasks(
        session,
        deal,
        activity_rows,
        linked_contacts,
        ai_interpretation=ai_interpretation,
        created_keys=created_keys,
    )

    base_score, _ = compute_health(deal, activity_rows)
    score = base_score
    if any(key in created_keys for key in {"deal_move_poc_agreed", "deal_move_commercial_negotiation", "deal_move_workshop"}):
        score += 6
    if latest_answered_call and latest_answered_call.created_at >= datetime.utcnow() - timedelta(days=3) and (
        (latest_answered_call.call_duration or 0) >= 90
        or latest_answered_call_has_intelligence
    ):
        score += 4
    if legal_signal:
        score -= 4
    if pricing_pushback:
        score -= 12
    if competitor_name:
        score -= 10
    if "deal_retry_call" in created_keys:
        score -= 6
    if "deal_voicemail_follow_up" in created_keys:
        score -= 3
    if reschedule_mentions >= 2 and stage_allows_system_key(deal.stage, "deal_reschedule_risk"):
        score -= 8
        created_keys.add("deal_reschedule_risk")
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key="deal_reschedule_risk",
            title="Stabilize repeated reschedules",
            description="Recent communication includes multiple reschedules. Confirm urgency, tighten the next-step owner, and protect momentum.",
            priority="medium",
            source="gmail_sync",
            recommended_action=None,
            action_payload={"deal_id": str(deal.id)},
            assigned_role="ae",
        )
    if "deal_follow_up" in created_keys:
        score -= 14

    score = max(0, min(score, 100))
    deal.health_score = score
    deal.health = _health_bucket(score)
    session.add(deal)

    managed_prefixes = (
        "deal_move_",
        "deal_attach_contact_",
        "deal_add_contact_",
        "deal_reprospect_",
        "deal_demo_",
        "deal_qualified_lead_",
        "deal_poc_agreed_",
        "deal_poc_wip_",
        "deal_poc_done_",
        "deal_commercial_negotiation_",
        "deal_workshop_msa_",
        "deal_closed_won_",
        "deal_churned_",
        "deal_not_fit_",
        "deal_cold_",
        "deal_closed_lost_",
        "deal_on_hold_",
        "deal_nurture_",
        "deal_send_pricing",
        "deal_book_workshop",
        "deal_add_security_contact",
        "deal_retry_call",
        "deal_voicemail_follow_up",
        "deal_send_call_recap",
        "deal_send_meeting_recap",
        "deal_follow_up",
        "deal_pricing_pushback",
        "deal_competitor_risk",
        "deal_reschedule_risk",
    )
    open_system_tasks = (
        await session.execute(
            select(Task).where(
                Task.entity_type == "deal",
                Task.entity_id == deal.id,
                Task.task_type == "system",
                Task.status == "open",
            )
        )
    ).scalars().all()
    for system_task in open_system_tasks:
        if not system_task.system_key:
            continue
        if not system_task.system_key.startswith(managed_prefixes):
            continue
        if system_task.system_key in created_keys:
            continue
        if system_task.system_key.startswith("deal_move_") and _stage_reached(deal.stage, system_task.system_key.replace("deal_move_", "")):
            await _resolve_system_task(session, entity_type="deal", entity_id=deal.id, system_key=system_task.system_key)
            continue
        await _resolve_system_task(session, entity_type="deal", entity_id=deal.id, system_key=system_task.system_key, status="dismissed")


# A MEDDPICC field is auto-raised (with no rep task) only when the AI is highly
# confident AND the move is a forward increase backed by fresh evidence. Anything
# softer stays a suggestion task the rep accepts. This is the "validation + proper
# reason" rail: never auto-lower a rep's manual score, and stamp every auto-apply
# with its summary/evidence/source on the field and the timeline.
MEDDPICC_AUTO_APPLY_CONFIDENCE = 0.9
# Filling an EMPTY dimension can't clobber a rep value, so it applies at a lower
# bar than refining an existing one — reps were dismissing suggestions, leaving
# MEDDPICC empty, so we let confident empty-field fills land automatically.
MEDDPICC_EMPTY_FIELD_CONFIDENCE = 0.75
_MEDDPICC_AUTO_APPLY_REASONS = {"empty_field", "material_refinement"}


async def _auto_apply_meddpicc_proposal(
    session: AsyncSession, deal: Deal, proposal: TaskProposal
) -> bool:
    """Apply a high-confidence MEDDPICC increase straight onto the deal.

    Returns True when applied — the caller then skips creating a rep task and
    resolves any open suggestion for the same field. Conservative on purpose:
    forward increases only, empty_field/material_refinement only, and only when
    the model's confidence clears the auto-apply bar. (Recently rep-edited fields
    are already excluded upstream by the emitter's dedupe window.)
    """
    if proposal.code != "T-MEDPICC":
        return False
    payload = proposal.payload or {}
    field = str(payload.get("field") or "").strip().lower()
    change_reason = str(payload.get("change_reason") or "").strip().lower()
    if change_reason not in _MEDDPICC_AUTO_APPLY_REASONS:
        return False
    min_confidence = (
        MEDDPICC_EMPTY_FIELD_CONFIDENCE if change_reason == "empty_field" else MEDDPICC_AUTO_APPLY_CONFIDENCE
    )
    if proposal.confidence < min_confidence:
        return False
    try:
        target_score = int(payload.get("target_score"))
    except (TypeError, ValueError):
        return False
    if target_score not in {1, 2, 3}:
        return False

    from app.models.deal import MEDDPICC_FIELDS, compute_meddpicc_score
    if field not in MEDDPICC_FIELDS:
        return False

    qualification = dict(deal.qualification) if isinstance(deal.qualification, dict) else {}
    meddpicc = dict(qualification.get("meddpicc")) if isinstance(qualification.get("meddpicc"), dict) else {}
    previous_score = int(meddpicc.get(field, 0) or 0)
    if target_score <= previous_score:  # forward-only; never auto-lower
        return False

    meddpicc_details = get_meddpicc_details(qualification)
    summary = str(payload.get("summary") or "").strip()[:240] or None
    evidence = str(payload.get("evidence") or "").strip()[:200] or None
    raw_contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else None
    detail_contact = None
    if raw_contact:
        detail_contact = {
            "name": str(raw_contact.get("name") or "").strip()[:120] or None,
            "email": str(raw_contact.get("email") or "").strip().lower()[:254] or None,
            "title": str(raw_contact.get("title") or "").strip()[:120] or None,
            "persona_type": str(raw_contact.get("persona_type") or "").strip().lower() or None,
        }
        if not any(detail_contact.values()):
            detail_contact = None
    tags = [str(t).strip()[:48] for t in (payload.get("tags") or []) if isinstance(t, str) and str(t).strip()][:8]
    entities = [str(e).strip()[:80] for e in (payload.get("entities") or []) if isinstance(e, str) and str(e).strip()][:5]

    meddpicc[field] = target_score
    meddpicc_details[field] = {
        "summary": summary,
        "evidence": evidence,
        "change_reason": change_reason,
        "updated_at": datetime.utcnow().isoformat(),
        "target_score": target_score,
        "evidence_activity_id": proposal.evidence_activity_id,
        "contact": detail_contact,
        "tags": tags,
        "entities": entities,
        "auto_applied": True,
        "auto_apply_confidence": round(float(proposal.confidence), 3),
    }
    qualification["meddpicc"] = meddpicc
    qualification["meddpicc_details"] = meddpicc_details
    qualification["meddpicc_score"] = compute_meddpicc_score(qualification)
    deal.qualification = qualification
    deal.updated_at = datetime.utcnow()
    session.add(deal)
    session.add(
        Activity(
            deal_id=deal.id,
            type="note",
            source="beacon_ai_auto",
            medium="internal",
            content=(
                f"MEDDPICC '{field}' auto-raised {previous_score}→{target_score} by Beacon AI "
                f"(confidence {float(proposal.confidence):.0%}). Reason: {change_reason}. "
                f"Summary: {summary or ''} Evidence: {evidence or ''}"
            ),
        )
    )
    logger.info(
        "ai_task_emitter: auto-applied MEDDPICC %s %s→%s for deal %s (conf=%.2f)",
        field, previous_score, target_score, deal.id, proposal.confidence,
    )
    return True


async def _refresh_sales_ai_tasks_for_deal(session: AsyncSession, deal: Deal) -> set[str]:
    """Emit T-STAGE/T-AMOUNT/T-CLOSE/T-MEDPICC/T-CONTACT (LLM) and T-CRITICAL (rules).

    Returns the set of system_keys produced so the deal-level reconciler can
    avoid dismissing them as stale.
    """
    produced_keys: set[str] = set()

    # ── T-CRITICAL (deterministic) ──────────────────────────────────────────
    activities = (
        await session.execute(
            select(Activity)
            .where(Activity.deal_id == deal.id)
            .order_by(Activity.created_at.desc())
            .limit(40)
        )
    ).scalars().all()
    contacts = (
        await session.execute(
            select(Contact)
            .join(DealContact, DealContact.contact_id == Contact.id)
            .where(DealContact.deal_id == deal.id)
        )
    ).scalars().all()

    findings: list[CriticalFinding] = evaluate_critical_rules(deal, activities, contacts)
    for finding in findings:
        system_key = f"t_critical:{finding.rule_id}"
        produced_keys.add(system_key)
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key=system_key,
            title=finding.title,
            description=finding.description,
            priority="high" if finding.severity == "high" else "medium",
            source="critical_rules",
            recommended_action=CODE_TO_ACTION["T-CRITICAL"],
            action_payload={
                "deal_id": str(deal.id),
                "rule_id": finding.rule_id,
                "severity": finding.severity,
                "deadline_missed_at": finding.deadline_missed_at.isoformat(),
                "evidence_activity_id": finding.evidence_activity_id,
            },
            assigned_role="ae",
            task_track="critical",
        )

    # ── T-STAGE/T-AMOUNT/T-CLOSE/T-MEDPICC/T-CONTACT (LLM) ──────────────────
    try:
        proposals: list[TaskProposal] = await emit_ai_tasks(session, deal)
    except Exception as exc:  # never let the emitter break the refresh pipeline
        logger.warning("AI task emitter failed for deal %s: %s", deal.id, exc)
        proposals = []

    for proposal in proposals:
        # High-confidence forward MEDDPICC increases auto-apply (with logged
        # reason/evidence) instead of nagging the rep; everything else becomes a
        # suggestion task. When auto-applied, clear any open suggestion for the
        # same field — it's now done — and don't re-create one.
        if proposal.code == "T-MEDPICC" and await _auto_apply_meddpicc_proposal(session, deal, proposal):
            await _resolve_system_task(
                session,
                entity_type="deal",
                entity_id=deal.id,
                system_key=proposal.system_key,
                status="completed",
            )
            continue
        produced_keys.add(proposal.system_key)
        action_payload = {"deal_id": str(deal.id), **proposal.payload}
        if proposal.evidence_activity_id:
            action_payload["evidence_activity_id"] = proposal.evidence_activity_id
        action_payload["confidence"] = proposal.confidence
        await _upsert_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key=proposal.system_key,
            title=proposal.title,
            description=proposal.description,
            priority=proposal.priority,
            source="ai_emitter",
            recommended_action=CODE_TO_ACTION[proposal.code],
            action_payload=action_payload,
            assigned_role="ae",
            task_track=track_for_code(proposal.code),
        )

    # ── Reconcile: dismiss stale sales_ai/critical tasks the latest run did
    # not re-propose. This lets the CRM self-clean when buyer signals change.
    managed_prefixes = ("t_stage", "t_amount", "t_close", "t_medpicc:", "t_contact:", "t_contact_name:", "t_critical:")
    open_ai_tasks = (
        await session.execute(
            select(Task).where(
                Task.entity_type == "deal",
                Task.entity_id == deal.id,
                Task.task_type == "system",
                Task.status == "open",
                Task.task_track.in_(["sales_ai", "critical"]),
            )
        )
    ).scalars().all()
    for task in open_ai_tasks:
        key = task.system_key or ""
        if not key.startswith(managed_prefixes):
            continue
        if key in produced_keys:
            continue
        await _resolve_system_task(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system_key=key,
            status="dismissed",
        )

    return produced_keys


async def refresh_system_tasks_for_entity(session: AsyncSession, entity_type: str, entity_id: UUID) -> None:
    # Single chokepoint for every automated task generator. When system tasks
    # are disabled the whole product is "manual tasks only" — return before any
    # generator (incl. the Opus-calling deal interpreter + AI emitter) runs, so
    # all ~12 callers (webhooks, activity logging, meeting/email sync, call
    # dispositions, the task-list load, the Celery health job) become no-ops.
    if not settings.ENABLE_SYSTEM_TASKS:
        return
    if entity_type == "company":
        await _refresh_company_tasks(session, entity_id)
    elif entity_type == "contact":
        await _refresh_contact_tasks(session, entity_id)
    elif entity_type == "deal":
        deal = await session.get(Deal, entity_id)
        if not deal:
            return
        input_hash = await compute_deal_task_input_hash(session, deal)
        await _refresh_deal_tasks(session, entity_id)
        await _refresh_sales_ai_tasks_for_deal(session, deal)
        mark_deal_task_refresh_completed(deal, input_hash=input_hash)
        session.add(deal)


async def apply_task_action(
    session: AsyncSession,
    task: Task,
    user: User,
) -> dict[str, str]:
    payload = task.action_payload if isinstance(task.action_payload, dict) else {}
    action = task.recommended_action
    action_spec = get_system_task_action_spec(action)
    actor_id = user.id
    actor_name = user.name
    actor_email = user.email
    execution_label = "accepted Beacon task"
    execution_prefix = "Accepted system task"

    if action and action_spec is None:
        logger.warning("Unknown system task action encountered: %s", action)

    if action == "move_deal_stage":
        deal_id = UUID(str(payload["deal_id"]))
        stage = str(payload["stage"])
        repo = DealRepository(session)
        deal = await repo.get_or_raise(deal_id)
        previous_stage = deal.stage
        await repo.update(
            deal,
            {
                "stage": stage,
                "stage_entered_at": datetime.utcnow(),
                "days_in_stage": 0,
                "updated_at": datetime.utcnow(),
            },
        )
        session.add(
            Activity(
                deal_id=deal_id,
                type="stage_change",
                source="system_task",
                content=f"Stage moved from {previous_stage} to {stage} via {execution_label}",
                created_by_id=actor_id,
            )
        )
        await record_deal_stage_milestone(
            session,
            deal=deal,
            stage=stage,
            reached_at=deal.stage_entered_at or deal.updated_at,
            source="system_task_move_deal_stage",
        )
        return {"message": f"Deal moved to {stage}"}

    if action == "convert_contact_to_deal":
        contact_id = UUID(str(payload["contact_id"]))
        contact = await session.get(Contact, contact_id)
        if not contact:
            raise ValueError("Prospect no longer exists")
        company = await session.get(Company, contact.company_id) if contact.company_id else None
        company_name = company.name if company else "Account"
        deal = await DealRepository(session).create(
            {
                "name": f"{company_name} - {contact.first_name} {contact.last_name}".strip(),
                "pipeline_type": "deal",
                "stage": str(payload.get("stage") or "demo_done"),
                "company_id": contact.company_id,
                "assigned_to_id": contact.assigned_to_id,
                # Preserve SDR-sourced pipeline credit through conversion.
                "sdr_id": contact.sdr_id,
                "tags": ["converted_from_task"],
                "next_step": "Review meeting context and advance the opportunity",
                "stage_entered_at": datetime.utcnow(),
            }
        )
        await record_deal_stage_milestone(
            session,
            deal=deal,
            stage=deal.stage,
            reached_at=deal.stage_entered_at or deal.created_at,
            source="system_task_convert_contact_to_deal",
        )
        await DealRepository(session).add_contact(deal.id, contact.id, contact.persona_type or "champion")
        return {"message": "Prospect converted into a deal"}

    if action == "attach_contact_to_deal":
        deal_id = UUID(str(payload["deal_id"]))
        contact_id = UUID(str(payload["contact_id"]))
        repo = DealRepository(session)
        deal = await repo.get_or_raise(deal_id)
        contact = await session.get(Contact, contact_id)
        if not contact:
            raise ValueError("Stakeholder no longer exists")
        existing_link = (
            await session.execute(
                select(DealContact).where(DealContact.deal_id == deal_id, DealContact.contact_id == contact_id)
            )
        ).scalar_one_or_none()
        if not existing_link:
            await repo.add_contact(deal_id, contact_id, contact.persona_type or "stakeholder")
        session.add(
            Activity(
                deal_id=deal_id,
                contact_id=contact_id,
                type="contact_linked",
                source="system_task",
                content=f"Stakeholder {contact.first_name} {contact.last_name} linked to the deal from a synced email thread",
                created_by_id=actor_id,
            )
        )
        return {"message": f"Attached {contact.first_name} {contact.last_name} to the deal"}

    if action == "create_contact_and_attach_to_deal":
        deal_id = UUID(str(payload["deal_id"]))
        repo = DealRepository(session)
        deal = await repo.get_or_raise(deal_id)
        email = str(payload["email"]).strip().lower()
        existing_contact = (
            await session.execute(select(Contact).where(Contact.email == email))
        ).scalar_one_or_none()
        contact = existing_contact
        if not contact:
            contact = Contact(
                first_name=str(payload.get("first_name") or "Unknown").strip() or "Unknown",
                last_name=str(payload.get("last_name") or "Contact").strip() or "Contact",
                email=email,
                company_id=UUID(str(payload["company_id"])) if payload.get("company_id") else deal.company_id,
                assigned_to_id=deal.assigned_to_id,
            )
            session.add(contact)
            await session.flush()

        existing_link = (
            await session.execute(
                select(DealContact).where(DealContact.deal_id == deal_id, DealContact.contact_id == contact.id)
            )
        ).scalar_one_or_none()
        if not existing_link:
            await repo.add_contact(deal_id, contact.id, contact.persona_type or "stakeholder")
        session.add(
            Activity(
                deal_id=deal_id,
                contact_id=contact.id,
                type="contact_linked",
                source="system_task",
                content=f"Created and linked stakeholder {contact.first_name} {contact.last_name} from a synced email thread",
                created_by_id=actor_id,
            )
        )
        return {"message": f"Created and attached {contact.first_name} {contact.last_name}"}

    if action == "re_enrich_company":
        from app.tasks.enrichment import re_enrich_company_task

        company_id = str(payload["company_id"])
        re_enrich_company_task.delay(company_id)
        company = await session.get(Company, UUID(company_id))
        if company:
            append_company_activity_log(
                company,
                action="system_task_accepted",
                actor_name=actor_name,
                actor_email=actor_email,
                message=f"{execution_prefix}: refresh company enrichment",
                metadata={"task_id": str(task.id), "action": action},
            )
            company.updated_at = datetime.utcnow()
            session.add(company)
        return {"message": "Company enrichment queued"}

    if action == "refresh_icp_research":
        from app.tasks.enrichment import icp_research_single_task

        company_id = str(payload["company_id"])
        icp_research_single_task.delay(company_id)
        company = await session.get(Company, UUID(company_id))
        if company:
            append_company_activity_log(
                company,
                action="system_task_accepted",
                actor_name=actor_name,
                actor_email=actor_email,
                message=f"{execution_prefix}: refresh ICP research",
                metadata={"task_id": str(task.id), "action": action},
            )
            company.updated_at = datetime.utcnow()
            session.add(company)
        return {"message": "ICP research queued"}

    if action == "re_enrich_contact":
        from app.tasks.enrichment import re_enrich_contact_task

        contact_id = str(payload["contact_id"])
        re_enrich_contact_task.delay(contact_id)
        return {"message": "Prospect re-enrichment queued"}

    if action in {
        "send_pricing_package",
        "book_workshop_session",
        "retry_deal_call",
        "follow_up_deal_voicemail",
        "send_deal_call_recap",
        "send_meeting_follow_up",
        "follow_up_buyer_thread",
    }:
        deal_id = UUID(str(payload["deal_id"]))
        deal = await session.get(Deal, deal_id)
        if not deal:
            raise ValueError("Deal no longer exists")

        action_text = {
            "send_pricing_package": "Sent pricing/commercial package",
            "book_workshop_session": "Booked technical workshop session",
            "retry_deal_call": "Retried buyer call",
            "follow_up_deal_voicemail": "Sent voicemail follow-up",
            "send_deal_call_recap": "Sent post-call recap",
            "send_meeting_follow_up": "Sent post-meeting follow-up",
            "follow_up_buyer_thread": "Sent buyer thread follow-up",
        }[action]

        stage_update: str | None = None
        if action == "send_pricing_package" and not _stage_allows_pricing_package(deal.stage):
            raise ValueError("Pricing package actions are gated until the deal reaches post-POC commercial motion.")
        if action == "book_workshop_session" and not _stage_allows_workshop_booking(deal.stage):
            raise ValueError("Workshop booking actions are gated until commercials are underway.")
        if action == "send_pricing_package" and not _stage_reached(deal.stage, "commercial_negotiation"):
            stage_update = "commercial_negotiation"
        elif action == "book_workshop_session" and not _stage_reached(deal.stage, "workshop"):
            stage_update = "workshop"

        if stage_update:
            deal.stage = stage_update
            deal.stage_entered_at = datetime.utcnow()
            deal.days_in_stage = 0
            await record_deal_stage_milestone(
                session,
                deal=deal,
                stage=stage_update,
                reached_at=deal.stage_entered_at,
                source="system_task_follow_up_action",
            )

        next_step = str(payload.get("next_step") or "").strip()
        if next_step:
            deal.next_step = next_step.replace("_", " ").capitalize()
        deal.updated_at = datetime.utcnow()
        session.add(deal)

        session.add(
            Activity(
                deal_id=deal_id,
                type="note",
                source="system_task",
                medium="internal",
                content=f"{action_text} via {execution_label}",
                created_by_id=actor_id,
            )
        )
        return {"message": action_text}

    if action in {"retry_contact_call", "follow_up_voicemail", "send_contact_call_recap"}:
        contact_id = UUID(str(payload["contact_id"]))
        contact = await session.get(Contact, contact_id)
        if not contact:
            raise ValueError("Prospect no longer exists")

        action_text = {
            "retry_contact_call": "Retried prospect call",
            "follow_up_voicemail": "Sent voicemail follow-up",
            "send_contact_call_recap": "Sent post-call recap",
        }[action]
        session.add(
            Activity(
                contact_id=contact_id,
                type="note",
                source="system_task",
                medium="internal",
                content=f"{action_text} via {execution_label}",
                created_by_id=actor_id,
            )
        )
        return {"message": action_text}

    # ── Email / Instantly task actions ────────────────────────────────────────

    if action in {"draft_reply_follow_up", "draft_open_follow_up", "book_call_from_interest"}:
        contact_id = UUID(str(payload["contact_id"]))
        contact = await session.get(Contact, contact_id)
        if not contact:
            raise ValueError("Prospect no longer exists")

        action_text = {
            "draft_reply_follow_up": "Followed up on email reply",
            "draft_open_follow_up": "Sent follow-up after email open",
            "book_call_from_interest": "Booked call with interested prospect",
        }[action]

        # For booking a call from interest — advance sequence status
        if action == "book_call_from_interest":
            contact.sequence_status = "meeting_booked"
            contact.updated_at = datetime.utcnow()
            session.add(contact)

        session.add(
            Activity(
                contact_id=contact_id,
                type="email",
                source="system_task",
                medium="email",
                content=f"{action_text} via {execution_label}",
                created_by_id=actor_id,
            )
        )
        return {"message": action_text}

    if action == "mark_contact_unsubscribed":
        contact_id = UUID(str(payload["contact_id"]))
        contact = await session.get(Contact, contact_id)
        if not contact:
            raise ValueError("Prospect no longer exists")
        contact.sequence_status = "unsubscribed"
        contact.instantly_status = "unsubscribed"
        contact.updated_at = datetime.utcnow()
        session.add(contact)
        session.add(
            Activity(
                contact_id=contact_id,
                type="note",
                source="system_task",
                medium="internal",
                content=f"Prospect marked as unsubscribed and outreach paused via {execution_label}",
                created_by_id=actor_id,
            )
        )
        return {"message": "Prospect marked unsubscribed"}

    if action == "close_not_interested_contact":
        contact_id = UUID(str(payload["contact_id"]))
        contact = await session.get(Contact, contact_id)
        if not contact:
            raise ValueError("Prospect no longer exists")
        contact.sequence_status = "not_interested"
        contact.updated_at = datetime.utcnow()
        session.add(contact)
        session.add(
            Activity(
                contact_id=contact_id,
                type="note",
                source="system_task",
                medium="internal",
                content=f"Prospect closed as Not Interested via {execution_label}",
                created_by_id=actor_id,
            )
        )
        return {"message": "Prospect closed as not interested"}

    # ── AI task emitter actions (the 6 codes) ────────────────────────────────
    if action == "t_stage_apply":
        deal_id = UUID(str(payload["deal_id"]))
        target_stage = str(payload.get("target_stage") or "").strip()
        if target_stage not in DEAL_STAGES:
            raise ValueError(f"Invalid target_stage: {target_stage}")
        repo = DealRepository(session)
        deal = await repo.get_or_raise(deal_id)
        previous_stage = deal.stage
        await repo.update(
            deal,
            {
                "stage": target_stage,
                "stage_entered_at": datetime.utcnow(),
                "days_in_stage": 0,
                "updated_at": datetime.utcnow(),
            },
        )
        session.add(
            Activity(
                deal_id=deal_id,
                type="stage_change",
                source="system_task",
                content=f"Stage moved from {previous_stage} to {target_stage} via T-STAGE (AI)",
                created_by_id=actor_id,
            )
        )
        await record_deal_stage_milestone(
            session,
            deal=deal,
            stage=target_stage,
            reached_at=deal.stage_entered_at or deal.updated_at,
            source="t_stage_apply",
        )
        return {"message": f"Deal moved to {target_stage}"}

    if action == "t_amount_apply":
        deal_id = UUID(str(payload["deal_id"]))
        try:
            new_value = float(payload.get("new_value"))
        except (TypeError, ValueError):
            raise ValueError("new_value missing or invalid")
        from decimal import Decimal

        repo = DealRepository(session)
        deal = await repo.get_or_raise(deal_id)
        previous = deal.value
        deal.value = Decimal(str(new_value))
        deal.updated_at = datetime.utcnow()
        session.add(deal)
        session.add(
            Activity(
                deal_id=deal_id,
                type="note",
                source="system_task",
                medium="internal",
                content=f"Deal value updated from {previous} to {new_value} via T-AMOUNT (AI)",
                created_by_id=actor_id,
            )
        )
        return {"message": f"Deal value set to {new_value}"}

    if action == "t_close_apply":
        deal_id = UUID(str(payload["deal_id"]))
        from datetime import date as date_cls

        try:
            new_close = date_cls.fromisoformat(str(payload.get("new_close_date")))
        except (TypeError, ValueError):
            raise ValueError("new_close_date missing or invalid")
        repo = DealRepository(session)
        deal = await repo.get_or_raise(deal_id)
        previous = deal.close_date_est
        deal.close_date_est = new_close
        deal.updated_at = datetime.utcnow()
        session.add(deal)
        session.add(
            Activity(
                deal_id=deal_id,
                type="note",
                source="system_task",
                medium="internal",
                content=f"Expected close date updated from {previous} to {new_close} via T-CLOSE (AI)",
                created_by_id=actor_id,
            )
        )
        return {"message": f"Close date set to {new_close.isoformat()}"}

    if action == "t_medpicc_apply":
        deal_id = UUID(str(payload["deal_id"]))
        field = str(payload.get("field") or "").strip().lower()
        try:
            target_score = int(payload.get("target_score"))
        except (TypeError, ValueError):
            raise ValueError("target_score missing or invalid")
        if target_score not in {1, 2, 3}:
            raise ValueError("target_score must be 1, 2, or 3")

        from app.models.deal import MEDDPICC_FIELDS, compute_meddpicc_score

        if field not in MEDDPICC_FIELDS:
            raise ValueError(f"Invalid MEDDPICC field: {field}")

        repo = DealRepository(session)
        deal = await repo.get_or_raise(deal_id)
        qualification = dict(deal.qualification) if isinstance(deal.qualification, dict) else {}
        meddpicc = dict(qualification.get("meddpicc")) if isinstance(qualification.get("meddpicc"), dict) else {}
        meddpicc_details = get_meddpicc_details(qualification)
        previous_score = int(meddpicc.get(field, 0) or 0)
        meddpicc[field] = target_score
        summary = str(payload.get("summary") or "").strip()[:240] or None
        evidence = str(payload.get("evidence") or "").strip()[:200] or None
        change_reason = str(payload.get("change_reason") or "").strip().lower() or None
        raw_contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else None
        detail_contact = None
        if raw_contact:
            detail_contact = {
                "name": str(raw_contact.get("name") or "").strip()[:120] or None,
                "email": str(raw_contact.get("email") or "").strip().lower()[:254] or None,
                "title": str(raw_contact.get("title") or "").strip()[:120] or None,
                "persona_type": str(raw_contact.get("persona_type") or "").strip().lower() or None,
            }
            if not any(detail_contact.values()):
                detail_contact = None
        raw_tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
        tags = [
            str(tag).strip()[:48]
            for tag in raw_tags
            if isinstance(tag, str) and str(tag).strip()
        ][:8]
        raw_entities = payload.get("entities") if isinstance(payload.get("entities"), list) else []
        entities = [
            str(entity).strip()[:80]
            for entity in raw_entities
            if isinstance(entity, str) and str(entity).strip()
        ][:5]
        meddpicc_details[field] = {
            "summary": summary,
            "evidence": evidence,
            "change_reason": change_reason,
            "updated_at": datetime.utcnow().isoformat(),
            "target_score": target_score,
            "evidence_activity_id": str(payload.get("evidence_activity_id") or "").strip() or None,
            "contact": detail_contact,
            "tags": tags,
            "entities": entities,
        }
        qualification["meddpicc"] = meddpicc
        qualification["meddpicc_details"] = meddpicc_details
        qualification["meddpicc_score"] = compute_meddpicc_score(qualification)
        deal.qualification = qualification
        deal.updated_at = datetime.utcnow()
        session.add(deal)
        session.add(
            Activity(
                deal_id=deal_id,
                type="note",
                source="system_task",
                medium="internal",
                content=(
                    f"MEDDPICC field '{field}' updated from {previous_score} to {target_score} via T-MEDPICC (AI). "
                    f"Summary: {summary or ''} Evidence: {evidence or ''}"
                ),
                created_by_id=actor_id,
            )
        )
        return {"message": f"MEDDPICC {field} → {target_score}"}

    if action == "t_contact_apply":
        deal_id = UUID(str(payload["deal_id"]))
        email = str(payload.get("email") or "").strip().lower()
        if email and "@" not in email:
            raise ValueError("email invalid")
        change_type = str(payload.get("change_type") or "").strip().lower()
        name = str(payload.get("name") or "").strip()
        if change_type == "add" and not email and not name:
            raise ValueError("name or email required")
        if change_type == "update" and (not email or "@" not in email):
            raise ValueError("email missing or invalid")
        repo = DealRepository(session)
        deal = await repo.get_or_raise(deal_id)

        existing_contact = None
        if email:
            existing_contact = (
                await session.execute(select(Contact).where(Contact.email == email))
            ).scalar_one_or_none()
        elif name:
            first_name, _, last_name = name.partition(" ")
            existing_contact = (
                await session.execute(
                    select(Contact).where(
                        Contact.company_id == deal.company_id,
                        Contact.first_name == first_name,
                        Contact.last_name == (last_name or "Contact"),
                    )
                )
            ).scalar_one_or_none()

        if change_type == "add":
            if existing_contact:
                contact = existing_contact
            else:
                name_parts = name.split(" ", 1)
                first_name = name_parts[0] if name_parts and name_parts[0] else "Unknown"
                last_name = name_parts[1] if len(name_parts) > 1 else "Contact"
                contact = Contact(
                    first_name=first_name,
                    last_name=last_name,
                    email=email or None,
                    title=payload.get("title"),
                    persona_type=payload.get("persona_type"),
                    company_id=deal.company_id,
                    assigned_to_id=deal.assigned_to_id,
                )
                session.add(contact)
                await session.flush()

            existing_link = (
                await session.execute(
                    select(DealContact).where(
                        DealContact.deal_id == deal_id, DealContact.contact_id == contact.id
                    )
                )
            ).scalar_one_or_none()
            if not existing_link:
                await repo.add_contact(deal_id, contact.id, contact.persona_type or "stakeholder")
            session.add(
                Activity(
                    deal_id=deal_id,
                    contact_id=contact.id,
                    type="contact_linked",
                    source="system_task",
                    content=f"Stakeholder {email or name or contact.id} added to deal via T-CONTACT (AI)",
                    created_by_id=actor_id,
                )
            )
            return {"message": f"Added {email or name} to the deal"}

        if change_type == "update":
            if not existing_contact:
                raise ValueError(f"No contact found with email {email}")
            if payload.get("title"):
                existing_contact.title = payload["title"]
            if payload.get("persona_type"):
                existing_contact.persona_type = payload["persona_type"]
            existing_contact.updated_at = datetime.utcnow()
            session.add(existing_contact)
            session.add(
                Activity(
                    deal_id=deal_id,
                    contact_id=existing_contact.id,
                    type="note",
                    source="system_task",
                    medium="internal",
                    content=f"Stakeholder {email} updated via T-CONTACT (AI)",
                    created_by_id=actor_id,
                )
            )
            return {"message": f"Updated {email}"}

        raise ValueError(f"Invalid change_type: {change_type}")

    if action == "t_critical_apply":
        # T-CRITICAL is a human-action alert — accepting it just acknowledges
        # that the rep has seen and is handling the overdue item. The rule
        # engine will re-emit on the next refresh if the condition persists.
        deal_id = UUID(str(payload["deal_id"]))
        rule_id = str(payload.get("rule_id") or "unknown")
        session.add(
            Activity(
                deal_id=deal_id,
                type="note",
                source="system_task",
                medium="internal",
                content=f"T-CRITICAL acknowledged by {actor_name}: rule={rule_id}",
                created_by_id=actor_id,
            )
        )
        return {"message": "Critical action acknowledged"}

    return {"message": "No automatic action configured"}
