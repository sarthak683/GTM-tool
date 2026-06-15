"""
Company-level account brief service.

This endpoint powers the lightweight "AI Account Brief" used from company and
meeting detail pages. It is intentionally lighter than the full
pre-meeting-intelligence pipeline, but it now reuses the same sales logic:

  - company profile from DB
  - saved signals from the CRM
  - discovered contacts and committee coverage
  - cached enrichment data
  - lightweight website summary via httpx + GPT (no Playwright dependency)

The goal is a fast, sales-usable account planning brief, not just a generic
website summary.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.clients.claude import ClaudeClient
from app.clients.web_search import WebSearchClient
from app.models.company import Company
from app.models.contact import Contact
from app.models.signal import Signal
from app.services.pre_meeting_intelligence import (
    _build_attendee_intelligence,
    _build_why_now_signals,
    _canonical_persona,
)

logger = logging.getLogger(__name__)

ai_client = ClaudeClient()
web_client = WebSearchClient()


def _unwrap_cache_entry(cache: dict[str, Any], key: str) -> Any:
    value = cache.get(key)
    if isinstance(value, dict) and "data" in value:
        return value.get("data")
    return value


def _normalize_tech_stack(tech_stack: Any) -> dict[str, str]:
    if isinstance(tech_stack, dict):
        return {
            str(key): str(value)
            for key, value in tech_stack.items()
            if value is not None and str(value).strip()
        }
    if isinstance(tech_stack, list):
        items = [str(item).strip() for item in tech_stack if str(item).strip()]
        return {"tools": ", ".join(items)} if items else {}
    if isinstance(tech_stack, str) and tech_stack.strip():
        return {"tools": tech_stack.strip()}
    return {}


def _as_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_list(value: Any) -> list[str]:
    if isinstance(value, dict) and "items" in value:
        value = value["items"]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _signal_to_dict(signal: Signal) -> dict[str, Any]:
    return {
        "title": signal.title,
        "summary": signal.summary,
        "url": signal.url,
        "source": signal.source,
        "signal_type": signal.signal_type,
        "published_at": signal.published_at.isoformat() if signal.published_at else None,
    }




async def _generate_sales_ready_brief(
    *,
    company: Company,
    company_profile: dict[str, Any],
    website_summary: dict[str, Any] | None,
    cached_ai_summary: dict[str, Any],
    recent_signals: list[dict[str, Any]],
    why_now_signals: list[dict[str, Any]],
    stakeholder_cards: list[dict[str, Any]],
    committee_coverage: dict[str, Any],
    priorities: list[str],
) -> str:
    if ai_client.mock:
        logger.warning("AI client not configured — skipping account brief generation for %s", company.name)
        return None

    signal_lines = "\n".join(
        f"- {item.get('title')}: {item.get('summary') or item.get('detail') or ''}".strip()
        for item in recent_signals[:5]
        if item.get("title")
    ) or "- None captured."

    why_now_lines = "\n".join(
        f"- {item.get('detail')}"
        for item in why_now_signals[:5]
        if item.get("detail")
    ) or "- No clear timing hook found."

    stakeholder_lines = "\n".join(
        f"- {card.get('name')} | {card.get('title') or card.get('role_label') or 'Stakeholder'} | "
        f"Focus: {card.get('likely_focus') or 'Unknown'}"
        for card in stakeholder_cards[:5]
    ) or "- No stakeholders mapped yet."

    missing_role_lines = "\n".join(
        f"- {item.get('label')}: {item.get('why') or 'Role not yet mapped.'}"
        for item in (committee_coverage.get("missing_roles") or [])[:4]
        if item.get("label")
    ) or "- No major committee gaps identified."

    priority_lines = "\n".join(f"- {item}" for item in priorities[:5]) or "- None captured."
    website_extract = (
        _as_text((website_summary or {}).get("extract"))
        or _as_text((website_summary or {}).get("description"))
        or _as_text(cached_ai_summary.get("description"))
        or "No website summary available."
    )

    system = (
        "You are a senior enterprise AE preparing for outbound and first-meeting prep at Beacon.li. "
        "Write a concise but useful account brief in markdown. "
        "Use exactly these sections:\n"
        "## Snapshot\n"
        "## Why Now\n"
        "## Who To Engage\n"
        "## Angle For Beacon\n"
        "## Risks / Gaps\n\n"
        "Rules:\n"
        "- Be specific and grounded in the provided evidence.\n"
        "- Use bullets under each section.\n"
        "- Favor practical sales guidance over generic company summaries.\n"
        "- Mention uncertainty where the evidence is thin.\n"
    )
    user = f"""
Company Profile:
- Name: {company_profile.get('name')}
- Domain: {company_profile.get('domain')}
- Industry: {company_profile.get('industry') or 'Unknown'}
- Employees: {company_profile.get('employee_count') or 'Unknown'}
- Funding: {company_profile.get('funding_stage') or 'Unknown'}
- ICP Score: {company_profile.get('icp_score') or 'Unknown'}
- ICP Tier: {company_profile.get('icp_tier') or 'Unknown'}
- Tech Stack: {", ".join(f"{k}: {v}" for k, v in (company_profile.get('tech_stack') or {}).items()) or 'Unknown'}

Website Summary:
{website_extract}

Recent Signals:
{signal_lines}

Why Now Signals:
{why_now_lines}

Stakeholder Coverage:
{stakeholder_lines}

Committee Coverage Score:
{committee_coverage.get('coverage_score', 0)}%

Missing Roles:
{missing_role_lines}

