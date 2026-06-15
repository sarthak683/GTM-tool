"""Link orphaned deals and contacts to a newly created account.

Accounts are only created via Account Sourcing (manual add or workbook upload).
When a new account is created, any deals and contacts in the workspace that have
no company linked (company_id IS NULL) may have been waiting for this account —
this helper backfills those links.

Matching strategy:
  1. Contacts  -> match by email domain == company.domain (strict, high-confidence)
  2. Deals     -> match transitively through deal_contacts: any deal that has a
                  contact that was just linked inherits the same company_id
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal, DealContact
from app.models.meeting import Meeting


async def backfill_orphans_for_company(
    session: AsyncSession, company: Company
) -> dict[str, int]:
    if not company or not company.id:
        return {"contacts_linked": 0, "deals_linked": 0, "shadow_companies_absorbed": 0}

    domain: Optional[str] = (company.domain or "").strip().lower() or None
    shadow_companies_absorbed = 0

    if domain and not domain.endswith(".unknown"):
        shadow_stmt = select(Company).where(
            Company.id != company.id,
            Company.sourcing_batch_id.is_(None),
            or_(
                func.lower(Company.domain) == domain,
                func.lower(func.trim(Company.name)) == func.lower(func.trim(company.name)),
            ),
        )
        shadow_companies = (await session.execute(shadow_stmt)).scalars().all()
        for shadow in shadow_companies:
            if not shadow.id:
                continue
            await session.execute(
                update(Contact)
                .where(Contact.company_id == shadow.id)
                .values(company_id=company.id)
            )
            await session.execute(
                update(Deal)
                .where(Deal.company_id == shadow.id)
                .values(company_id=company.id)
            )
            await session.execute(
                update(Meeting)
                .where(Meeting.company_id == shadow.id)
                .values(company_id=company.id)
            )
            shadow_companies_absorbed += 1

    if not domain or domain.endswith(".unknown"):
        await session.flush()
        return {"contacts_linked": 0, "deals_linked": 0, "shadow_companies_absorbed": shadow_companies_absorbed}

    # 1) Link orphan contacts by email domain. Postgres position() lets us find
    #    the "@" and take everything after it without a Python roundtrip.
    contacts_stmt = select(Contact).where(
        Contact.company_id.is_(None),
        Contact.email.is_not(None),
        func.lower(func.split_part(Contact.email, "@", 2)) == domain,
    )
    contacts = (await session.execute(contacts_stmt)).scalars().all()

    contact_ids: list[UUID] = []
    for contact in contacts:
        contact.company_id = company.id
        session.add(contact)
        contact_ids.append(contact.id)

    # 2) Link orphan deals that share any of those contacts via deal_contacts.
    deals_linked = 0
    if contact_ids:
        deal_ids_stmt = (
            select(DealContact.deal_id)
            .where(DealContact.contact_id.in_(contact_ids))
            .distinct()
        )
        deal_ids = [row for row in (await session.execute(deal_ids_stmt)).scalars().all()]
        if deal_ids:
            result = await session.execute(
                update(Deal)
                .where(Deal.id.in_(deal_ids), Deal.company_id.is_(None))
                .values(company_id=company.id)
            )
            deals_linked = int(result.rowcount or 0)

    await session.flush()
    return {
        "contacts_linked": len(contact_ids),
        "deals_linked": deals_linked,
        "shadow_companies_absorbed": shadow_companies_absorbed,
    }
