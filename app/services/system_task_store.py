"""Create, find and resolve the CRM's system-generated tasks.

These are the write primitives behind every "the CRM noticed something and
raised a task" path; `_upsert_system_task` alone has 105 call sites in
app/services/tasks.py. They were interleaved with the 1769-line stage-playbook
if-chain that calls them, which made the actual write semantics — dedupe
window, dismissal snooze, assignee resolution — hard to find and hard to change
with any confidence.

Note what is NOT here: `complete_system_task` stays in tasks.py because it
calls `apply_task_action` (691 lines, same module). Moving it too would make
this module import tasks.py while tasks.py imports this one. Splitting on the
cycle rather than around it keeps the dependency one-directional:
task_signals -> system_task_store -> tasks.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.task import Task
from app.models.user import User
from app.services.task_signals import _stage_reached

logger = logging.getLogger(__name__)


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
    try:
        # Savepoint + flush: refresh_system_tasks_for_entity fires from
        # concurrent webhook events on both API replicas, and both can pass the
        # _find_open_system_task check. uq_tasks_open_system_key makes the
        # loser's INSERT fail right here — swallow it (the winner's task is the
        # one we wanted) instead of aborting the caller's whole transaction.
        async with session.begin_nested():
            session.add(task)
            await session.flush()
    except IntegrityError:
        logger.info(
            "system task %s/%s/%s created concurrently; reusing the winner",
            entity_type, entity_id, system_key,
        )
        return await _find_open_system_task(
            session,
            entity_type=entity_type,
            entity_id=entity_id,
            system_key=system_key,
        )
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