Prospecting Priorities:
{priority_lines}
"""

    try:
        response = await ai_client.complete(system, user, max_tokens=650)
        if response:
            return response
    except Exception as exc:
        logger.warning("Account brief generation failed: %s", exc)

    return None


async def generate_account_brief(company_id: UUID, session: AsyncSession) -> dict[str, Any]:
    """
    Generate a company-level account brief for outreach and first-meeting prep.
    """
    company = await session.get(Company, company_id)
    if not company:
        return {"error": "Company not found"}

    cache = (company.enrichment_cache or {}) if isinstance(company.enrichment_cache, dict) else {}
    cached_ai_summary = _unwrap_cache_entry(cache, "ai_summary")
    cached_ai_summary = cached_ai_summary if isinstance(cached_ai_summary, dict) else {}
    cached_web_scrape = _unwrap_cache_entry(cache, "web_scrape")
    cached_web_scrape = cached_web_scrape if isinstance(cached_web_scrape, dict) else {}
    cached_committee = _unwrap_cache_entry(cache, "committee_coverage")
    cached_committee = cached_committee if isinstance(cached_committee, dict) else {}
    cached_priorities = _unwrap_cache_entry(cache, "prospecting_priorities")
    cached_intent = _unwrap_cache_entry(cache, "intent_signals") or company.intent_signals or {}

    tech_stack = _normalize_tech_stack(company.tech_stack)

    company_profile = {
        "name": company.name,
        "domain": company.domain,
        "industry": company.industry,
        "vertical": company.vertical,
        "employee_count": company.employee_count,
        "funding_stage": company.funding_stage,
        "arr_estimate": company.arr_estimate,
        "icp_score": company.icp_score,
        "icp_tier": company.icp_tier,
        "has_dap": company.has_dap,
        "dap_tool": company.dap_tool,
        "tech_stack": tech_stack,
    }

    signals_result = await session.execute(
        select(Signal)
        .where(Signal.company_id == company_id)
        .order_by(Signal.created_at.desc())
        .limit(6)
    )
    signal_rows = list(signals_result.scalars().all())
    recent_signals = [_signal_to_dict(signal) for signal in signal_rows]

    if not recent_signals and company.name:
        try:
            live_news = await web_client.recent_news(company.name, company.domain or "")
            recent_signals = [
                {
                    "title": item.get("title"),
                    "summary": item.get("snippet"),
                    "url": item.get("url"),
                    "source": "live_search",
                    "signal_type": "news",
                    "published_at": None,
                }
                for item in live_news[:4]
                if item.get("title")
            ]
        except Exception as exc:
            logger.warning("Live news lookup failed for %s: %s", company.name, exc)

    contacts_result = await session.execute(
        select(Contact)
        .where(Contact.company_id == company_id)
        .order_by(Contact.created_at.desc())
        .limit(20)
    )
    contacts = list(contacts_result.scalars().all())

    stakeholders = [
        {
            "id": str(contact.id),
            "name": f"{contact.first_name} {contact.last_name}".strip(),
            "title": contact.title,
            "email": contact.email,
            "persona": _canonical_persona(contact.persona, contact.persona_type),
            "persona_type": contact.persona_type,
            "seniority": contact.seniority,
            "linkedin_url": contact.linkedin_url,
            "email_verified": contact.email_verified,
        }
        for contact in contacts
    ]
    stakeholder_intelligence = _build_attendee_intelligence([], stakeholders)

    website_summary: dict[str, Any] | None = None
    if company.domain and not company.domain.endswith(".unknown"):
        try:
            website_summary = await web_client.company_website_summary(
                company.domain,
                company.name,
                ai_client,
            )
        except Exception as exc:
            logger.warning("Website summary lookup failed for %s: %s", company.domain, exc)

    why_now_signals = _build_why_now_signals(
        company_profile=company_profile,
        website_analysis=None,
        recent_news=recent_signals,
        intent_signals=cached_intent if isinstance(cached_intent, dict) else {},
        google_news=[],
    )

    committee_coverage = (
        cached_committee
        if isinstance(cached_committee, dict) and cached_committee.get("coverage_score") is not None
        else stakeholder_intelligence.get("committee_coverage", {})
    )
    stakeholder_cards = stakeholder_intelligence.get("stakeholder_cards", [])
    priorities = _as_list(cached_priorities)

    brief_text = await _generate_sales_ready_brief(
        company=company,
        company_profile=company_profile,
        website_summary=website_summary,
        cached_ai_summary=cached_ai_summary if isinstance(cached_ai_summary, dict) else {},
        recent_signals=recent_signals,
        why_now_signals=why_now_signals,
        stakeholder_cards=stakeholder_cards,
        committee_coverage=committee_coverage if isinstance(committee_coverage, dict) else {},
        priorities=priorities,
    )

    scraped = {
        "title": (website_summary or {}).get("title") or company.name,
        "description": (website_summary or {}).get("description") or _as_text(cached_ai_summary.get("description")) or "",
        "body_text": (website_summary or {}).get("extract") or _as_text(cached_web_scrape.get("text")) or "",
        "about_text": _as_text(cached_web_scrape.get("about_text")) or "",
        "error": None if website_summary or cached_web_scrape else "Website summary unavailable",
    }

    return {
        "company_id": str(company_id),
        "company_name": company.name,
        "domain": company.domain,
        "scraped": scraped,
        "news_signals": recent_signals,
        "tech_stack": tech_stack,
        "brief": brief_text,
        "stakeholder_summary": stakeholder_intelligence,
        "committee_coverage": committee_coverage,
        "why_now_signals": why_now_signals,
        "prospecting_priorities": priorities,
    }
