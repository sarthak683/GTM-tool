"""Shared Prospecting visibility and edit authorization helpers."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.core.exceptions import NotFoundError
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal
from app.repositories.contact import visible_contact_restriction


async def get_visible_contact(session, user, contact_id: UUID) -> Contact:
    """Fetch a contact only when it is visible in the caller's Prospecting scope."""
    stmt = select(Contact).where(Contact.id == contact_id)
    restriction = await visible_contact_restriction(session, user)
    if restriction is not None:
        stmt = stmt.where(restriction)
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
    """Allow admins, contact/account/deal owners, and claimable role slots."""
    role = (user.role or "").lower()
    if role == "admin":
        return
    if contact.assigned_to_id == user.id or contact.sdr_id == user.id:
        return
    if role == "sdr":
        if contact.sdr_id is None:
            contact.sdr_id = user.id
            contact.sdr_name = getattr(user, "name", None) or user.email
            return
    elif contact.assigned_to_id is None:
        contact.assigned_to_id = user.id
        contact.assigned_rep_email = user.email
        return

    if contact.company_id is not None:
        owns_account = (
            await session.execute(
                select(Company.id)
                .where(
                    Company.id == contact.company_id,
                    or_(
                        Company.assigned_to_id == user.id,
                        Company.sdr_id == user.id,
                    ),
                )
                .limit(1)
            )
        ).first()
        if owns_account:
            return

        owns_deal = (
            await session.execute(
                select(Deal.id)
                .where(
                    Deal.company_id == contact.company_id,
                    Deal.assigned_to_id == user.id,
                )
                .limit(1)
            )
        ).first()
        if owns_deal:
            return

    raise HTTPException(
        status_code=403,
        detail=(
            "You can only edit prospects assigned to you or in an account you own. "
            "Claim an unassigned one, or ask an admin to reassign this prospect."
        ),
    )


async def get_actionable_contact(session, user, contact_id: UUID) -> Contact:
    """Fetch a visible contact and enforce the Prospecting edit policy."""
    contact = await get_visible_contact(session, user, contact_id)
    await authorize_contact_edit(session, user, contact)
    return contact
