from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlmodel import SQLModel, select

from app.core.dependencies import AdminUser, CurrentUser, DBSession, Pagination
from app.core.exceptions import NotFoundError
from app.models.company import Company
from app.models.contact import Contact, ContactCreate, ContactRead, ContactUpdate
from app.repositories.contact import ContactRepository
from app.schemas.common import PaginatedResponse
from app.services.account_sourcing import (
    load_workspace_sequence_schedule,
    parse_prospect_upload_file,
    refresh_company_prospecting_fields,
    refresh_contact_sequence_plan,
    row_to_company_fields,
    row_to_contact_fields,
)
from app.services.contact_tracking import apply_contact_tracking, to_contact_read
from app.services.disposition_effects import (
    apply_call_disposition_effects,
    apply_linkedin_status_effects,
)
from app.services.timeline import build_contact_timeline
from app.services.permissions import require_workspace_permission
from app.services.persona_classifier import classify_persona
from app.services.prospect_hygiene import is_valid_prospect_candidate

router = APIRouter(prefix="/contacts", tags=["contacts"])


class ProspectImportMissingCompany(SQLModel):
    name: str
    domain: Optional[str] = None
    contacts_count: int = 0


class ProspectImportCreatedCompany(SQLModel):
    id: UUID
    name: str
    domain: Optional[str] = None
    contacts_count: int = 0


class ProspectImportResponse(SQLModel):
    imported_rows: int
    created_count: int
    updated_count: int
    skipped_count: int
    warning_count: int = 0
    missing_company_count: int
    missing_companies: list[ProspectImportMissingCompany]
    created_company_count: int = 0
    created_companies: list[ProspectImportCreatedCompany] = []
    message: str


async def _resolve_uploaded_company(session: DBSession, row: dict[str, str]) -> Company | None:
    from app.repositories.company import CompanyRepository

    company_fields = row_to_company_fields(row)
    domain = (company_fields.get("domain") or "").strip().lower()
    name = (company_fields.get("name") or "").strip()

    company: Company | None = None
    if domain and not domain.endswith(".unknown"):
        company = (
            await session.execute(select(Company).where(Company.domain == domain).limit(1))
        ).scalars().first()
    if not company and name:
        company = (
            await session.execute(
                select(Company).where(func.lower(Company.name) == name.lower()).limit(1)
            )
        ).scalars().first()
    if not company and name:
        # Looser dedupe so "OpenGov Inc." matches "OpenGov" and prevents
        # the placeholder-domain fallback from creating a shadow record.
        company = await CompanyRepository(session).get_by_normalized_name(name)
    return company


def _placeholder_company_domain(name: str) -> str:
    base = "".join(ch.lower() if ch.isalnum() else "-" for ch in (name or "").strip())
    slug = "-".join(part for part in base.split("-") if part) or "unknown-company"
    return f"{slug}.unknown"


async def _get_or_create_uploaded_placeholder_company(
    session: DBSession,
    row: dict[str, str],
    current_user: CurrentUser,
    auto_create: bool = False,
    sourcing_batch_id: UUID | None = None,
) -> tuple[Company | None, bool]:
    # Default behavior matches 553d929: accounts are created only via Account
    # Sourcing, and unmatched prospect rows import with company_id=NULL until
    # the proper account is added (backfill_orphans_for_company re-links them).
    #
    # When auto_create=True the caller is opting into a narrower carve-out:
    # if the 3-layer matcher in _resolve_uploaded_company (domain → exact name
    # → fuzzy normalized name) finds nothing, we create a Company row inline
    # so the rep's prospects are searchable by company name immediately.
    #
    # The DB has a BEFORE INSERT trigger (prevent_unbatched_company_insert)
    # that rejects any company row with sourcing_batch_id IS NULL. So when we
    # auto-create, the caller must supply a SourcingBatch — that's why this
    # function takes sourcing_batch_id. The batch puts the new accounts in
    # Account Sourcing under a labelled group ("Prospect import: <file>") so
    # ops can review them as a unit.
    company = await _resolve_uploaded_company(session, row)
    if company or not auto_create:
        return company, False

    if sourcing_batch_id is None:
        # Defensive: auto_create=True but caller forgot the batch. Treat as
        # "no match", same as the default path, rather than 500-ing the upload.
        return None, False

    company_fields = row_to_company_fields(row)
    name = (company_fields.get("name") or "").strip()
    if not name:
        return None, False

    raw_domain = (company_fields.get("domain") or "").strip().lower()
    domain = raw_domain or _placeholder_company_domain(name)

    enrichment_sources = {
        "created_from": "prospect_csv_upload",
        "uploaded_by": current_user.email,
        "uploaded_at": datetime.utcnow().isoformat(),
        "pending_icp_review": True,
    }

    company = Company(
        name=name,
        domain=domain,
        enrichment_sources=enrichment_sources,
        sourcing_batch_id=sourcing_batch_id,
    )
    session.add(company)
    await session.flush()  # populate company.id before the caller links contacts
    return company, True


