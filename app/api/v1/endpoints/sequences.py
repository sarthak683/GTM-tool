"""
Outreach Sequence endpoints — the new, reusable multi-contact cadence
system (builder + enrollment). See app/models/sequence.py and
app/services/sequences.py.

Running a step reuses the existing Call Disposition Drawer / "Log LinkedIn
touch" dialog / "Log Email" dialog untouched (via PUT /contacts/{id} with
cadence_task_id) — nothing about completing a step lives in this file.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.dependencies import CurrentUser, DBSession
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.models.sequence import (
    SEQUENCE_EMAIL_SEND_VIA,
    SEQUENCE_LINKEDIN_CATEGORIES,
    SEQUENCE_STEP_TYPES,
    Enrollment,
    EnrollmentRead,
    Sequence,
    SequenceCreate,
    SequenceRead,
    SequenceStep,
    SequenceStepCreate,
    SequenceStepRead,
    SequenceUpdate,
)
from app.models.user import User
from app.services.sequences import enroll_contact, remove_enrollment

router = APIRouter(prefix="/sequences", tags=["sequences"])


def _validate_steps(steps: list[SequenceStepCreate]) -> None:
    if not steps:
        raise ValidationError("A sequence needs at least one step")
    for s in steps:
        if s.type not in SEQUENCE_STEP_TYPES:
            raise ValidationError(f"Invalid step type: {s.type}")
        if s.type == "email":
            if s.send_via and s.send_via not in SEQUENCE_EMAIL_SEND_VIA:
                raise ValidationError(f"Invalid send_via: {s.send_via}")
            if s.send_via == "instantly" and not s.instantly_campaign_id:
                raise ValidationError("instantly_campaign_id is required when send_via=instantly")
        if s.type == "linkedin":
            if s.linkedin_category and s.linkedin_category not in SEQUENCE_LINKEDIN_CATEGORIES:
                raise ValidationError(f"Invalid linkedin_category: {s.linkedin_category}")
        if s.delay_minutes < 0:
            raise ValidationError("delay_minutes cannot be negative")


async def _get_sequence_or_raise(session: DBSession, sequence_id: UUID) -> Sequence:
    sequence = await session.get(Sequence, sequence_id)
    if not sequence:
        raise NotFoundError("Sequence not found")
    return sequence


def _can_edit(sequence: Sequence, user: User) -> bool:
    # Per decision: a shared Team sequence can only be edited by its
    # creator or an admin. A private sequence is only ever its owner's.
    if sequence.owner_id == user.id:
        return True
    return bool(user.is_admin)


class SequenceCreatePayload(SequenceCreate):
    steps: list[SequenceStepCreate]


@router.get("", response_model=list[SequenceRead])
async def list_sequences(session: DBSession, current_user: CurrentUser, scope: Optional[str] = None):
    """scope=mine | team | omitted (both, mine first)."""
    stmt = select(Sequence)
    if scope == "mine":
        stmt = stmt.where(Sequence.owner_id == current_user.id)
    elif scope == "team":
        stmt = stmt.where(Sequence.shared.is_(True))
    else:
        stmt = stmt.where((Sequence.owner_id == current_user.id) | (Sequence.shared.is_(True)))
    sequences = (await session.execute(stmt.order_by(Sequence.updated_at.desc()))).scalars().all()
    if not sequences:
        return []

    owner_ids = {s.owner_id for s in sequences}
    owners = dict(
        (await session.execute(select(User.id, User.name).where(User.id.in_(owner_ids)))).all()
    )
    seq_ids = [s.id for s in sequences]
    step_counts = dict(
        (await session.execute(
            select(SequenceStep.sequence_id, func.count(SequenceStep.id))
            .where(SequenceStep.sequence_id.in_(seq_ids))
            .group_by(SequenceStep.sequence_id)
        )).all()
    )
    active_counts = dict(
        (await session.execute(
            select(Enrollment.sequence_id, func.count(Enrollment.id))
            .where(Enrollment.sequence_id.in_(seq_ids), Enrollment.status == "active")
            .group_by(Enrollment.sequence_id)
        )).all()
    )

    reads = []
    for s in sequences:
        read = SequenceRead.model_validate(s)
        read.owner_name = owners.get(s.owner_id)
        read.step_count = step_counts.get(s.id, 0)
        read.active_enrollment_count = active_counts.get(s.id, 0)
        reads.append(read)
    return reads


@router.get("/{sequence_id}", response_model=SequenceRead)
async def get_sequence(sequence_id: UUID, session: DBSession, current_user: CurrentUser):
    sequence = await _get_sequence_or_raise(session, sequence_id)
    if sequence.owner_id != current_user.id and not sequence.shared:
        raise ForbiddenError("Not your sequence")
    read = SequenceRead.model_validate(sequence)
    owner = await session.get(User, sequence.owner_id)
    read.owner_name = owner.name if owner else None
    steps = (
        await session.execute(select(SequenceStep).where(SequenceStep.sequence_id == sequence_id))
    ).scalars().all()
    read.step_count = len(steps)
    active = (
        await session.execute(
            select(func.count(Enrollment.id)).where(Enrollment.sequence_id == sequence_id, Enrollment.status == "active")
        )
    ).scalar_one()
    read.active_enrollment_count = active
    return read


@router.get("/{sequence_id}/steps", response_model=list[SequenceStepRead])
async def list_sequence_steps(sequence_id: UUID, session: DBSession, current_user: CurrentUser):
    sequence = await _get_sequence_or_raise(session, sequence_id)
    if sequence.owner_id != current_user.id and not sequence.shared:
        raise ForbiddenError("Not your sequence")
    steps = (
        await session.execute(
            select(SequenceStep).where(SequenceStep.sequence_id == sequence_id).order_by(SequenceStep.order)
        )
    ).scalars().all()
    return steps


@router.post("", response_model=SequenceRead, status_code=201)
async def create_sequence(payload: SequenceCreatePayload, session: DBSession, current_user: CurrentUser):
    _validate_steps(payload.steps)
    sequence = Sequence(name=payload.name, shared=payload.shared, owner_id=current_user.id)
    session.add(sequence)
    await session.flush()
    for i, step in enumerate(payload.steps):
        session.add(SequenceStep(
            sequence_id=sequence.id,
            order=i,
            type=step.type,
            delay_minutes=step.delay_minutes,
            send_via=step.send_via if step.type == "email" else None,
            instantly_campaign_id=step.instantly_campaign_id if step.type == "email" else None,
            linkedin_category=step.linkedin_category if step.type == "linkedin" else None,
        ))
    await session.commit()
    await session.refresh(sequence)
    read = SequenceRead.model_validate(sequence)
    read.owner_name = current_user.name
    read.step_count = len(payload.steps)
    return read


@router.put("/{sequence_id}", response_model=SequenceRead)
async def update_sequence(sequence_id: UUID, payload: SequenceCreatePayload, session: DBSession, current_user: CurrentUser):
    """Full replace of name/shared/steps. In-flight Enrollments are
    unaffected — they keep advancing through whichever SequenceStep rows
    their current_step index still points at; only steps AT OR AFTER an
    enrollment's current position could shift meaning, which is an accepted
    tradeoff of "edit anytime" rather than versioning every edit.
    """
    sequence = await _get_sequence_or_raise(session, sequence_id)
    if not _can_edit(sequence, current_user):
        raise ForbiddenError("Only the creator or an admin can edit this sequence")
    _validate_steps(payload.steps)

    sequence.name = payload.name
    sequence.shared = payload.shared
    from datetime import datetime
    sequence.updated_at = datetime.utcnow()
    session.add(sequence)

    # Update existing steps IN PLACE by position rather than delete-and-
    # recreate: an in-flight Task's step_id points at a specific SequenceStep
    # row, and that row disappearing (even to be replaced by an identical-
    # looking one) breaks the task's link to its step (step_id -> NULL) and
    # hides its action button in Tasks. Only positions beyond the new step
    # count are actually deleted.
    existing_steps = (
        await session.execute(
            select(SequenceStep).where(SequenceStep.sequence_id == sequence_id).order_by(SequenceStep.order)
        )
    ).scalars().all()
    for i, step in enumerate(payload.steps):
        if i < len(existing_steps):
            row = existing_steps[i]
            row.type = step.type
            row.delay_minutes = step.delay_minutes
            row.send_via = step.send_via if step.type == "email" else None
            row.instantly_campaign_id = step.instantly_campaign_id if step.type == "email" else None
            row.linkedin_category = step.linkedin_category if step.type == "linkedin" else None
            session.add(row)
        else:
            session.add(SequenceStep(
                sequence_id=sequence.id,
                order=i,
                type=step.type,
                delay_minutes=step.delay_minutes,
                send_via=step.send_via if step.type == "email" else None,
                instantly_campaign_id=step.instantly_campaign_id if step.type == "email" else None,
                linkedin_category=step.linkedin_category if step.type == "linkedin" else None,
            ))
    for extra in existing_steps[len(payload.steps):]:
        await session.delete(extra)
    await session.commit()
    await session.refresh(sequence)
    read = SequenceRead.model_validate(sequence)
    read.owner_name = current_user.name
    read.step_count = len(payload.steps)
    read.active_enrollment_count = (
        await session.execute(
            select(func.count(Enrollment.id)).where(
                Enrollment.sequence_id == sequence.id, Enrollment.status == "active"
            )
        )
    ).scalar_one()
    return read


@router.delete("/{sequence_id}", status_code=204)
async def delete_sequence(sequence_id: UUID, session: DBSession, current_user: CurrentUser):
    """Deleting a template never deletes past Enrollments or their Task
    history — only future enrollment into this exact template stops being
    possible.
    """
    sequence = await _get_sequence_or_raise(session, sequence_id)
    if not _can_edit(sequence, current_user):
        raise ForbiddenError("Only the creator or an admin can delete this sequence")
    steps = (
        await session.execute(select(SequenceStep).where(SequenceStep.sequence_id == sequence_id))
    ).scalars().all()
    for s in steps:
        await session.delete(s)
    await session.delete(sequence)
    await session.commit()


class EnrollPayload(BaseModel):
    contact_id: UUID
    sequence_id: UUID


@router.post("/enroll", response_model=EnrollmentRead, status_code=201)
async def enroll(payload: EnrollPayload, session: DBSession, current_user: CurrentUser):
    try:
        enrollment = await enroll_contact(
            session, contact_id=payload.contact_id, sequence_id=payload.sequence_id, enrolled_by=current_user.id
        )
    except ValueError as e:
        raise ValidationError(str(e))
    sequence = await session.get(Sequence, enrollment.sequence_id)
    total = (
        await session.execute(
            select(func.count(SequenceStep.id)).where(SequenceStep.sequence_id == enrollment.sequence_id)
        )
    ).scalar_one()
    read = EnrollmentRead.model_validate(enrollment)
    read.sequence_name = sequence.name if sequence else None
    read.total_steps = total
    return read


@router.get("/contacts/{contact_id}/enrollment", response_model=Optional[EnrollmentRead])
async def get_contact_enrollment(contact_id: UUID, session: DBSession, current_user: CurrentUser):
    """The contact's current active enrollment, if any — backs the "In:
    Sequence Name — Step N/M" status line that replaces the Add-to-sequence
    button once enrolled.
    """
    enrollment = (
        await session.execute(
            select(Enrollment).where(Enrollment.contact_id == contact_id, Enrollment.status == "active")
        )
    ).scalar_one_or_none()
    if not enrollment:
        return None
    sequence = await session.get(Sequence, enrollment.sequence_id)
    total = (
        await session.execute(
            select(func.count(SequenceStep.id)).where(SequenceStep.sequence_id == enrollment.sequence_id)
        )
    ).scalar_one()
    read = EnrollmentRead.model_validate(enrollment)
    read.sequence_name = sequence.name if sequence else None
    read.total_steps = total
    return read


@router.delete("/enrollments/{enrollment_id}", status_code=204)
async def unenroll(enrollment_id: UUID, session: DBSession, current_user: CurrentUser):
    await remove_enrollment(session, enrollment_id=enrollment_id)


@router.post("/tasks/{task_id}/instantly-add")
async def add_task_contact_to_instantly(task_id: UUID, session: DBSession, current_user: CurrentUser):
    """The one-click action for an Instantly-routed Email step's task.

    Adds the contact as a lead to the step's chosen campaign right now, but
    does NOT complete the task or advance the enrollment — Instantly sends
    on its own schedule, and the existing Instantly webhook is what
    eventually confirms the send and calls complete_cadence_step.
    """
    from app.clients.instantly import InstantlyClient
    from app.models.contact import Contact
    from app.models.task import Task

    task = await session.get(Task, task_id)
    if not task or task.source != "cadence" or not task.step_id:
        raise NotFoundError("Cadence task not found")
    step = await session.get(SequenceStep, task.step_id)
    if not step or step.type != "email" or step.send_via != "instantly" or not step.instantly_campaign_id:
        raise ValidationError("This task is not an Instantly email step")
    contact = await session.get(Contact, task.entity_id)
    if not contact or not contact.email:
        raise ValidationError("Contact has no email on file")

    result = await InstantlyClient().add_lead(
        campaign_id=step.instantly_campaign_id,
        email=contact.email,
        first_name=contact.first_name or "",
        last_name=contact.last_name or "",
        job_title=contact.title or "",
        linkedin_url=contact.linkedin_url or "",
    )
    return {"added": result is not None}
