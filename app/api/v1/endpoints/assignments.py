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

from fastapi import APIRouter, File, Query, UploadFile
from pydantic import BaseModel
from sqlmodel import select

from app.core.dependencies import AdminUser, CurrentUser, DBSession
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.models.company import Company, CompanyRead
from app.models.contact import Contact, ContactRead
from app.models.user import User
from app.services.account_sourcing import append_company_activity_log
from app.services.account_sourcing_tabular import parse_tabular_file
from app.services.assignment_upload import (
    apply_assignment_plan,
    plan_assignment_upload,
    summarize,
)
from app.services.sdr_reassignment import (
    reset_contact_outreach_progress,
    sync_company_ae_assignment_to_contacts,
    sync_company_sdr_assignment_to_contacts,
)


def _backfill_company_owner_from_contact(company, user, *, is_sdr: bool) -> bool:
    """Give an ownerless account the owner its prospect just received.

    Prod had 25 accounts whose contacts were assigned to an SDR while the
    account itself showed Unassigned — invisible to that SDR's account list
    (non-admins only see accounts they own), so the rep worked prospects of an
    account they could not open. Backfills ONLY an empty slot; a differing
    existing owner is a deliberate split and stays. Returns True if backfilled.
    Deliberately does not cascade to sibling contacts — assigning one prospect
    shouldn't silently reassign its neighbors.
    """
    if company is None or user is None:
        return False
    if is_sdr:
        if company.sdr_id is not None:
            return False
        company.sdr_id = user.id
        company.sdr_name = user.name
        company.sdr_email = user.email
        company.sdr_assigned_at = datetime.utcnow()
        return True
    if company.assigned_to_id is not None:
        return False
    company.assigned_to_id = user.id
    company.assigned_rep = user.name
    company.assigned_rep_name = user.name
    company.assigned_rep_email = user.email
    return True

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

    if is_sdr:
        cascade = await sync_company_sdr_assignment_to_contacts(session, company, current_assigned_id)
    else:
        cascade = await sync_company_ae_assignment_to_contacts(session, company, current_assigned_id)

    next_name = (
        company.sdr_name or company.sdr_email
        if is_sdr
        else company.assigned_rep_name or company.assigned_rep_email
    )

    # Divergence must be VISIBLE: prospects held by a third rep are deliberately
    # not moved, but silently skipping them is how account and prospect
    # ownership drifted apart (206 conflicting prospects in prod). Name it in
    # the account log so the assigner can decide to move them explicitly.
    divergent_note = ""
    if cascade.kept_divergent:
        divergent_note = (
            f" ({len(cascade.kept_divergent)} prospect(s) kept their individual "
            f"{'SDR' if is_sdr else 'AE'} — reassign them from the Prospects page if that split is not intended)"
        )

    append_company_activity_log(
        company,
        action="company_assignment_updated",
        actor_name=actor.name,
        actor_email=actor.email,
        message=f"{'SDR' if is_sdr else 'AE'} updated from {previous_name or 'Unassigned'} to {next_name or 'Unassigned'}{divergent_note}",
        metadata={
            "role": "sdr" if is_sdr else "ae",
            "before": previous_name,
            "after": next_name,
            "prospects_moved": len(cascade.moved),
            "prospects_kept_divergent": len(cascade.kept_divergent),
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

    # A per-prospect SDR handoff is still a handoff: the incoming rep starts
    # from zero, exactly as they would from the account-level reassignment.
    # AE changes do not reset — outreach progress belongs to the SDR motion.
    if is_sdr and contact.sdr_id != current_assigned_id:
        reset_contact_outreach_progress(contact)

    contact.updated_at = datetime.utcnow()
    session.add(contact)
    if contact.company_id:
        company = (await session.execute(select(Company).where(Company.id == contact.company_id))).scalar_one_or_none()
        if company:
            backfilled = False
            if body.user_id:
                backfilled = _backfill_company_owner_from_contact(company, user, is_sdr=is_sdr)
            next_name = contact.sdr_name if is_sdr else contact.assigned_rep_email
            backfill_note = (
                f" — account {'SDR' if is_sdr else 'AE'} was empty and now follows this assignment"
                if backfilled
                else ""
            )
            append_company_activity_log(
                company,
                action="contact_assignment_updated",
                actor_name=actor.name,
                actor_email=actor.email,
                message=f"{'SDR' if is_sdr else 'AE'} updated to {next_name or 'Unassigned'} for {contact.first_name} {contact.last_name}{backfill_note}",
                metadata={
                    "role": "sdr" if is_sdr else "ae",
                    "contact_id": str(contact.id),
                    "contact_name": f"{contact.first_name} {contact.last_name}".strip(),
                    "before": previous_name,
                    "after": next_name,
                    "company_owner_backfilled": backfilled,
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
    prospects_kept_divergent = 0
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

        if is_sdr:
            cascade = await sync_company_sdr_assignment_to_contacts(session, company, current_assigned_id)
        else:
            cascade = await sync_company_ae_assignment_to_contacts(session, company, current_assigned_id)
        prospects_kept_divergent += len(cascade.kept_divergent)
        company.updated_at = datetime.utcnow()
        session.add(company)
        updated += 1

    await session.commit()
    return {
        "updated": updated,
        "skipped": skipped,
        "user_id": str(body.user_id) if body.user_id else None,
        "role": role_key,
        # Prospects on these accounts held by a third rep — deliberately not
        # moved; surfaced so the UI can tell the assigner about the split.
        "prospects_kept_divergent": prospects_kept_divergent,
    }


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
    companies_backfilled = 0
    company_cache: dict[UUID, Company | None] = {}
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
        # Same handoff semantics as the single-contact endpoint: a bulk SDR
        # change is still a handoff, so the incoming rep starts from zero.
        # (Bulk previously skipped this reset, so aggregates credited the old
        # SDR's calls/opens to the new one — the two paths must not differ.)
        if is_sdr and contact.sdr_id != current_assigned_id:
            reset_contact_outreach_progress(contact)
        if user and contact.company_id:
            company = company_cache.get(contact.company_id)
            if company is None and contact.company_id not in company_cache:
                company = (
                    await session.execute(select(Company).where(Company.id == contact.company_id))
                ).scalar_one_or_none()
                company_cache[contact.company_id] = company
            if _backfill_company_owner_from_contact(company, user, is_sdr=is_sdr):
                companies_backfilled += 1
                company.updated_at = datetime.utcnow()
                session.add(company)
        contact.updated_at = datetime.utcnow()
        session.add(contact)
        updated += 1

    await session.commit()
    return {
        "updated": updated,
        "skipped": skipped,
        "user_id": str(body.user_id) if body.user_id else None,
        "role": role_key,
        # Accounts whose empty owner slot now follows these assignments (see
        # _backfill_company_owner_from_contact).
        "companies_backfilled": companies_backfilled,
    }


# ── Bulk reassignment from an uploaded file ──────────────────────────────────


class AssignmentUploadRow(BaseModel):
    row_number: int
    identifier: str
    status: str
    message: str = ""
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    company_domain: Optional[str] = None
    current_ae: Optional[str] = None
    current_sdr: Optional[str] = None
    new_ae: Optional[str] = None
    new_sdr: Optional[str] = None
    ae_changes: bool = False
    sdr_changes: bool = False


class AssignmentUploadResult(BaseModel):
    dry_run: bool
    filename: str
    summary: dict
    rows: List[AssignmentUploadRow]
    applied: Optional[dict] = None


def _serialize_planned(entry) -> AssignmentUploadRow:
    return AssignmentUploadRow(
        row_number=entry.row_number,
        identifier=entry.identifier,
        status=entry.status,
        message=entry.message,
        company_id=str(entry.company_id) if entry.company_id else None,
        company_name=entry.company_name,
        company_domain=entry.company_domain,
        current_ae=entry.current_ae,
        current_sdr=entry.current_sdr,
        new_ae=entry.new_ae,
        new_sdr=entry.new_sdr,
        ae_changes=entry.ae_moves,
        sdr_changes=entry.sdr_moves,
    )


@router.post("/bulk-upload", response_model=AssignmentUploadResult)
async def bulk_assign_from_upload(
    admin: AdminUser,
    session: DBSession,
    file: UploadFile = File(...),
    dry_run: bool = Query(True, description="Preview only. Set false to write."),
):
    """Reassign many accounts' AE/SDR from a CSV/XLSX.

    Admin-only, mirroring the row-selection bulk endpoints. The SAME file is
    posted twice — once to preview, once to apply — so the applied plan is
    always re-derived server-side and never taken from the client.

    Column headers are flexible (account/company/name, domain/website, ae,
    ae_email, sdr, sdr_email). A blank AE/SDR cell leaves that slot untouched;
    clearing an owner requires the literal word "unassign".
    """
    lower_name = (file.filename or "").lower()
    if not (lower_name.endswith(".csv") or lower_name.endswith(".xlsx")):
        raise ValidationError("File must be a .csv or .xlsx")

    content = await file.read()
    rows = parse_tabular_file(file.filename or "upload.csv", content)
    if not rows:
        raise ValidationError(
            "No rows found. The file needs a header row plus an account name or domain column."
        )

    planned = await plan_assignment_upload(session, rows)
    summary = summarize(planned)

    applied = None
    if not dry_run:
        applied = await apply_assignment_plan(session, planned, actor=admin)
        await session.commit()

    return AssignmentUploadResult(
        dry_run=dry_run,
        filename=file.filename or "upload.csv",
        summary=summary,
        rows=[_serialize_planned(e) for e in planned],
        applied=applied,
    )
