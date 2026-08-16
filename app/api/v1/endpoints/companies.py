from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlmodel import select

from app.core.dependencies import AdminUser, CurrentUser, DBSession, Pagination
from app.core.exceptions import NotFoundError
from app.models.company import Company, CompanyCreate, CompanyRead, CompanyUpdate, INACTIVE_ACCOUNT_STATUSES
from app.models.deal import Deal, DealRead
from app.repositories.company import CompanyRepository, company_visibility_filter
from app.schemas.common import PaginatedResponse
from app.services.icp_scorer import score_company
from app.services.sdr_reassignment import sync_company_sdr_assignment_to_contacts

router = APIRouter(prefix="/companies", tags=["companies"])


def _can_see_company(company: Company, user) -> bool:
    """Python mirror of ``company_visibility_filter`` for single-object guards.

    Admins see every company; a non-admin sees a company they own (AE or SDR).
    Ownership is the ONLY gate here — deliberately NOT the disabled-status
    check the list filter applies: an owner must be able to OPEN their parked
    (not_a_fit/dnd) account to review or re-enable it. Lists hide parked
    accounts by default; direct access never dead-ends. Use on single-company
    detail/update routes to 404 a company the caller can't see (so existence
    isn't leaked).
    """
    if company.deleted_at is not None:
        return False  # soft-deleted: gone for everyone (404, no trash view yet)
    if user.role == "admin":
        return True
    return company.assigned_to_id == user.id or company.sdr_id == user.id


def _visible_company_selector_filter():
    # Global company selectors should only show source-of-truth accounts that
    # came through Account Sourcing/manual add, plus imported shells that are
    # already linked to deals. Deal-linked accounts need to stay searchable for
    # meeting/company mapping and prospect mapping. Soft-deleted accounts are
    # never valid selector targets.
    return and_(
        Company.deleted_at.is_(None),
        or_(
            Company.sourcing_batch_id.isnot(None),
            select(Deal.id).where(Deal.company_id == Company.id).exists(),
        ),
    )


async def _apply_company_update(session, company: Company, update_data: dict) -> None:
    if "additional_domains" in update_data:
        # Same alias validation as the account-sourcing update path.
        from app.services.company_lifecycle import validate_alias_domains

        try:
            update_data["additional_domains"] = (
                await validate_alias_domains(session, company, update_data.get("additional_domains"))
                or None
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    previous_sdr_id = company.sdr_id
    previous_account_status = company.account_status
    sdr_update_requested = any(key in update_data for key in ("sdr_id", "sdr_email", "sdr_name"))
    for key, value in update_data.items():
        setattr(company, key, value)
    if sdr_update_requested:
        await sync_company_sdr_assignment_to_contacts(session, company, previous_sdr_id)
    # Keep the disable cascade identical to the account-sourcing update path:
    # both routes can park an account, so both must pause its outreach.
    if (
        company.account_status in INACTIVE_ACCOUNT_STATUSES
        and previous_account_status not in INACTIVE_ACCOUNT_STATUSES
    ):
        from app.services.account_status import apply_account_disable_effects

        await apply_account_disable_effects(
            session, company, reason=f"account_status={company.account_status}"
        )


class DuplicateCheckRequest(BaseModel):
    names: list[str] = []
    domains: list[str] = []


class DuplicateCheckResponse(BaseModel):
    duplicate_names: list[str]   # names that already exist (lowercased)
    duplicate_domains: list[str] # domains that already exist (lowercased)


@router.post("/check-duplicates", response_model=DuplicateCheckResponse)
async def check_duplicates(payload: DuplicateCheckRequest, session: DBSession, _user: CurrentUser):
    """
    Given lists of company names and domains from a CSV preview, return which
    ones already exist in the DB. Single query per dimension — O(1) DB round-trips.
    """
    dup_names: list[str] = []
    dup_domains: list[str] = []

    if payload.names:
        normalised = [n.strip().lower() for n in payload.names if n.strip()]
        rows = await session.execute(
            select(func.lower(func.trim(Company.name))).where(
                func.lower(func.trim(Company.name)).in_(normalised)
            )
        )
        dup_names = list(rows.scalars().all())

    if payload.domains:
        normalised_d = [d.strip().lower() for d in payload.domains if d.strip()]
        rows = await session.execute(
            select(Company.domain).where(
                Company.domain.in_(normalised_d)
            )
        )
        dup_domains = list(rows.scalars().all())

    return DuplicateCheckResponse(
        duplicate_names=dup_names,
        duplicate_domains=dup_domains,
    )


@router.get("/", response_model=PaginatedResponse[CompanyRead])
async def list_companies(
    session: DBSession,
    _user: CurrentUser,
    pagination: Pagination,
    icp_tier: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="Filter by company name or domain (case-insensitive substring)."),
):
    repo = CompanyRepository(session)
    filters = [
        _visible_company_selector_filter(),
        company_visibility_filter(_user.id, _user.role == "admin"),
    ]
    if icp_tier:
        filters.append(Company.icp_tier == icp_tier)
    # Company.id tiebreaker: icp_score is heavily tied/NULL, and OFFSET
    # pagination without a unique sort key repeats and drops rows across pages.
    order_by = (Company.icp_score.desc(), Company.id)
    if q and q.strip():
        # Server-side search so selectors find any matching account, not just
        # the top-N-by-ICP slice the client happened to load. Substring OR
        # trigram fuzzy match (pg_trgm) so a typo like "Haily HR" still finds
        # "Hailey HR". Results ordered by closeness of the name match.
        qval = q.strip()
        like = f"%{qval}%"
        filters.append(
            or_(
                Company.name.ilike(like),
                Company.domain.ilike(like),
                func.similarity(Company.name, qval) > 0.3,
            )
        )
        order_by = (func.similarity(Company.name, qval).desc(), Company.id)
    items, total = await repo.list_paginated(
        *filters,
        skip=pagination.skip,
        limit=pagination.limit,
        order_by=order_by,
    )
    return PaginatedResponse.build(items, total, pagination.skip, pagination.limit)