@router.get("/", response_model=PaginatedResponse[ContactRead])
async def list_contacts(
    session: DBSession,
    pagination: Pagination,
    current_user: CurrentUser,
    company_id: Optional[UUID] = Query(default=None),
    q: Optional[str] = Query(default=None, description="Search by name, email, title, or company"),
    q_field: Optional[str] = Query(default=None, description="Scope `q` to a single column: name | email | company | title | phone | linkedin. Defaults to multi-field."),
    q_match: Optional[str] = Query(default=None, description="When scoped: 'exact' for whole-cell case-insensitive equality, 'contains' (default) for substring LIKE. Only honored alongside q_field."),
    persona: Optional[str] = Query(default=None),
    sequence_status: Optional[str] = Query(default=None),
    call_disposition: Optional[str] = Query(default=None, description="Filter by one or more call dispositions"),
    email_state: Optional[str] = Query(default=None, description="has_email | missing_email | verified | unverified"),
    sort_by: Optional[str] = Query(default=None, description="Sort key: name | first_name | last_name | company | email | title | created_at."),
    sort_dir: Optional[str] = Query(default=None, description="Sort direction: asc | desc. Defaults to asc."),
    ae_id: Optional[str] = Query(default=None, description="Filter by one or more assigned AE user IDs"),
    sdr_id: Optional[str] = Query(default=None, description="Filter by one or more assigned SDR user IDs"),
    owner_id: Optional[str] = Query(default=None, description="Filter by one or more user IDs across AE or SDR ownership"),
    scope_any_match: bool = Query(default=False, description="When true, ownership filters match AE or SDR ownership instead of requiring each selected role filter"),
    prospect_only: bool = Query(default=False, description="Exclude internal/generated contacts and obvious company mismatches"),
    timezone: Optional[str] = Query(default=None, description="Filter by one or more timezones (comma-separated, e.g. 'Asia/Kolkata,America/New_York')"),
):
    """
    Returns contacts with company_name populated via a single SQL JOIN.

    Visibility: every authenticated user sees every contact in the workspace.
    Assignment (`sdr_id` / `assigned_to_id`) is a label for ownership, NOT a
    visibility filter — this keeps the team on a shared view of the pipeline.
    Any per-rep filtering happens via the `ae_id` / `sdr_id` query params,
    which are driven by explicit user selection in the UI, not by the
    caller's own role.
    """
    repo = ContactRepository(session)
    items, total = await repo.list_with_company_name(
        company_id=company_id,
        q=q,
        q_field=q_field,
        q_match=q_match,
        persona=persona,
        sequence_status=sequence_status,
        call_disposition=call_disposition,
        email_state=email_state,
        sort_by=sort_by,
        sort_dir=sort_dir,
        ae_id=ae_id,
        sdr_id=sdr_id,
        owner_id=owner_id,
        scope_any_match=scope_any_match,
        prospect_only=prospect_only,
        timezone=timezone,
        skip=pagination.skip,
        limit=pagination.limit,
    )
    return PaginatedResponse.build(items, total, pagination.skip, pagination.limit)


