"""Reminders — per-stakeholder follow-up reminders for sales reps."""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

from app.core.dependencies import CurrentUser, DBSession
from app.core.exceptions import NotFoundError, ValidationError
from app.models.company import Company
from app.models.contact import Contact
from app.models.reminder import Reminder, ReminderCreate, ReminderRead, ReminderUpdate
from app.models.user import User
from app.repositories.contact import contact_visibility_filter
from app.services.contact_access import get_actionable_contact, get_visible_contact
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

    Each reminder hydrates the linked contact's name, so non-admins only see
    reminders for prospects in their current Prospecting scope.
    """
    stmt = select(Reminder)
    if not await can_view_all_prospects(session, current_user):
        visible_contact_ids = select(Contact.id).where(
            contact_visibility_filter(current_user.id, current_user.role)
        )
        stmt = stmt.where(Reminder.contact_id.in_(visible_contact_ids))
    if contact_id:
        await get_visible_contact(session, current_user, contact_id)
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
    contact = await get_actionable_contact(session, _user, payload.contact_id)

    reminder = Reminder(
        contact_id=payload.contact_id,
        company_id=contact.company_id,
        created_by_id=_user.id,
        assigned_to_id=payload.assigned_to_id if _user.is_admin else _user.id,
        note=payload.note,
        due_at=_normalize_utc_naive(payload.due_at),
    )
    session.add(reminder)
    await session.commit()
    await session.refresh(reminder)
    return await _to_read(session, reminder)


class BulkReminderCreate(SQLModel):
    contact_ids: list[UUID]
    note: str
    due_at: datetime


@router.post("/bulk")
async def create_bulk_reminders(
    payload: BulkReminderCreate,
    session: DBSession,
    current_user: CurrentUser,
):
    """Set contact follow-up timestamps and reminders in one transaction."""
    contact_ids = list(dict.fromkeys(payload.contact_ids))
    if not contact_ids:
        raise ValidationError("Select at least one prospect.")
    if len(contact_ids) > 2000:
        raise ValidationError("Too many prospects in one request (max 2000).")
    note = payload.note.strip()
    if not note:
        raise ValidationError("Reminder note is required.")
    due_at = _normalize_utc_naive(payload.due_at)
    if due_at is None:
        raise ValidationError("Reminder due date is required.")

    contacts = []
    for contact_id in contact_ids:
        contacts.append(await get_actionable_contact(session, current_user, contact_id))

    for contact in contacts:
        contact.next_followup_at = due_at
        contact.updated_at = datetime.utcnow()
        session.add(contact)
        session.add(
            Reminder(
                contact_id=contact.id,
                company_id=contact.company_id,
                created_by_id=current_user.id,
                assigned_to_id=current_user.id,
                note=note,
                due_at=due_at,
            )
        )
    await session.commit()
    return {"created": len(contacts), "requested": len(contact_ids)}


@router.patch("/{reminder_id}", response_model=ReminderRead)
async def update_reminder(reminder_id: UUID, payload: ReminderUpdate, session: DBSession, _user: CurrentUser):
    reminder = await session.get(Reminder, reminder_id)
    if not reminder:
        raise NotFoundError(f"Reminder {reminder_id} not found")
    await get_visible_contact(session, _user, reminder.contact_id)
    if not _user.is_admin and _user.id not in {
        reminder.created_by_id,
        reminder.assigned_to_id,
    }:
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
    await get_visible_contact(session, _user, reminder.contact_id)
    if not _user.is_admin and _user.id not in {
        reminder.created_by_id,
        reminder.assigned_to_id,
    }:
        raise NotFoundError(f"Reminder {reminder_id} not found")
    await session.delete(reminder)
    await session.commit()
