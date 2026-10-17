"""Shared Prospecting visibility and edit authorization helpers."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.core.exceptions import NotFoundError
from app.models.contact import Contact
from app.services.record_access import can_edit_record, can_log_call
from app.repositories.contact import ContactRepository, visible_contact_restriction


async def get_visible_contact(session, user, contact_id: UUID) -> Contact:
    """Fetch a contact only when it is visible in the caller's Prospecting scope."""
    stmt = (await ContactRepository.visible_to(session, user)).where(
        Contact.id == contact_id
    )
    contact = (await session.execute(stmt)).scalars().first()
    if contact is None:
        raise NotFoundError("Contact not found")
    return contact


async def get_visible_contact_ids(session, user, contact_ids: list[UUID]) -> list[UUID]:
    """Return only IDs in the caller's Prospecting scope, preserving input order."""
    unique_ids = list(dict.fromkeys(contact_ids))
    if not unique_ids:
        return []
    stmt = select(Contact.id).where(Contact.id.in_(unique_ids))
    restriction = await visible_contact_restriction(session, user)
    if restriction is not None:
        stmt = stmt.where(restriction)
    allowed = set((await session.execute(stmt)).scalars().all())
    return [contact_id for contact_id in unique_ids if contact_id in allowed]


async def authorize_contact_edit(session, user, contact: Contact) -> None:
    """Only admins or this prospect's assigned AE/SDR may edit; never auto-claim."""
    if not can_edit_record(user, contact):
        raise HTTPException(403, "Only the assigned AE/SDR or an admin can edit this prospect.")


# Contact columns the call-logging drawers write. A PUT made only of these is a
# call log, not a prospect edit, so it is open to the whole calling team.
CALL_LOG_FIELDS = frozenset(
    {
        "call_status",
        "call_disposition",
        "call_notes",
        "call_last_at",
        "next_followup_at",
        "sequence_status",
        "account_status",
        "cadence_task_id",
    }
)


async def authorize_call_logging(session, user, contact: Contact) -> None:
    """Any AE/SDR/admin may log a call on any prospect; everything else stays owner-only."""
    if can_log_call(user):
        return
    await authorize_contact_edit(session, user, contact)


async def get_call_loggable_contact(session, user, contact_id: UUID) -> Contact:
    """Fetch a visible contact and enforce the call-logging policy."""
    contact = await get_visible_contact(session, user, contact_id)
    await authorize_call_logging(session, user, contact)
    return contact


async def get_actionable_contact(session, user, contact_id: UUID) -> Contact:
    """Fetch a visible contact and enforce the Prospecting edit policy."""
    contact = await get_visible_contact(session, user, contact_id)
    await authorize_contact_edit(session, user, contact)
    return contact
