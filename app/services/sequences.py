"""
Outreach Sequence engine — enrollment and step advancement.

The whole point of this feature: reps reuse the exact same Call Disposition
Drawer / "Log LinkedIn touch" dialog / "Log Email" dialog they already use
from Prospecting. Nothing about those dialogs or their save behavior
changes. The only new thing is `complete_cadence_step`, called after that
same normal save succeeds, IF (and only if) the dialog was opened from a
cadence task — which is how a step actually advances.

delay_minutes anchoring: a step's delay_minutes is added to the moment the
PREVIOUS step was completed (self-correcting if a rep falls behind), not to
the original enrollment date. Step 1 anchors to enrollment.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.sequence import Enrollment, Sequence, SequenceStep
from app.models.task import Task


async def _sequence_steps(session: AsyncSession, sequence_id: UUID) -> list[SequenceStep]:
    rows = (
        await session.execute(
            select(SequenceStep).where(SequenceStep.sequence_id == sequence_id).order_by(SequenceStep.order)
        )
    ).scalars().all()
    return list(rows)


_LINKEDIN_CATEGORY_LABEL = {"dm": "DM", "follow_up": "Follow up", "inmail": "In-mail"}


def _step_task_title(sequence_name: str, step_number: int, total_steps: int, step: SequenceStep) -> str:
    channel = {"email": "Email", "call": "Call", "linkedin": "LinkedIn"}.get(step.type, step.type.title())
    if step.type == "linkedin" and step.linkedin_category:
        channel = f"LinkedIn — {_LINKEDIN_CATEGORY_LABEL.get(step.linkedin_category, step.linkedin_category)}"
    return f"{sequence_name} — Step {step_number}/{total_steps}: {channel}"


async def _create_step_task(
    session: AsyncSession,
    *,
    enrollment: Enrollment,
    step: SequenceStep,
    step_number: int,
    total_steps: int,
    sequence_name: str,
    due_at: datetime,
) -> Task:
    task = Task(
        entity_type="contact",
        entity_id=enrollment.contact_id,
        # The Tasks workspace endpoint only ever lists task_type in
        # {"manual", "system"} — a cadence task is rep-facing work, so it's
        # "manual" here; task.source="cadence" is what actually distinguishes
        # it everywhere else (Tasks page filter/chip, the webhook lookup).
        task_type="manual",
        title=_step_task_title(sequence_name, step_number, total_steps, step),
        status="open",
        priority="medium",
        source="cadence",
        due_at=due_at,
        enrollment_id=enrollment.id,
        step_id=step.id,
        # Assigned to whoever enrolled the contact — this is what makes the
        # task land in that rep's "My Queue" on the Tasks page.
        created_by_id=enrollment.enrolled_by,
        assigned_to_id=enrollment.enrolled_by,
    )
    session.add(task)
    await session.flush()
    return task


async def enroll_contact(
    session: AsyncSession,
    *,
    contact_id: UUID,
    sequence_id: UUID,
    enrolled_by: UUID,
) -> Enrollment:
    """Enroll one contact into one sequence. Raises ValueError if the
    contact already has an active enrollment (enforced here, and backed by
    a partial unique index at the DB level as the final guard).
    """
    existing = (
        await session.execute(
            select(Enrollment).where(Enrollment.contact_id == contact_id, Enrollment.status == "active")
        )
    ).scalar_one_or_none()
    if existing:
        raise ValueError("Contact already has an active sequence enrollment")

    steps = await _sequence_steps(session, sequence_id)
    if not steps:
        raise ValueError("Sequence has no steps")

    sequence = await session.get(Sequence, sequence_id)
    if not sequence:
        raise ValueError("Sequence not found")

    enrollment = Enrollment(
        contact_id=contact_id,
        sequence_id=sequence_id,
        enrolled_by=enrolled_by,
        current_step=1,
        status="active",
    )
    session.add(enrollment)
    await session.flush()

    # Step 1 anchors to enrollment (now), not a prior step's completion.
    await _create_step_task(
        session,
        enrollment=enrollment,
        step=steps[0],
        step_number=1,
        total_steps=len(steps),
        sequence_name=sequence.name,
        due_at=datetime.utcnow() + timedelta(minutes=steps[0].delay_minutes),
    )
    await session.commit()
    await session.refresh(enrollment)
    return enrollment


async def complete_cadence_step(session: AsyncSession, *, task_id: UUID) -> Optional[Enrollment]:
    """Mark a cadence task's step complete and advance its Enrollment.

    Called after the SAME save that already happened in the reused Call /
    LinkedIn / Email dialogs — this never runs the underlying action itself,
    it only reacts to it having already succeeded. Silently no-ops if the
    task isn't a still-open cadence task (already completed, or not a
    cadence task at all) so a stale/duplicate call is harmless.
    """
    task = await session.get(Task, task_id)
    if not task or task.source != "cadence" or task.status != "open" or not task.enrollment_id:
        return None

    enrollment = await session.get(Enrollment, task.enrollment_id)
    if not enrollment or enrollment.status != "active":
        return None

    now = datetime.utcnow()
    task.status = "completed"
    task.completed_at = now
    session.add(task)

    steps = await _sequence_steps(session, enrollment.sequence_id)
    sequence = await session.get(Sequence, enrollment.sequence_id)
    next_index = enrollment.current_step  # 0-based index of the NEXT step (current_step is 1-based)

    if next_index < len(steps):
        next_step = steps[next_index]
        enrollment.current_step = next_index + 1
        session.add(enrollment)
        await _create_step_task(
            session,
            enrollment=enrollment,
            step=next_step,
            step_number=next_index + 1,
            total_steps=len(steps),
            sequence_name=sequence.name if sequence else "Sequence",
            due_at=now + timedelta(minutes=next_step.delay_minutes),
        )
    else:
        enrollment.status = "completed"
        enrollment.completed_at = now
        session.add(enrollment)

    await session.commit()
    await session.refresh(enrollment)
    return enrollment


async def remove_enrollment(session: AsyncSession, *, enrollment_id: UUID) -> None:
    """Stop a contact's active enrollment — used by the "remove/switch"
    control on the status line. Any still-open cadence task for it is
    dismissed rather than left dangling in Tasks.
    """
    enrollment = await session.get(Enrollment, enrollment_id)
    if not enrollment:
        return
    enrollment.status = "removed"
    session.add(enrollment)

    open_tasks = (
        await session.execute(
            select(Task).where(Task.enrollment_id == enrollment_id, Task.status == "open")
        )
    ).scalars().all()
    for t in open_tasks:
        t.status = "dismissed"
        session.add(t)
    await session.commit()
