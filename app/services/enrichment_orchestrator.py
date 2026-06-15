"""
Enrichment orchestrator — waterfall pattern.

Order: Apollo (firmographics) → Hunter (email signals) → BuiltWith (tech stack)
Each source is tried independently; partial failures don't block the rest.
After enrichment, ICP scoring is re-run so the score reflects the new data.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.services.icp_scorer import score_company
from app.services.log_safety import safe_error_message

logger = logging.getLogger(__name__)


async def enrich_company_by_id(
    company_id: UUID, session: AsyncSession
) -> Optional[Company]:
    company = await session.get(Company, company_id)
    if not company:
        logger.warning(f"enrich_company_by_id: company {company_id} not found")
        return None
    return await enrich_company(company, session)


async def enrich_company(company: Company, session: AsyncSession) -> Company:
    from app.clients.apollo import ApolloClient
    from app.clients.hunter import HunterClient
    from app.clients.builtwith import BuiltWithClient
    from app.clients.news import NewsClient

    # Step 0: resolve placeholder domain via AI before any real API calls
    if company.domain.endswith(".unknown"):
        from app.services.domain_resolver import resolve_and_update_domain
        resolved = await resolve_and_update_domain(company, session)
        if not resolved:
            logger.warning(
                f"Enrichment skipped for '{company.name}': could not resolve '.unknown' domain"
            )
            return company

    apollo = ApolloClient()
    hunter = HunterClient()
    builtwith = BuiltWithClient()
    news = NewsClient()

    enrichment_sources: dict = {}

    # ── 1. Apollo — company firmographics ────────────────────────────────────
    try:
        apollo_data = await apollo.enrich_company(company.domain)
        if apollo_data:
            enrichment_sources["apollo"] = apollo_data
            _apply_apollo(company, apollo_data)
            logger.info(f"Apollo enriched {company.domain}")
    except Exception as e:
        logger.error("Apollo enrichment failed for %s: %s", company.domain, safe_error_message(e))

    # ── 2. Hunter — email pattern signals + auto-create contacts ─────────────
    try:
        hunter_data = await hunter.domain_search(company.domain)
        if hunter_data:
            enrichment_sources["hunter"] = hunter_data
            await _create_contacts_from_hunter(company, hunter_data.get("contacts", []), session)
            logger.info(f"Hunter enriched {company.domain}")
    except Exception as e:
        logger.error("Hunter enrichment failed for %s: %s", company.domain, safe_error_message(e))

    # ── 3. BuiltWith — tech stack ─────────────────────────────────────────────
    try:
        builtwith_data = await builtwith.get_tech_stack(company.domain)
        if builtwith_data:
            enrichment_sources["builtwith"] = builtwith_data
            if builtwith_data.get("tech_stack"):
                company.tech_stack = builtwith_data["tech_stack"]
            logger.info(f"BuiltWith enriched {company.domain}")
    except Exception as e:
        logger.error("BuiltWith enrichment failed for %s: %s", company.domain, safe_error_message(e))

    # ── 4. NewsAPI — funding & PR signals ─────────────────────────────────────
    try:
        news_data = await news.get_company_signals(company.name, company.domain)
        if news_data:
            enrichment_sources["news"] = news_data
            # Funding news boosts ICP — mark it so scorer can use it
            if news_data.get("has_funding_news"):
                logger.info(f"Funding signal detected for {company.name}")
            logger.info(f"NewsAPI enriched {company.name}: {news_data['total_articles']} articles found")
    except Exception as e:
        logger.error("NewsAPI enrichment failed for %s: %s", company.name, safe_error_message(e))

    # ── Persist ───────────────────────────────────────────────────────────────
    company.enrichment_sources = enrichment_sources
    company.enriched_at = datetime.utcnow()

    # Re-score now that we have richer data
    company.icp_score, company.icp_tier = score_company(company)

    company.updated_at = datetime.utcnow()
    session.add(company)
    await session.commit()
    await session.refresh(company)
    return company


async def _create_contacts_from_hunter(
    company: Company, contacts: list, session: AsyncSession
) -> None:
    """Turn Hunter-discovered emails into Contact rows, skipping duplicates."""
    from app.models.contact import Contact
    from app.services.persona_classifier import classify_persona
    from app.services.prospect_hygiene import is_valid_prospect_candidate
    from sqlmodel import select

    for c in contacts:
        email = c.get("email")
        if not email:
            continue

        # Skip if already exists
        existing = await session.execute(select(Contact).where(Contact.email == email))
        if existing.scalar_one_or_none():
            continue

        first = (c.get("first_name") or "").strip()
        last = (c.get("last_name") or "").strip()

        # Fall back to parsing email prefix when Hunter has no name
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

    await session.commit()
    logger.info(f"Created contacts from Hunter for {company.domain}")


def _apply_apollo(company: Company, data: dict) -> None:
    """Write Apollo fields onto the company only when the value is non-None."""
    scalar_fields = [
        "name", "industry", "vertical", "employee_count",
        "arr_estimate", "funding_stage", "has_dap", "dap_tool",
    ]
    for field in scalar_fields:
        value = data.get(field)
        if value is not None:
            setattr(company, field, value)
