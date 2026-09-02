"""Shared Prospecting visibility and edit authorization helpers."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.core.exceptions import NotFoundError
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal
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
    """Allow admins, contact/account/deal owners, and claimable role slots.

    Before any claim, an EMPTY contact slot whose account already has an owner
    inherits the ACCOUNT's owner — never the editor. The old behavior (editor
    claims any empty slot) silently diverged prospect ownership from the
    account whenever a teammate touched an un-cascaded contact; in prod that
    left 95 prospects invisible to their account's SDR. Claims are only for
    contacts whose account is unowned in that role (or who have no account).
    """
    role = (user.role or "").lower()
    if role == "admin":
        return
    if contact.assigned_to_id == user.id or contact.sdr_id == user.id:
        return

    if contact.company_id is not None and (contact.sdr_id is None or contact.assigned_to_id is None):
        owner_row = (
            await session.execute(
                select(Company.sdr_id, Company.sdr_name, Company.assigned_to_id, Company.assigned_rep_email)
                .where(Company.id == contact.company_id)
                .limit(1)
            )
        ).first()
        if owner_row is not None:
            company_sdr_id, company_sdr_name, company_ae_id, company_ae_email = owner_row
            if contact.sdr_id is None and company_sdr_id is not None:
                # Propagation-gap fill, not a handoff: no watermark reset, the
                # account SDR's earlier attempts on this prospect stay theirs.
                contact.sdr_id = company_sdr_id
                contact.sdr_name = company_sdr_name
            if contact.assigned_to_id is None and company_ae_id is not None:
                contact.assigned_to_id = company_ae_id
                contact.assigned_rep_email = company_ae_email
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