@router.post("/admin/purge-all")
async def purge_all_prospects(
    session: DBSession,
    current_user: CurrentUser,
    confirm: str = Query(default="", description="Must equal 'DELETE ALL PROSPECTS' to proceed"),
):
    """Admin-only: delete every contact and their FK dependents.

    Explicitly hard-to-invoke: caller must be an admin AND supply the exact
    confirmation phrase. Intended for migration resets, not day-to-day use.
    Deals themselves survive; only deal_contacts (stakeholder links) go away.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    if confirm != "DELETE ALL PROSPECTS":
        raise HTTPException(
            status_code=400,
            detail="Pass ?confirm=DELETE+ALL+PROSPECTS to confirm. This is irreversible.",
        )

    # Count for reporting
    pre_count = (
        await session.execute(select(func.count(Contact.id)))
    ).scalar_one()

    # Order matters: dependents first. Use raw DELETE for speed on large tables.
    from sqlalchemy import delete as _sql_delete
    from app.models.outreach import OutreachSequence, OutreachStep
    from app.models.deal import DealContact
    from app.models.reminder import Reminder
    from app.models.angel import AngelMapping
    from app.models.activity import Activity

    # Null-out activity links (keep activity history)
    await session.execute(
        Activity.__table__.update().values(contact_id=None).where(Activity.contact_id.is_not(None))
    )
    # Delete outreach steps via their parent sequences
    seq_ids_subq = select(OutreachSequence.id)
    await session.execute(_sql_delete(OutreachStep).where(OutreachStep.sequence_id.in_(seq_ids_subq)))
    await session.execute(_sql_delete(OutreachSequence))
    # Delete deal_contacts (stakeholder links) — deals themselves stay
    await session.execute(_sql_delete(DealContact))
    # Delete reminders + angel mappings
    await session.execute(_sql_delete(Reminder))
    await session.execute(_sql_delete(AngelMapping))
    # Finally the contacts
    await session.execute(_sql_delete(Contact))
    await session.commit()

    return {
        "deleted_contacts": pre_count,
        "message": f"Purged {pre_count} prospects and their sequences, stakeholder links, reminders, and angel mappings. Deals and activity history retained.",
    }


@router.post("/", response_model=ContactRead, status_code=201)
async def create_contact(payload: ContactCreate, session: DBSession, _user: CurrentUser):
    # No hygiene gate on manual adds — when a rep explicitly types a prospect
    # into the form, that's intent. We still need *something* to identify the
    # row, so we only reject truly empty submissions.
    if not any([
        (payload.first_name or "").strip(),
        (payload.last_name or "").strip(),
        (payload.email or "").strip(),
        (payload.title or "").strip(),
        (payload.linkedin_url or "").strip(),
    ]):
        raise HTTPException(status_code=422, detail="Provide at least a name, email, title, or LinkedIn URL.")

    contact = Contact(**payload.model_dump())

    # Auto-assign to the creator (unless already set) so a rep who manually
    # adds a prospect actually sees it in their scoped list. Admins creating
    # contacts on behalf of someone else can leave it unassigned.
    if _user.role != "admin":
        if not contact.assigned_to_id and not contact.sdr_id:
            if _user.role == "sdr":
                contact.sdr_id = _user.id
                contact.sdr_name = getattr(_user, "name", None) or _user.email
            else:
                contact.assigned_to_id = _user.id
                contact.assigned_rep_email = _user.email

    current_enrichment = contact.enrichment_data if isinstance(contact.enrichment_data, dict) else {}
    current_enrichment.setdefault("source", "manual_prospect")
    current_enrichment.setdefault("uploaded_by", _user.email)
    current_enrichment.setdefault("uploaded_at", datetime.utcnow().isoformat())
    contact.enrichment_data = current_enrichment
    if not contact.persona:
        contact.persona = classify_persona(contact)

    # Seed the prospect's progress tracker using the workspace's current
    # Sequence Settings (Email D0 / LinkedIn D3 / Call D7 etc). This only
    # runs for fresh prospects — the refresh helper refuses to overwrite
    # the plan once the sequence has started.
    company_for_tz = None
    if contact.company_id:
        company_for_tz = await session.get(Company, contact.company_id)
        if company_for_tz:
            ws_schedule = await load_workspace_sequence_schedule(session)
            refresh_contact_sequence_plan(contact, company_for_tz, workspace_schedule=ws_schedule)

    # Infer timezone from phone country-code + company HQ when the rep
    # didn't supply one. Saves the "where are they / when should I call"
    # guess-work on every cold call.
    if not contact.timezone:
        from app.services.timezone_infer import infer_timezone

        contact.timezone = infer_timezone(
            phone=contact.phone,
            company_hq=getattr(company_for_tz, "headquarters", None),
            company_region=getattr(company_for_tz, "region", None),
            company_name=getattr(company_for_tz, "name", None),
        )

    saved = await ContactRepository(session).save(contact)
    return await to_contact_read(session, saved)


@router.get("/{contact_id}", response_model=ContactRead)
async def get_contact(contact_id: UUID, session: DBSession):
    contact = await ContactRepository(session).get_or_raise(contact_id)
    return await to_contact_read(session, contact)


@router.put("/{contact_id}", response_model=ContactRead)
async def update_contact(contact_id: UUID, payload: ContactUpdate, session: DBSession, _user: CurrentUser):
    repo = ContactRepository(session)
    contact = await repo.get_or_raise(contact_id)
    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        # Strip timezone info so asyncpg doesn't mix aware/naive datetimes
        if isinstance(value, datetime) and value.tzinfo is not None:
            value = value.replace(tzinfo=None)
        setattr(contact, key, value)
    if "title" in update_data or "seniority" in update_data:
        contact.persona = classify_persona(contact)

    # If the rep updated the phone (or the contact still has no timezone),
    # re-run inference. A rep explicitly setting `timezone` always wins.
    if "timezone" not in update_data:
        phone_changed = "phone" in update_data
        if phone_changed or not contact.timezone:
            from app.services.timezone_infer import infer_timezone

            company_for_tz = await session.get(Company, contact.company_id) if contact.company_id else None
            inferred = infer_timezone(
                phone=contact.phone,
                company_hq=getattr(company_for_tz, "headquarters", None),
                company_region=getattr(company_for_tz, "region", None),
                company_name=getattr(company_for_tz, "name", None),
            )
            if inferred:
                contact.timezone = inferred

    contact.updated_at = datetime.utcnow()
    saved = await repo.save(contact)

    # Server-side source of truth for the rep-driven state machine: when a
    # call or LinkedIn outcome was just logged, derive the correct
    # sequence_status, pause Instantly if this was a dead-end disposition, and
    # refresh system tasks (book-the-meeting, retry-call, etc.) so the rep
    # sees an accurate next-best-action without waiting for a cron.
    if "call_disposition" in update_data:
        await apply_call_disposition_effects(
            session, saved, disposition=update_data.get("call_disposition")
        )
    if "linkedin_status" in update_data:
        await apply_linkedin_status_effects(
            session, saved, linkedin_status=update_data.get("linkedin_status")
        )
    if "call_disposition" in update_data or "linkedin_status" in update_data:
        await session.commit()
        await session.refresh(saved)

    return await to_contact_read(session, saved)


@router.delete("/bulk", status_code=204)
async def bulk_delete_contacts(session: DBSession, _admin: AdminUser):
    """Delete all contacts. Admin only."""
    repo = ContactRepository(session)
    await repo.delete_all()


@router.delete("/{contact_id}", status_code=204)
async def delete_contact(contact_id: UUID, session: DBSession, _user: CurrentUser):
    repo = ContactRepository(session)
    await repo.get_or_raise(contact_id)
    await repo.delete_with_cascade(contact_id)


@router.get("/{contact_id}/timeline")
async def get_contact_timeline(
    contact_id: UUID,
    session: DBSession,
    _user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=500),
):
    """Unified chronological timeline: activities + meetings, newest first."""
    await ContactRepository(session).get_or_raise(contact_id)
    return {"items": await build_contact_timeline(session, contact_id, limit=limit)}


@router.get("/{contact_id}/sequence-lifecycle")
async def get_sequence_lifecycle(
    contact_id: UUID, session: DBSession, _user: CurrentUser
):
    """Full reconciled cadence for one contact: every step's actual state,
    timestamps, and any detected issues (stalled sequence, overdue step,
    bounced email, paused campaign). Drives the lifecycle drawer."""
    from app.services.sequence_lifecycle import build_sequence_lifecycle

    payload = await build_sequence_lifecycle(session, contact_id)
    if payload.get("error"):
        raise NotFoundError(payload["error"])
    return payload


class LifecycleSummariesPayload(SQLModel):
    contact_ids: list[UUID]


@router.post("/sequence-lifecycle/summaries")
async def post_sequence_lifecycle_summaries(
    payload: LifecycleSummariesPayload,
    session: DBSession,
    _user: CurrentUser,
):
    """Compact per-contact cadence summary for the Prospecting list view.
    Rep sees 'Day 7 · 2/5 · overdue' inline on the row without opening the
    drawer."""
    from app.services.sequence_lifecycle import (
        build_sequence_lifecycle_summaries,
    )

    summaries = await build_sequence_lifecycle_summaries(
        session, payload.contact_ids[:200]
    )
    # Return with string keys since JSON can't carry UUID keys
    return {"summaries": {str(k): v for k, v in summaries.items()}}


@router.get("/{contact_id}/precall-brief")
async def get_precall_brief(contact_id: UUID, session: DBSession, _user: CurrentUser):
    """Return the full pre-call brief for a contact.

    Read-only, assembled from existing DB state, no AI or network calls — so
    the rep can tap 'Call' and have a complete brief in one API round-trip.
    """
    from app.services.precall_brief import build_precall_brief

    brief = await build_precall_brief(session, contact_id)
    if brief.get("error"):
        raise NotFoundError(brief["error"])
    return brief


@router.post("/{contact_id}/enrich")
async def enrich_contact(contact_id: UUID, session: DBSession, _user: CurrentUser):
    repo = ContactRepository(session)
    contact = await repo.get_or_raise(contact_id)

    from app.clients.hunter import HunterClient
    hunter = HunterClient()
    enriched_fields: list[str] = []

    if contact.email:
        try:
            result = await hunter.verify_email(contact.email)
            if result:
                new_verified = result.get("result") == "deliverable"
                if new_verified != contact.email_verified:
                    contact.email_verified = new_verified
                    enriched_fields.append("email_verified")
        except Exception:
            pass

    old_persona = contact.persona
    contact.persona = classify_persona(contact)
    if contact.persona != old_persona:
        enriched_fields.append("persona")

    contact.updated_at = datetime.utcnow()
    await repo.save(contact)
    contact_read = await to_contact_read(session, contact)
    return {
        "contact_id": str(contact_id),
        "status": "enriched",
        "fields_updated": enriched_fields,
        "contact": contact_read,
    }


@router.post("/discover/{company_id}", response_model=list[ContactRead], status_code=201)
async def discover_contacts(company_id: UUID, session: DBSession, _user: CurrentUser):
    """
    Call Hunter domain-search for the given company and create any new contacts found.
    Skips duplicates by email. Returns the newly created contacts.
    """
    from app.repositories.company import CompanyRepository
    from app.clients.hunter import HunterClient
    from app.services.persona_classifier import classify_persona
    from sqlmodel import select

    company = await CompanyRepository(session).get_or_raise(company_id)

    # If the company was imported without a real domain, try to resolve it via AI first
    if company.domain.endswith(".unknown"):
        from app.services.domain_resolver import resolve_and_update_domain
        resolved = await resolve_and_update_domain(company, session)
        if not resolved:
            return []  # Can't search Hunter without a real domain

    hunter = HunterClient()
    hunter_data = await hunter.domain_search(company.domain)
    raw_contacts = (hunter_data or {}).get("contacts", [])

    created: list[Contact] = []
    for c in raw_contacts:
        email = (c.get("email") or "").strip()
        if not email:
            continue
        # Use first-row existence check to tolerate historical duplicate rows.
        existing = await session.execute(
            select(Contact).where(Contact.email == email).limit(1)
        )
        if existing.scalars().first():
            continue
        first = (c.get("first_name") or "").strip()
        last = (c.get("last_name") or "").strip()
        if not first and not last:
            prefix = email.split("@")[0]
            parts = prefix.replace(".", " ").replace("_", " ").split()
            first = parts[0].capitalize() if parts else prefix
            last = parts[1].capitalize() if len(parts) > 1 else ""
        if not is_valid_prospect_candidate(
            first_name=first,
            last_name=last,
            email=email,
            title=c.get("title"),
            linkedin_url=c.get("linkedin_url"),
        ):
            continue
        contact = Contact(
            first_name=first,
            last_name=last,
            email=email,
            title=c.get("title"),
            linkedin_url=c.get("linkedin_url"),
            company_id=company.id,
        )
        contact.persona = classify_persona(contact)
        session.add(contact)
        created.append(contact)

    await session.commit()
    for c in created:
        await session.refresh(c)

    reads = [ContactRead.model_validate(c) for c in created]
    await apply_contact_tracking(session, reads)
    return reads


@router.post("/import-csv", response_model=ProspectImportResponse, status_code=201)
async def import_contacts_csv(
    current_user: CurrentUser,
    session: DBSession,
    file: UploadFile = File(...),
    auto_create_companies: bool = Form(False),
):
    await require_workspace_permission(session, current_user, "prospect_migration")

    lower_name = (file.filename or "").lower()
    if not (lower_name.endswith(".csv") or lower_name.endswith(".xlsx")):
        raise HTTPException(status_code=400, detail="File must be a .csv or .xlsx")

    content = await file.read()
    rows = parse_prospect_upload_file(file.filename or "prospects.csv", content)
    if not rows:
        raise HTTPException(status_code=400, detail="No rows found in the upload")

    # Load the workspace sequence schedule once so every imported prospect's
    # progress tracker reflects the current Email/Call/LinkedIn cadence.
    ws_schedule = await load_workspace_sequence_schedule(session)

    created_count = 0
    updated_count = 0
    skipped_count = 0
    warning_count = 0
    touched_company_ids: set[UUID] = set()
    missing_companies: dict[str, ProspectImportMissingCompany] = {}
    # Track companies the importer just created (only populated when
    # auto_create_companies=True). Keyed by company id so we can queue ICP
    # enrichment once per company and return a deduped summary to the caller.
    created_companies: dict[UUID, ProspectImportCreatedCompany] = {}

    # The DB trigger prevent_unbatched_company_insert requires every new
    # company row to carry a sourcing_batch_id. When auto_create is on, we
    # lazily create a single SourcingBatch the first time we need to insert
    # a company, so all newly-created accounts land in Account Sourcing under
    # one labelled group. If auto_create is off, or if every prospect's
    # company already exists, no batch is created — no pollution.
    from app.models.sourcing_batch import SourcingBatch

    auto_create_batch: SourcingBatch | None = None

    async def _ensure_auto_create_batch() -> UUID | None:
        nonlocal auto_create_batch
        if not auto_create_companies:
            return None
        if auto_create_batch is not None:
            return auto_create_batch.id
        auto_create_batch = SourcingBatch(
            filename=f"Prospect import: {file.filename or 'upload.csv'}",
            status="completed",  # we inline the work; no Celery batch task runs
            total_rows=len(rows),
            created_by_id=current_user.id,
            created_by_name=current_user.name,
            created_by_email=current_user.email,
            meta={
                "created_from": "prospect_csv_upload",
                "pending_icp_review": True,
                "upload_mode": "inline_from_contacts_import",
            },
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(auto_create_batch)
        await session.flush()  # populate batch id
        return auto_create_batch.id

    for row in rows:
        batch_id_for_row = await _ensure_auto_create_batch() if auto_create_companies else None
        company, created_placeholder_company = await _get_or_create_uploaded_placeholder_company(
            session, row, current_user,
            auto_create=auto_create_companies,
            sourcing_batch_id=batch_id_for_row,
        )
        company_fields = row_to_company_fields(row)
        company_context = {
            "name": company.name if company else company_fields.get("name"),
            "headquarters": company.headquarters if company else company_fields.get("headquarters"),
            "region": company.region if company else company_fields.get("region"),
            "assigned_rep_email": company.assigned_rep_email if company else None,
            "recommended_outreach_lane": company.recommended_outreach_lane if company else None,
            "prospecting_profile": company.prospecting_profile if company else None,
            "enrichment_sources": company.enrichment_sources if company else None,
        }
        contact_fields = row_to_contact_fields(row, company_context)
        if not contact_fields:
            # Row has zero identifying data (no name, email, title, or LinkedIn).
            # These are genuinely empty and cannot be imported.
            skipped_count += 1
            continue
        # Hygiene is a warning, not a block — reps can fix suspicious rows
        # in-app after import rather than lose them at the upload step.
        if not is_valid_prospect_candidate(
            first_name=contact_fields.get("first_name"),
            last_name=contact_fields.get("last_name"),
            email=contact_fields.get("email"),
            title=contact_fields.get("title"),
            linkedin_url=contact_fields.get("linkedin_url"),
        ):
            warning_count += 1

        if not company:
            key = f"{(company_fields.get('domain') or '').strip().lower()}::{(company_fields.get('name') or '').strip().lower()}"
            current = missing_companies.get(key)
            if current:
                current.contacts_count += 1
            else:
                missing_companies[key] = ProspectImportMissingCompany(
                    name=(company_fields.get("name") or "Unknown company").strip(),
                    domain=(company_fields.get("domain") or "").strip() or None,
                    contacts_count=1,
                )
            raw_enrichment = contact_fields.get("enrichment_data") if isinstance(contact_fields.get("enrichment_data"), dict) else {}
            raw_enrichment["company_mapping"] = {
                "status": "unmapped",
                "suggested_company_name": (company_fields.get("name") or "").strip() or None,
                "suggested_company_domain": (company_fields.get("domain") or "").strip() or None,
                "hint": "Add this account in Account Sourcing, then map the prospect to that company.",
            }
            contact_fields["enrichment_data"] = raw_enrichment

        if created_placeholder_company and company is not None and company.id is not None:
            # The auto-create branch fired for this row. Track the new
            # company so we can queue ICP enrichment after commit and surface
            # it in the response. Do NOT add it to missing_companies — the
            # account now exists, so the rep should see it under "newly
            # created", not under "still needs to be added".
            existing_created = created_companies.get(company.id)
            if existing_created:
                existing_created.contacts_count += 1
            else:
                created_companies[company.id] = ProspectImportCreatedCompany(
                    id=company.id,
                    name=company.name,
                    domain=(company.domain or None),
                    contacts_count=1,
                )

        if company:
            touched_company_ids.add(company.id)
            contact_fields["assigned_to_id"] = contact_fields.get("assigned_to_id") or company.assigned_to_id
            contact_fields["assigned_rep_email"] = contact_fields.get("assigned_rep_email") or company.assigned_rep_email
            contact_fields["sdr_id"] = contact_fields.get("sdr_id") or company.sdr_id
            contact_fields["sdr_name"] = contact_fields.get("sdr_name") or company.sdr_name
            contact_fields["company_id"] = company.id

        raw_enrichment = contact_fields.get("enrichment_data") if isinstance(contact_fields.get("enrichment_data"), dict) else {}
        raw_enrichment["source"] = "prospect_csv_upload"
        raw_enrichment["uploaded_by"] = current_user.email
        raw_enrichment["uploaded_at"] = datetime.utcnow().isoformat()
        contact_fields["enrichment_data"] = raw_enrichment

        email = (contact_fields.get("email") or "").strip().lower() if isinstance(contact_fields.get("email"), str) else None
        first_name = (contact_fields.get("first_name") or "").strip()
        last_name = (contact_fields.get("last_name") or "").strip()

        existing = None
        if email:
            existing = (
                await session.execute(select(Contact).where(Contact.email == email).limit(1))
            ).scalars().first()
        if not existing and first_name and last_name:
            name_match_filters = [
                Contact.first_name == first_name,
                Contact.last_name == last_name,
            ]
            if company:
                name_match_filters.append(Contact.company_id == company.id)
            else:
                name_match_filters.append(Contact.company_id.is_(None))
            existing = (
                await session.execute(select(Contact).where(*name_match_filters).limit(1))
            ).scalars().first()

        if existing and company and existing.company_id and existing.company_id != company.id:
            skipped_count += 1
            continue

        if existing:
            changed = False
            for key, value in contact_fields.items():
                if value in (None, "", []):
                    continue
                if key == "enrichment_data":
                    current_enrichment = existing.enrichment_data if isinstance(existing.enrichment_data, dict) else {}
                    current_enrichment.update(value)
                    if current_enrichment != existing.enrichment_data:
                        existing.enrichment_data = current_enrichment
                        changed = True
                    continue
                if getattr(existing, key, None) != value:
                    setattr(existing, key, value)
                    changed = True
            if changed or not existing.persona:
                existing.persona = classify_persona(existing)
                existing.updated_at = datetime.utcnow()
                if company:
                    refresh_contact_sequence_plan(existing, company, workspace_schedule=ws_schedule)
                session.add(existing)
                updated_count += 1
            else:
                skipped_count += 1
        else:
            contact = Contact(**contact_fields)
            contact.persona = classify_persona(contact)
            if company:
                refresh_contact_sequence_plan(contact, company, workspace_schedule=ws_schedule)
            session.add(contact)
            created_count += 1

    await session.commit()

    for company_id in touched_company_ids:
        company = await session.get(Company, company_id)
        if not company:
            continue
        company_contacts = (
            await session.execute(select(Contact).where(Contact.company_id == company_id))
        ).scalars().all()
        refresh_company_prospecting_fields(company, company_contacts)
        session.add(company)
    await session.commit()

    missing_rows = sorted(missing_companies.values(), key=lambda item: (item.name.lower(), item.domain or ""))
    created_rows = sorted(created_companies.values(), key=lambda item: (item.name.lower(), item.domain or ""))

    # If we lazy-created a SourcingBatch but ended up creating zero companies
    # (e.g. every prospect's company turned out to exist after all), delete
    # the empty batch so Account Sourcing doesn't show an orphan "0 created"
    # entry. If we did create companies, sync the counters to reflect the
    # actual work done.
    if auto_create_batch is not None:
        if created_rows:
            auto_create_batch.created_companies = len(created_rows)
            auto_create_batch.processed_rows = len(created_rows)
            auto_create_batch.updated_at = datetime.utcnow()
            session.add(auto_create_batch)
            await session.commit()
        else:
            await session.delete(auto_create_batch)
            await session.commit()

    # Queue ICP enrichment for the freshly-created accounts so they don't sit
    # in Account Sourcing as bare placeholders. Uses the same single-company
    # task the re-enrich button on AccountSourcing calls. Best-effort: if
    # Celery is unreachable the import already succeeded, so we just log.
    if created_rows:
        try:
            from app.tasks.enrichment import icp_research_single_task

            for created in created_rows:
                icp_research_single_task.delay(str(created.id))
        except Exception:  # noqa: BLE001 — Celery brokers can fail in many ways
            import logging
            logging.getLogger(__name__).exception(
                "Failed to queue ICP enrichment for %d auto-created companies",
                len(created_rows),
            )

    message_parts = ["Prospects imported successfully."]
    if warning_count:
        message_parts.append(
            f"{warning_count} row{'s' if warning_count != 1 else ''} look{'s' if warning_count == 1 else ''} like a role mailbox or placeholder — review them in Prospecting."
        )
    if created_rows:
        message_parts.append(
            f"Beacon created {len(created_rows)} new account{'s' if len(created_rows) != 1 else ''} in Account Sourcing — ICP review pending."
        )
    if missing_rows:
        message_parts.append("Some prospects were imported without a company match. Add those accounts in Account Sourcing, then map the prospects to the company.")

    return ProspectImportResponse(
        imported_rows=len(rows),
        created_count=created_count,
        updated_count=updated_count,
        skipped_count=skipped_count,
        warning_count=warning_count,
        missing_company_count=len(missing_rows),
        missing_companies=missing_rows,
        created_company_count=len(created_rows),
        created_companies=created_rows,
        message=" ".join(message_parts),
    )


@router.get("/{contact_id}/brief")
async def get_contact_brief(contact_id: UUID, session: DBSession):
    """Generate AI stakeholder brief (Playwright + GPT-4o, 5-20s). Not cached."""
    from app.services.contact_intelligence import generate_contact_brief
    result = await generate_contact_brief(contact_id, session)
    if "error" in result:
        raise NotFoundError(result["error"])
    return result