@router.post("/", response_model=CompanyRead, status_code=201)
async def create_company(payload: CompanyCreate, session: DBSession, _user: CurrentUser):
    raise HTTPException(
        status_code=410,
        detail=(
            "Accounts can only be created from Account Sourcing. "
            "Use /api/v1/account-sourcing/companies/manual or upload a workbook from the Account Sourcing page."
        ),
    )


@router.get("/{company_id}", response_model=CompanyRead)
async def get_company(company_id: UUID, session: DBSession, _user: CurrentUser):
    company = await CompanyRepository(session).get_or_raise(company_id)
    if not _can_see_company(company, _user):
        # 404 (not 403) so a non-admin can't probe which company ids exist.
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.put("/{company_id}", response_model=CompanyRead)
async def update_company(company_id: UUID, payload: CompanyUpdate, session: DBSession, _user: CurrentUser):
    repo = CompanyRepository(session)
    company = await repo.get_or_raise(company_id)
    if not _can_see_company(company, _user):
        raise HTTPException(status_code=404, detail="Company not found")
    update_data = payload.model_dump(exclude_unset=True)
    await _apply_company_update(session, company, update_data)
    company.icp_score, company.icp_tier = score_company(company)
    company.updated_at = datetime.utcnow()
    return await repo.save(company)


@router.patch("/{company_id}", response_model=CompanyRead)
async def patch_company(company_id: UUID, payload: CompanyUpdate, session: DBSession, _user: CurrentUser):
    repo = CompanyRepository(session)
    company = await repo.get_or_raise(company_id)
    if not _can_see_company(company, _user):
        raise HTTPException(status_code=404, detail="Company not found")
    update_data = payload.model_dump(exclude_unset=True)
    await _apply_company_update(session, company, update_data)
    company.updated_at = datetime.utcnow()
    return await repo.save(company)


@router.delete("/{company_id}", status_code=204)
async def delete_company(company_id: UUID, session: DBSession, _admin: AdminUser):
    """SOFT-delete an account (admin).

    The old hard cascade destroyed the company's activities and its deals'
    stage history — deleting one account silently rewrote last quarter's
    demos/closed-won scorecards. Now: the company and its live deals get
    ``deleted_at``; contacts keep their rows and links (hidden from prospecting
    by the deleted-account gate); every activity and history row survives, so
    historical metrics never change. Restoring is a manual UPDATE (deleted_at
    = NULL) until a trash view exists.
    """
    from app.services.company_lifecycle import soft_delete_company

    repo = CompanyRepository(session)
    company = await repo.get_or_raise(company_id)  # 404 if not found
    if company.deleted_at is None:
        await soft_delete_company(session, company)
        await session.commit()


@router.get("/{company_id}/deals", response_model=List[DealRead])
async def get_company_deals(company_id: UUID, session: DBSession, _user: CurrentUser):
    result = await session.execute(
        select(Deal)
        .where(Deal.company_id == company_id)
        .order_by(Deal.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{company_id}/timeline")
async def get_company_timeline(
    company_id: UUID, session: DBSession, _user: CurrentUser, limit: int = 200
):
    """Account-level activity rollup across all of this company's contacts + deals."""
    from app.services.timeline import build_company_timeline

    return {"items": await build_company_timeline(session, company_id, limit=limit)}
