"""Reminders — per-stakeholder follow-up reminders for sales reps."""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, DBSession
from app.core.exceptions import NotFoundError, ValidationError
from app.models.company import Company
from app.models.contact import Contact
from app.models.reminder import Reminder, ReminderCreate, ReminderRead, ReminderUpdate
from app.models.user import User
from app.repositories.contact import contact_visibility_filter
from app.services.permissions import can_view_all_prospects

router = APIRouter(prefix="/reminders", tags=["reminders"])


def _normalize_utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    """Persist datetimes as naive UTC for TIMESTAMP WITHOUT TIME ZONE columns."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


async def _to_read(session: AsyncSession, r: Reminder) -> ReminderRead:
    read = ReminderRead.model_validate(r)
    contact = await session.get(Contact, r.contact_id)
    if contact:
        read.contact_name = f"{contact.first_name} {contact.last_name}"
    if r.company_id:
        company = await session.get(Company, r.company_id)
        if company:
            read.company_name = company.name
    if r.assigned_to_id:
        user = await session.get(User, r.assigned_to_id)
        if user:
            read.assigned_to_name = user.name
    return read


@router.get("/", response_model=list[ReminderRead])
async def list_reminders(
    session: DBSession,
    current_user: CurrentUser,
    contact_id: Optional[UUID] = Query(default=None),
    company_id: Optional[UUID] = Query(default=None),
    status: Optional[str] = Query(default=None),
    assigned_to_id: Optional[UUID] = Query(default=None),
):
    """List reminders with optional filters.

    Each reminder hydrates the linked contact's name, so a non-admin must only
    see reminders that are theirs (assigned to them, no contact, or about a
    contact they may view) — otherwise the list leaked every rep's prospect names.
    """
    stmt = select(Reminder)
    if not await can_view_all_prospects(session, current_user):
        visible_contact_ids = select(Contact.id).where(contact_visibility_filter(current_user.id))
        stmt = stmt.where(
            or_(
                Reminder.assigned_to_id == current_user.id,
                Reminder.contact_id.is_(None),
                Reminder.contact_id.in_(visible_contact_ids),
            )
        )
    if contact_id:
        stmt = stmt.where(Reminder.contact_id == contact_id)
    if company_id:
        stmt = stmt.where(Reminder.company_id == company_id)
    if status:
        stmt = stmt.where(Reminder.status == status)
    if assigned_to_id:
        stmt = stmt.where(Reminder.assigned_to_id == assigned_to_id)
    stmt = stmt.order_by(Reminder.due_at.asc())
    rows = (await session.execute(stmt)).scalars().all()
    return [await _to_read(session, r) for r in rows]


@router.post("/", response_model=ReminderRead, status_code=201)
async def create_reminder(payload: ReminderCreate, session: DBSession, _user: CurrentUser):
    contact = await session.get(Contact, payload.contact_id)
    if not contact:
        raise NotFoundError(f"Contact {payload.contact_id} not found")

    reminder = Reminder(
        contact_id=payload.contact_id,
        company_id=payload.company_id or contact.company_id,
        assigned_to_id=payload.assigned_to_id,
        note=payload.note,
        due_at=_normalize_utc_naive(payload.due_at),
    )
    session.add(reminder)
    await session.commit()
    await session.refresh(reminder)
    return await _to_read(session, reminder)


@router.patch("/{reminder_id}", response_model=ReminderRead)
async def update_reminder(reminder_id: UUID, payload: ReminderUpdate, session: DBSession, _user: CurrentUser):
    reminder = await session.get(Reminder, reminder_id)
    if not reminder:
        raise NotFoundError(f"Reminder {reminder_id} not found")

    data = payload.model_dump(exclude_unset=True)
    if "due_at" in data:
        data["due_at"] = _normalize_utc_naive(data["due_at"])
    if "completed_at" in data:
        data["completed_at"] = _normalize_utc_naive(data["completed_at"])
    if "status" in data:
        if data["status"] not in ("pending", "completed", "dismissed"):
            raise ValidationError("Status must be pending, completed, or dismissed")
        if data["status"] == "completed":
            data["completed_at"] = datetime.utcnow()

    for key, value in data.items():
        setattr(reminder, key, value)

    session.add(reminder)
    await session.commit()
    await session.refresh(reminder)
    return await _to_read(session, reminder)


@router.delete("/{reminder_id}", status_code=204)
async def delete_reminder(reminder_id: UUID, session: DBSession, _user: CurrentUser):
    reminder = await session.get(Reminder, reminder_id)
    if not reminder:
        raise NotFoundError(f"Reminder {reminder_id} not found")
    await session.delete(reminder)
    await session.commit()
