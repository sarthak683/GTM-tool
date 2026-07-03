from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlmodel import select

from app.core.dependencies import AdminUser, CurrentUser, DBSession, Pagination
from app.core.exceptions import NotFoundError
from app.models.company import Company, CompanyCreate, CompanyRead, CompanyUpdate
from app.models.deal import Deal, DealRead
from app.repositories.company import CompanyRepository, company_visibility_filter
from app.schemas.common import PaginatedResponse
from app.services.icp_scorer import score_company

router = APIRouter(prefix="/companies", tags=["companies"])


def _can_see_company(company: Company, user) -> bool:
    """Python mirror of ``company_visibility_filter`` for single-object guards.

    Admins see every company (including ``not_a_fit``); a non-admin only sees a
    company they own (AE or SDR) that is not flagged ``not_a_fit``. Use on
    single-company detail/update routes to 404 a company the caller can't see
    (so existence isn't leaked).
    """
    if user.role == "admin":
        return True
    owns = company.assigned_to_id == user.id or company.sdr_id == user.id
    return owns and company.account_status != "not_a_fit"


def _visible_company_selector_filter():
    # Global company selectors should only show source-of-truth accounts that
    # came through Account Sourcing/manual add, plus imported shells that are
    # already linked to deals. Deal-linked accounts need to stay searchable for
    # meeting/company mapping and prospect mapping.
    return or_(
        Company.sourcing_batch_id.isnot(None),
        select(Deal.id).where(Deal.company_id == Company.id).exists(),
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
    order_by = Company.icp_score.desc()
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
        order_by = func.similarity(Company.name, qval).desc()
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
    for key, value in update_data.items():
        setattr(company, key, value)
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
    for key, value in update_data.items():
        setattr(company, key, value)
    company.updated_at = datetime.utcnow()
    return await repo.save(company)


@router.delete("/{company_id}", status_code=204)
async def delete_company(company_id: UUID, session: DBSession, _admin: AdminUser):
    repo = CompanyRepository(session)
    await repo.get_or_raise(company_id)  # 404 if not found
    await repo.delete_with_cascade(company_id)


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
