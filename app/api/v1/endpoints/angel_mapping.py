"""
Angel/Investor Mapping endpoints.

Manages angel investor profiles and their connections to prospects (contacts).
Supports CRUD for both angels and mappings, plus bulk import from structured data.
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import select

from app.core.dependencies import CurrentUser, DBSession, Pagination
from app.core.exceptions import NotFoundError
from app.models.angel import (
    AngelInvestor,
    AngelInvestorCreate,
    AngelInvestorRead,
    AngelInvestorUpdate,
    AngelMapping,
    AngelMappingCreate,
    AngelMappingRead,
    AngelMappingUpdate,
)
from app.models.company import Company
from app.models.contact import Contact

router = APIRouter(prefix="/angel-mapping", tags=["angel-mapping"])


# ── Angel Investors CRUD ────────────────────────────────────────────────────


@router.get("/investors", response_model=List[AngelInvestorRead])
async def list_investors(
    session: DBSession,
    _user: CurrentUser,
    pagination: Pagination,
):
    """List all angel investors."""
    result = await session.execute(
        select(AngelInvestor)
        .order_by(AngelInvestor.name)
        .offset(pagination.skip)
        .limit(pagination.limit)
    )
    return result.scalars().all()


@router.get("/investors/{investor_id}", response_model=AngelInvestorRead)
async def get_investor(investor_id: UUID, session: DBSession, _user: CurrentUser):
    """Get a single angel investor by ID."""
    investor = (
        await session.execute(
            select(AngelInvestor).where(AngelInvestor.id == investor_id)
        )
    ).scalar_one_or_none()
    if not investor:
        raise NotFoundError("Angel investor not found")
    return investor


@router.post("/investors", response_model=AngelInvestorRead, status_code=201)
async def create_investor(
    body: AngelInvestorCreate,
    session: DBSession,
    _user: CurrentUser,
):
    """Create a new angel investor profile."""
    investor = AngelInvestor(**body.model_dump())
    session.add(investor)
    await session.commit()
    await session.refresh(investor)
    return investor


@router.patch("/investors/{investor_id}", response_model=AngelInvestorRead)
async def update_investor(
    investor_id: UUID,
    body: AngelInvestorUpdate,
    session: DBSession,
    _user: CurrentUser,
):
    """Update an angel investor profile."""
    investor = (
        await session.execute(
            select(AngelInvestor).where(AngelInvestor.id == investor_id)
        )
    ).scalar_one_or_none()
    if not investor:
        raise NotFoundError("Angel investor not found")

    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(investor, key, value)
    investor.updated_at = datetime.utcnow()

    session.add(investor)
    await session.commit()
    await session.refresh(investor)
    return investor


@router.delete("/investors/{investor_id}", status_code=204)
async def delete_investor(investor_id: UUID, session: DBSession, _user: CurrentUser):
    """Delete an angel investor and all their mappings."""
    investor = (
        await session.execute(
            select(AngelInvestor).where(AngelInvestor.id == investor_id)
        )
    ).scalar_one_or_none()
    if not investor:
        raise NotFoundError("Angel investor not found")
    await session.delete(investor)
    await session.commit()


# ── Angel Mappings CRUD ─────────────────────────────────────────────────────


@router.get("/mappings", response_model=List[AngelMappingRead])
async def list_mappings(
    session: DBSession,
    _user: CurrentUser,
    pagination: Pagination,
    contact_id: Optional[UUID] = None,
    company_id: Optional[UUID] = None,
    angel_investor_id: Optional[UUID] = None,
    min_strength: Optional[int] = None,
):
    """List angel mappings with optional filters."""
    query = select(
        AngelMapping,
        Contact.first_name,
        Contact.last_name,
        Contact.title,
        Contact.linkedin_url,
        Company.name.label("company_name"),
        AngelInvestor.name.label("angel_name"),
        AngelInvestor.current_role.label("angel_current_role"),
        AngelInvestor.current_company.label("angel_current_company"),
    ).join(
        Contact, AngelMapping.contact_id == Contact.id
    ).outerjoin(
        Company, AngelMapping.company_id == Company.id
    ).join(
        AngelInvestor, AngelMapping.angel_investor_id == AngelInvestor.id
    )

    if contact_id:
        query = query.where(AngelMapping.contact_id == contact_id)
    if company_id:
        query = query.where(AngelMapping.company_id == company_id)
    if angel_investor_id:
        query = query.where(AngelMapping.angel_investor_id == angel_investor_id)
    if min_strength:
        query = query.where(AngelMapping.strength >= min_strength)

    query = query.order_by(AngelMapping.rank).offset(pagination.skip).limit(pagination.limit)
    result = await session.execute(query)
    rows = result.all()

    return [
        AngelMappingRead(
            id=mapping.id,
            contact_id=mapping.contact_id,
            company_id=mapping.company_id,
            angel_investor_id=mapping.angel_investor_id,
            strength=mapping.strength,
            rank=mapping.rank,
            connection_path=mapping.connection_path,
            why_it_works=mapping.why_it_works,
            recommended_strategy=mapping.recommended_strategy,
            contact_name=f"{first_name} {last_name}".strip(),
            contact_title=title,
            contact_linkedin=linkedin_url,
            company_name=company_name,
            angel_name=angel_name,
            angel_current_role=angel_current_role,
            angel_current_company=angel_current_company,
            created_at=mapping.created_at,
            updated_at=mapping.updated_at,
        )
        for mapping, first_name, last_name, title, linkedin_url, company_name,
            angel_name, angel_current_role, angel_current_company in rows
    ]


@router.post("/mappings", response_model=AngelMappingRead, status_code=201)
async def create_mapping(
    body: AngelMappingCreate,
    session: DBSession,
    _user: CurrentUser,
):
    """Create a new angel-to-prospect mapping."""
    # Validate references exist
    contact = (await session.execute(select(Contact).where(Contact.id == body.contact_id))).scalar_one_or_none()
    if not contact:
        raise NotFoundError("Contact not found")
    angel = (await session.execute(select(AngelInvestor).where(AngelInvestor.id == body.angel_investor_id))).scalar_one_or_none()
    if not angel:
        raise NotFoundError("Angel investor not found")

    mapping = AngelMapping(**body.model_dump())
    session.add(mapping)
    await session.commit()
    await session.refresh(mapping)

    # Return with joined names
    company_name = None
    if mapping.company_id:
        company = (await session.execute(select(Company).where(Company.id == mapping.company_id))).scalar_one_or_none()
        company_name = company.name if company else None

    return AngelMappingRead(
        **mapping.model_dump(),
        contact_name=f"{contact.first_name} {contact.last_name}".strip(),
        contact_title=contact.title,
        contact_linkedin=contact.linkedin_url,
        company_name=company_name,
        angel_name=angel.name,
        angel_current_role=angel.current_role,
        angel_current_company=angel.current_company,
    )


@router.patch("/mappings/{mapping_id}", response_model=AngelMappingRead)
async def update_mapping(
    mapping_id: UUID,
    body: AngelMappingUpdate,
    session: DBSession,
    _user: CurrentUser,
):
    """Update an angel mapping."""
    mapping = (
        await session.execute(select(AngelMapping).where(AngelMapping.id == mapping_id))
    ).scalar_one_or_none()
    if not mapping:
        raise NotFoundError("Angel mapping not found")

    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(mapping, key, value)
    mapping.updated_at = datetime.utcnow()

    session.add(mapping)
    await session.commit()
    await session.refresh(mapping)

    # Fetch joined names
    contact = (await session.execute(select(Contact).where(Contact.id == mapping.contact_id))).scalar_one_or_none()
    angel = (await session.execute(select(AngelInvestor).where(AngelInvestor.id == mapping.angel_investor_id))).scalar_one_or_none()
    company_name = None
    if mapping.company_id:
        company = (await session.execute(select(Company).where(Company.id == mapping.company_id))).scalar_one_or_none()
        company_name = company.name if company else None

    return AngelMappingRead(
        **mapping.model_dump(),
        contact_name=f"{contact.first_name} {contact.last_name}".strip() if contact else None,
        contact_title=contact.title if contact else None,
        contact_linkedin=contact.linkedin_url if contact else None,
        company_name=company_name,
        angel_name=angel.name if angel else None,
        angel_current_role=angel.current_role if angel else None,
        angel_current_company=angel.current_company if angel else None,
    )


@router.delete("/mappings/{mapping_id}", status_code=204)
async def delete_mapping(mapping_id: UUID, session: DBSession, _user: CurrentUser):
    """Delete an angel mapping."""
    mapping = (
        await session.execute(select(AngelMapping).where(AngelMapping.id == mapping_id))
    ).scalar_one_or_none()
    if not mapping:
        raise NotFoundError("Angel mapping not found")
    await session.delete(mapping)
    await session.commit()


# ── Bulk Import ─────────────────────────────────────────────────────────────


class BulkAngelImportRow(BaseModel):
    """One row from the angel mapping Excel/CSV import."""
    company_name: str
    prospect_name: str
    prospect_title: Optional[str] = None
    prospect_linkedin: Optional[str] = None
    ownership_stage: Optional[str] = None
    pe_investors: Optional[str] = None
    vc_investors: Optional[str] = None
    strategic_investors: Optional[str] = None
    angel_1_name: Optional[str] = None
    angel_1_strength: Optional[int] = None
    angel_1_path: Optional[str] = None
    angel_1_why: Optional[str] = None
    angel_2_name: Optional[str] = None
    angel_2_strength: Optional[int] = None
    angel_2_path: Optional[str] = None
    angel_2_why: Optional[str] = None
    angel_3_name: Optional[str] = None
    angel_3_strength: Optional[int] = None
    angel_3_path: Optional[str] = None
    angel_3_why: Optional[str] = None
    recommended_strategy: Optional[str] = None


class BulkAngelImportRequest(BaseModel):
    rows: List[BulkAngelImportRow]


class BulkAngelImportResult(BaseModel):
    investors_created: int
    mappings_created: int
    companies_updated: int
    errors: List[str]


@router.post("/import", response_model=BulkAngelImportResult)
async def bulk_import(
    body: BulkAngelImportRequest,
    session: DBSession,
    _user: CurrentUser,
):
    """
    Bulk import angel mapping data (matches Sheet8 format from the Excel).
    Auto-creates angel investor records if they don't exist.
    Matches contacts by name + company, updates company investor fields.
    """
    investors_created = 0
    mappings_created = 0
    companies_updated = 0
    errors: List[str] = []

    # Cache angel investors by name to avoid duplicate lookups
    angel_cache: dict[str, AngelInvestor] = {}
    existing = (await session.execute(select(AngelInvestor))).scalars().all()
    for a in existing:
        angel_cache[a.name.strip().lower()] = a

    for i, row in enumerate(body.rows):
        try:
            # Find the contact by matching name + company
            name_parts = row.prospect_name.strip().split(None, 1)
            first_name = name_parts[0] if name_parts else ""
            last_name = name_parts[1] if len(name_parts) > 1 else ""

            # Try to find contact
            contact_query = select(Contact).join(
                Company, Contact.company_id == Company.id
            ).where(
                Contact.first_name.ilike(f"%{first_name}%"),
                Company.name.ilike(f"%{row.company_name.strip()}%"),
            )
            contact = (await session.execute(contact_query)).scalar_one_or_none()

            if not contact:
                errors.append(f"Row {i+1}: Contact '{row.prospect_name}' at '{row.company_name}' not found")
                continue

            # Update company investor fields if present
            if contact.company_id:
                company = (await session.execute(
                    select(Company).where(Company.id == contact.company_id)
                )).scalar_one_or_none()
                if company:
                    changed = False
                    if row.ownership_stage and not company.ownership_stage:
                        company.ownership_stage = row.ownership_stage
                        changed = True
                    if row.pe_investors and not company.pe_investors:
                        company.pe_investors = row.pe_investors
                        changed = True
                    if row.vc_investors and not company.vc_investors:
                        company.vc_investors = row.vc_investors
                        changed = True
                    if row.strategic_investors and not company.strategic_investors:
                        company.strategic_investors = row.strategic_investors
                        changed = True
                    if changed:
                        company.updated_at = datetime.utcnow()
                        session.add(company)
                        companies_updated += 1

            # Process angel connections (1, 2, 3)
            for rank, (angel_name, strength, path, why) in enumerate([
                (row.angel_1_name, row.angel_1_strength, row.angel_1_path, row.angel_1_why),
                (row.angel_2_name, row.angel_2_strength, row.angel_2_path, row.angel_2_why),
                (row.angel_3_name, row.angel_3_strength, row.angel_3_path, row.angel_3_why),
            ], start=1):
                if not angel_name or not strength:
                    continue

                # Get or create angel investor
                key = angel_name.strip().lower()
                if key not in angel_cache:
                    angel = AngelInvestor(name=angel_name.strip())
                    session.add(angel)
                    await session.flush()
                    angel_cache[key] = angel
                    investors_created += 1

                angel = angel_cache[key]

                # Create mapping
                mapping = AngelMapping(
                    contact_id=contact.id,
                    company_id=contact.company_id,
                    angel_investor_id=angel.id,
                    strength=max(1, min(5, strength)),
                    rank=rank,
                    connection_path=path,
                    why_it_works=why,
                    recommended_strategy=row.recommended_strategy if rank == 1 else None,
                )
                session.add(mapping)
                mappings_created += 1

        except Exception as e:
            errors.append(f"Row {i+1}: {str(e)}")

    await session.commit()
    return BulkAngelImportResult(
        investors_created=investors_created,
        mappings_created=mappings_created,
        companies_updated=companies_updated,
        errors=errors,
    )
