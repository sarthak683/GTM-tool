"""
Assignment endpoints — Admin assigns companies/contacts to sales reps.

PATCH /assignments/company/{id}     Assign/unassign a company
PATCH /assignments/contact/{id}     Assign/unassign a contact
PATCH /assignments/bulk-companies   Bulk assign multiple companies
PATCH /assignments/bulk-contacts    Bulk assign multiple contacts
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import select

from app.core.dependencies import CurrentUser, DBSession
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.models.company import Company, CompanyRead
from app.models.contact import Contact, ContactRead
from app.models.user import User
from app.services.account_sourcing import append_company_activity_log

router = APIRouter(prefix="/assignments", tags=["assignments"])


class AssignRequest(BaseModel):
    user_id: Optional[UUID] = None  # None = unassign
    role: Optional[str] = None      # "ae" (default) or "sdr"


class BulkAssignRequest(BaseModel):
    ids: List[UUID]
    user_id: Optional[UUID] = None  # None = unassign
    role: Optional[str] = None      # "ae" (default) or "sdr"


def _validate_assignment_user(user: User, *, role: str) -> None:
    # The AE/SDR slots are ownership labels, not a hard role gate: any active
    # team member (admin/ae/sdr) may hold either slot. This lets admins (e.g.
    # Shahruk) own prospects as AE *or* SDR, and lets AEs cover the SDR slot.
    # We only reject genuinely non-assignable accounts.
    assignable = {"admin", "ae", "sdr", "agency"}
    if (user.role or "").lower() not in assignable:
        raise ValidationError(
            f"Cannot assign {user.name} ({(user.role or 'unknown').upper()}) — not an assignable team member"
        )


def _is_self_claim_or_self_release(
    *,
    actor: User,
    target_user_id: Optional[UUID],
    current_assigned_id: Optional[UUID],
    role: str,
) -> bool:
    """Non-admin reps may only self-claim or self-release a slot.

    Allowed transitions for a non-admin actor whose role matches the slot:
      - Slot is unassigned, target is themselves           (claim)
      - Slot is themselves, target is None                 (release)

    Everything else (taking someone else's slot, assigning a third party,
    cross-role action) requires admin. Per Pulkit's request 2026-05-07.
    """
    if actor.role != role:
        return False
    is_self_target = target_user_id == actor.id
    is_unassign = target_user_id is None
    if current_assigned_id is None and is_self_target:
        return True
    if current_assigned_id == actor.id and (is_unassign or is_self_target):
        return True
    return False


def _can_assign_team(actor: User) -> bool:
    """Any admin/AE/SDR may assign or reassign an account's (or contact's) AE/SDR.

    Per Annie 2026-06-17 account ownership is collaborative — any AE or SDR can
    set the AE/SDR slot, superseding the self-claim-only rule (Pulkit 2026-05-07,
    which still gates the *bulk* endpoints via `_is_self_claim_or_self_release`).
    """
    return (actor.role or "").lower() in {"admin", "ae", "sdr"}


# ── Single assignment ────────────────────────────────────────────────────────


@router.patch("/company/{company_id}", response_model=CompanyRead)
async def assign_company(
    company_id: UUID,
    body: AssignRequest,
    session: DBSession,
    actor: CurrentUser,
):
    """Assign a company-level AE or SDR. Admins can assign anyone; non-admin
    reps can only self-claim an unassigned slot that matches their role, or
    release a slot they currently hold. Pass user_id=null to unassign.
    """
    company = (
        await session.execute(select(Company).where(Company.id == company_id))
    ).scalar_one_or_none()
    if not company:
        raise NotFoundError("Company not found")

    is_sdr = (body.role or "ae") == "sdr"
    role_key = "sdr" if is_sdr else "ae"
    current_assigned_id = company.sdr_id if is_sdr else company.assigned_to_id
    if not _can_assign_team(actor):
        raise ForbiddenError("You do not have permission to assign account owners.")
    previous_name = (
        company.sdr_name or company.sdr_email
        if is_sdr
        else company.assigned_rep_name or company.assigned_rep or company.assigned_rep_email
    )
    if body.user_id:
        user = (await session.execute(select(User).where(User.id == body.user_id))).scalar_one_or_none()
        if not user:
            raise NotFoundError("User not found")
        _validate_assignment_user(user, role="sdr" if is_sdr else "ae")
        if is_sdr:
            company.sdr_id = user.id
            company.sdr_email = user.email
            company.sdr_name = user.name
        else:
            company.assigned_to_id = user.id
            company.assigned_rep = user.name
            company.assigned_rep_email = user.email
            company.assigned_rep_name = user.name
    else:
        if is_sdr:
            company.sdr_id = None
            company.sdr_email = None
            company.sdr_name = None
        else:
            company.assigned_to_id = None
            company.assigned_rep = None
            company.assigned_rep_email = None
            company.assigned_rep_name = None

    contacts = (
        await session.execute(select(Contact).where(Contact.company_id == company.id))
    ).scalars().all()
    for contact in contacts:
        if is_sdr:
            # Preserve deliberate per-contact splits: only cascade onto contacts
            # that were following the company's previous SDR (or are unassigned).
            # A contact pointed at a DIFFERENT SDR (e.g. a timezone split) is left
            # untouched. `current_assigned_id` is the company's pre-change SDR.
            if contact.sdr_id not in (None, current_assigned_id):
                continue
            contact.sdr_id = company.sdr_id
            contact.sdr_name = company.sdr_name
        else:
            if contact.assigned_to_id not in (None, current_assigned_id):
                continue
            contact.assigned_to_id = company.assigned_to_id
            contact.assigned_rep_email = company.assigned_rep_email
        contact.updated_at = datetime.utcnow()
        session.add(contact)

    next_name = (
        company.sdr_name or company.sdr_email
        if is_sdr
        else company.assigned_rep_name or company.assigned_rep_email
    )

    append_company_activity_log(
        company,
        action="company_assignment_updated",
        actor_name=actor.name,
        actor_email=actor.email,
        message=f"{'SDR' if is_sdr else 'AE'} updated from {previous_name or 'Unassigned'} to {next_name or 'Unassigned'}",
        metadata={
            "role": "sdr" if is_sdr else "ae",
            "before": previous_name,
            "after": next_name,
        },
    )

    company.updated_at = datetime.utcnow()
    session.add(company)
    await session.commit()
    await session.refresh(company)
    return company


@router.patch("/contact/{contact_id}", response_model=ContactRead)
async def assign_contact(
    contact_id: UUID,
    body: AssignRequest,
    session: DBSession,
    actor: CurrentUser,
):
    """Assign a contact to a sales rep. Admins can assign anyone; non-admin
    reps can only self-claim an unassigned slot or release a slot they hold.
    role="ae" (default) sets AE, role="sdr" sets SDR. Pass user_id=null to unassign.
    """
    contact = (
        await session.execute(select(Contact).where(Contact.id == contact_id))
    ).scalar_one_or_none()
    if not contact:
        raise NotFoundError("Contact not found")

    is_sdr = (body.role or "ae") == "sdr"
    role_key = "sdr" if is_sdr else "ae"
    current_assigned_id = contact.sdr_id if is_sdr else contact.assigned_to_id
    if not _can_assign_team(actor):
        raise ForbiddenError("You do not have permission to assign contact owners.")
    previous_name = contact.sdr_name if is_sdr else contact.assigned_rep_email

    if body.user_id:
        user = (await session.execute(select(User).where(User.id == body.user_id))).scalar_one_or_none()
        if not user:
            raise NotFoundError("User not found")
        _validate_assignment_user(user, role="sdr" if is_sdr else "ae")
        if is_sdr:
            contact.sdr_id = user.id
            contact.sdr_name = user.name
        else:
            contact.assigned_to_id = user.id
            contact.assigned_rep_email = user.email
    else:
        if is_sdr:
            contact.sdr_id = None
            contact.sdr_name = None
        else:
            contact.assigned_to_id = None
            contact.assigned_rep_email = None

    contact.updated_at = datetime.utcnow()
    session.add(contact)
    if contact.company_id:
        company = (await session.execute(select(Company).where(Company.id == contact.company_id))).scalar_one_or_none()
        if company:
            next_name = contact.sdr_name if is_sdr else contact.assigned_rep_email
            append_company_activity_log(
                company,
                action="contact_assignment_updated",
                actor_name=actor.name,
                actor_email=actor.email,
                message=f"{'SDR' if is_sdr else 'AE'} updated to {next_name or 'Unassigned'} for {contact.first_name} {contact.last_name}",
                metadata={
                    "role": "sdr" if is_sdr else "ae",
                    "contact_id": str(contact.id),
                    "contact_name": f"{contact.first_name} {contact.last_name}".strip(),
                    "before": previous_name,
                    "after": next_name,
                },
            )
            company.updated_at = datetime.utcnow()
            session.add(company)
    await session.commit()
    await session.refresh(contact)
    return contact


# ── Bulk assignment ──────────────────────────────────────────────────────────


@router.patch("/bulk-companies")
async def bulk_assign_companies(
    body: BulkAssignRequest,
    session: DBSession,
    actor: CurrentUser,
):
    """Bulk assign multiple companies to a sales rep.

    Admins can assign/reassign any AE/SDR slot. Non-admin reps can only
    bulk self-claim unassigned slots, or bulk release slots they own.
    """
    is_sdr = (body.role or "ae") == "sdr"
    role_key = "sdr" if is_sdr else "ae"
    user = None
    if body.user_id:
        user = (await session.execute(select(User).where(User.id == body.user_id))).scalar_one_or_none()
        if not user:
            raise NotFoundError("User not found")
        _validate_assignment_user(user, role=role_key)
    if actor.role != "admin" and not (
        (body.user_id == actor.id or body.user_id is None) and actor.role == role_key
    ):
        raise ForbiddenError(
            "Only admins can bulk reassign accounts. You can bulk claim unassigned "
            f"{role_key.upper()} slots or release your own assignments."
        )

    updated = 0
    skipped = 0
    for cid in body.ids:
        company = (
            await session.execute(select(Company).where(Company.id == cid))
        ).scalar_one_or_none()
        if not company:
            skipped += 1
            continue
        current_assigned_id = company.sdr_id if is_sdr else company.assigned_to_id
        if actor.role != "admin" and not _is_self_claim_or_self_release(
            actor=actor,
            target_user_id=body.user_id,
            current_assigned_id=current_assigned_id,
            role=role_key,
        ):
            skipped += 1
            continue
        if user:
            if is_sdr:
                company.sdr_id = user.id
                company.sdr_email = user.email
                company.sdr_name = user.name
            else:
                company.assigned_to_id = user.id
                company.assigned_rep = user.name
                company.assigned_rep_email = user.email
                company.assigned_rep_name = user.name
        else:
            if is_sdr:
                company.sdr_id = None
                company.sdr_email = None
                company.sdr_name = None
            else:
                company.assigned_to_id = None
                company.assigned_rep = None
                company.assigned_rep_email = None
                company.assigned_rep_name = None

        contacts = (
            await session.execute(select(Contact).where(Contact.company_id == company.id))
        ).scalars().all()
        for contact in contacts:
            if is_sdr:
                # Preserve deliberate per-contact SDR splits (see assign_company).
                if contact.sdr_id not in (None, current_assigned_id):
                    continue
                contact.sdr_id = company.sdr_id
                contact.sdr_name = company.sdr_name
            else:
                if contact.assigned_to_id not in (None, current_assigned_id):
                    continue
                contact.assigned_to_id = company.assigned_to_id
                contact.assigned_rep_email = company.assigned_rep_email
            contact.updated_at = datetime.utcnow()
            session.add(contact)
        company.updated_at = datetime.utcnow()
        session.add(company)
        updated += 1

    await session.commit()
    return {"updated": updated, "skipped": skipped, "user_id": str(body.user_id) if body.user_id else None, "role": role_key}


@router.patch("/bulk-contacts")
async def bulk_assign_contacts(
    body: BulkAssignRequest,
    session: DBSession,
    actor: CurrentUser,
):
    """Bulk assign multiple contacts to a sales rep.

    Admins can assign/reassign any AE/SDR slot. Non-admin reps can only
    bulk self-claim unassigned slots, or bulk release slots they own.
    """
    is_sdr = (body.role or "ae") == "sdr"
    role_key = "sdr" if is_sdr else "ae"
    user = None
    if body.user_id:
        user = (await session.execute(select(User).where(User.id == body.user_id))).scalar_one_or_none()
        if not user:
            raise NotFoundError("User not found")
        _validate_assignment_user(user, role=role_key)
    if actor.role != "admin" and not (
        (body.user_id == actor.id or body.user_id is None) and actor.role == role_key
    ):
        raise ForbiddenError(
            "Only admins can bulk reassign contacts. You can bulk claim unassigned "
            f"{role_key.upper()} slots or release your own assignments."
        )

    updated = 0
    skipped = 0
    for cid in body.ids:
        contact = (
            await session.execute(select(Contact).where(Contact.id == cid))
        ).scalar_one_or_none()
        if not contact:
            skipped += 1
            continue
        current_assigned_id = contact.sdr_id if is_sdr else contact.assigned_to_id
        if actor.role != "admin" and not _is_self_claim_or_self_release(
            actor=actor,
            target_user_id=body.user_id,
            current_assigned_id=current_assigned_id,
            role=role_key,
        ):
            skipped += 1
            continue
        if user:
            if is_sdr:
                contact.sdr_id = user.id
                contact.sdr_name = user.name
            else:
                contact.assigned_to_id = user.id
                contact.assigned_rep_email = user.email
        else:
            if is_sdr:
                contact.sdr_id = None
                contact.sdr_name = None
            else:
                contact.assigned_to_id = None
                contact.assigned_rep_email = None
        contact.updated_at = datetime.utcnow()
        session.add(contact)
        updated += 1

    await session.commit()
    return {"updated": updated, "skipped": skipped, "user_id": str(body.user_id) if body.user_id else None, "role": role_key}
