"""
ICP Intelligence Pipeline — full-spectrum company research.

Given just a company name (+ optional domain), this service:
  1. Resolves the domain if missing
  2. Runs parallel web research: DuckDuckGo, website scraping, Google News
  3. Calls Apollo for firmographics and contacts
  4. Feeds ALL collected data to Claude with the exact TAL filter & ICP scoring
     criteria from the Beacon Onboarding ICPs methodology
  5. Returns structured JSON matching the ICP & Intent Analysis format

Each company takes 15-30s. Use Celery for batch processing.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import json
import logging
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import UUID

from app.config import settings
from app.services.log_safety import safe_error_message

logger = logging.getLogger(__name__)

# ── Research Orchestrator ─────────────────────────────────────────────────────


async def research_company(
    company_name: str,
    domain: str = "",
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Full-spectrum research on a single company.
    Returns a dict with all collected raw data + Claude's ICP analysis.
    """
    from app.clients.web_search import WebSearchClient
    from app.clients.apollo import ApolloClient

    ws = WebSearchClient()
    apollo = ApolloClient()
    hunter_key = settings.HUNTER_API_KEY

    domain = domain.strip().lower() if domain else ""
    has_domain = bool(domain) and not domain.endswith(".unknown")

    # ── Step 1: Resolve domain if missing ────────────────────────────────
    if not has_domain:
        domain = await _resolve_domain(ws, company_name)
        has_domain = bool(domain) and not domain.endswith(".unknown")

    # ── Step 2: Parallel data collection ─────────────────────────────────
    collected = await _collect_all_data(
        ws, apollo, company_name, domain, has_domain, hunter_key
    )

    # ── Step 3: Claude ICP Analysis ──────────────────────────────────────
    icp_analysis = await _run_icp_analysis(
        company_name=company_name,
        domain=domain,
        collected=collected,
        extra_context=extra_context,
    )

    return {
        "company_name": company_name,
        "domain": domain,
        "collected_at": datetime.utcnow().isoformat(),
        "raw_data": {
            "scraped_pages": collected.get("scraped", {}).get("pages_scraped", 0),
            "search_results_count": len(collected.get("general_search") or []),
            "apollo_found": bool(collected.get("apollo_company")),
            "contacts_found": len(collected.get("all_contacts") or []),
            "hunter_contacts_found": len(collected.get("hunter_contacts") or []),
            "news_results": (collected.get("news") or {}).get("total_articles", 0) if isinstance(collected.get("news"), dict) else len(collected.get("news") or []),
        },
        "icp_analysis": icp_analysis,
        # Pass contacts through for DB persistence (not stored in enrichment_cache)
        "_all_contacts": collected.get("all_contacts", []),
        "_collected": collected,
    }


async def _resolve_domain(ws, company_name: str) -> str:
    """Resolve to a verified company domain when possible."""
    from app.services.domain_resolver import resolve_company_domain

    resolved, _meta = await resolve_company_domain(company_name)
    return resolved or f"{company_name.lower().replace(' ', '')}.unknown"


async def _safe_apply_resolved_domain(session, company, resolved_domain: str) -> dict[str, Any]:
    """Apply a newly resolved domain unless it collides with another company."""
    from sqlmodel import select
    from app.models.company import Company

    normalized = (resolved_domain or "").strip().lower()
    current = (company.domain or "").strip().lower()
    if not normalized or normalized.endswith(".unknown"):
        return {"status": "unresolved"}
    if normalized == current:
        return {"status": "unchanged", "domain": normalized}

    existing = (
        await session.execute(
            select(Company).where(Company.domain == normalized, Company.id != company.id)
        )
    ).scalar_one_or_none()
    if existing:
        logger.warning(
            "ICP research: resolved domain '%s' for '%s' already belongs to company %s (%s); keeping existing domain '%s'",
            normalized,
            company.name,
            existing.id,
            existing.name,
            company.domain,
        )
        return {
            "status": "conflict",
            "domain": normalized,
            "conflict_company_id": str(existing.id),
            "conflict_company_name": existing.name,
            "kept_domain": company.domain,
        }

    company.domain = normalized
    return {"status": "updated", "domain": normalized}


async def _collect_all_data(
    ws, apollo, company_name: str, domain: str, has_domain: bool,
    hunter_key: str = "",
) -> dict[str, Any]:
    """Run all data collection in parallel where possible."""
    tasks = {}
    domain_hint = f" {domain}" if has_domain and domain and not domain.endswith(".unknown") else ""

    # Web scraping (if domain available)
    if has_domain:
        tasks["scraped"] = ws.scrape_company_pages(domain)

    # General company search
    tasks["general_search"] = ws.search(
        f'"{company_name}"{domain_hint} company enterprise SaaS what they do', max_results=5
    )

    # ICP-specific searches: PS/Implementation hiring
    tasks["ps_hiring"] = ws.search(
        f'"{company_name}"{domain_hint} hiring "professional services" OR "implementation" OR '
        f'"solutions engineer" OR "delivery" OR "onboarding" OR "customer success"',
        max_results=4
    )

    # Leadership / org moves
    tasks["leadership"] = ws.search(
        f'"{company_name}"{domain_hint} "VP" OR "hired" OR "appointed" OR "new CTO" OR '
        f'"Chief" OR "Head of" leadership 2025 OR 2026',
        max_results=4
    )

    # PR / Funding / Expansion
    tasks["funding"] = ws.search(
        f'"{company_name}"{domain_hint} funding OR raised OR acquisition OR partnership OR '
        f'expansion OR "Series" OR IPO 2024 OR 2025 OR 2026',
        max_results=5
    )

    # G2 Reviews / Case Studies (implementation complexity signals)
    tasks["reviews"] = ws.search(
        f'"{company_name}"{domain_hint} site:g2.com OR "case study" OR "implementation" OR '
        f'"took months" OR "complex" OR "configuration" OR review',
        max_results=4
    )

    # Events / Thought Leadership
    tasks["events"] = ws.search(
        f'"{company_name}"{domain_hint} conference OR webinar OR summit OR "thought leadership" '
        f'OR keynote 2025 OR 2026',
        max_results=3
    )

    # Negative signals: internal AI / agentic overlap
    tasks["ai_overlap"] = ws.search(
        f'"{company_name}"{domain_hint} "AI" OR "agentic" OR "automation" OR "built-in" OR '
        f'"internal AI" OR "machine learning" implementation',
        max_results=3
    )

    # News (recent) — use free Google News RSS instead of Serper
    from app.clients.news import NewsClient
    news_client = NewsClient()
    tasks["news"] = news_client.get_company_signals(company_name, domain)

    # Apollo firmographics + contacts
    if has_domain and apollo.api_key:
        tasks["apollo_company"] = apollo.enrich_company(domain)
        tasks["apollo_contacts"] = apollo.search_people(
            domain=domain,
            limit=10,
            seniorities=["c_suite", "vp", "director"],
        )

    # Hunter.io domain search — discovers contacts with verified emails
    if has_domain and hunter_key:
        tasks["hunter_contacts"] = _hunter_domain_search(domain, hunter_key)

    # Run all in parallel with timeout
    keys = list(tasks.keys())
    coros = list(tasks.values())

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=45,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Data collection timed out for {company_name}")
        results = [None] * len(coros)

    collected: dict[str, Any] = {}
    for key, result in zip(keys, results):
        if isinstance(result, Exception):
            logger.warning(
                "Research task '%s' failed for %s: %s",
                key,
                company_name,
                safe_error_message(result),
            )
            collected[key] = [] if key != "scraped" else {"text": "", "pages_scraped": 0}
        else:
            collected[key] = result

    # Merge Apollo + Hunter contacts into a unified list for Claude
    apollo_contacts = collected.get("apollo_contacts", [])
    hunter_contacts = collected.get("hunter_contacts", [])
    if not isinstance(apollo_contacts, list):
        apollo_contacts = []
    if not isinstance(hunter_contacts, list):
        hunter_contacts = []

    # Deduplicate by email, preferring Apollo data (richer)
    seen_emails: set[str] = set()
    all_contacts: list[dict] = []
    for c in apollo_contacts:
        if isinstance(c, dict):
            email = (c.get("email") or "").lower()
            if email:
                seen_emails.add(email)
            all_contacts.append({**c, "_source": "apollo"})
    for c in hunter_contacts:
        if isinstance(c, dict):
            email = (c.get("email") or "").lower()
            if email and email not in seen_emails:
                seen_emails.add(email)
                all_contacts.append({**c, "_source": "hunter"})

    collected["all_contacts"] = all_contacts
    collected["_company_name"] = company_name
    collected["_domain"] = domain

    return collected


async def _hunter_domain_search(domain: str, api_key: str) -> list[dict]:
    """
    Hunter.io domain search — finds contacts at a domain with verified emails.
    Returns list of {first_name, last_name, email, title, seniority, confidence,
    linkedin_url, department, email_type}.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(
                "https://api.hunter.io/v2/domain-search",
                params={
                    "domain": domain,
                    "api_key": api_key,
                    "limit": 15,
                    "seniority": "senior,executive,c_level",
                    "department": "executive,management,it",
                },
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json().get("data", {})
            emails = data.get("emails", [])

            return [
                {
                    "first_name": e.get("first_name") or "",
                    "last_name": e.get("last_name") or "",
                    "email": e.get("value") or "",
                    "title": e.get("position") or None,
                    "seniority": e.get("seniority") or None,
                    "confidence": e.get("confidence"),
                    "linkedin_url": e.get("linkedin") or None,
                    "department": e.get("department") or None,
                    "email_type": e.get("type") or None,  # personal vs generic
                    "email_verified": (
                        "verified" if e.get("verification", {}).get("status") == "valid"
                        else e.get("verification", {}).get("status", "unknown")
                    ),
                }
                for e in emails
                if e.get("value") and e.get("first_name")
            ]
    except Exception as exc:
        logger.warning("Hunter domain search failed for %s: %s", domain, safe_error_message(exc))
        return []


# ── Claude ICP Analysis ───────────────────────────────────────────────────────

_ICP_SYSTEM_PROMPT = """You are an expert B2B sales intelligence analyst for Beacon.li — an AI-powered implementation orchestration platform that automates enterprise SaaS deployments.

Your job: Analyze a company using Beacon's EXACT ICP & Intent methodology and produce a comprehensive, specific, actionable assessment that a sales rep can immediately use.

## BEACON'S TARGET ACCOUNT LIST (TAL) FILTER

### INCLUSION criteria (company MUST meet ALL):
1. **Enterprise SaaS** that is a **System-of-Record (SoR)** OR has **Complex Implementation** requirements
   - SoR signals: called "source of truth", "system of record", replaces spreadsheets/manual processes, central data hub, regulatory/compliance data
   - Complex Impl signals: multi-month deployments, requires SI partners, phased rollouts, dedicated PS/impl teams, sandbox environments, data migration, workflow configuration, role-based access setup
2. **Financial Capacity** for 150K+ ACV deals:
   - ARR >= $20M OR Total funding >= $50M OR Public company with revenue >= $50M OR Clear enterprise signals (Fortune 500 customers, gov contracts, etc.)

### EXCLUSION criteria (if ANY apply → exclude):
- Pure SMB/self-serve (no enterprise motion)
- Simple plug-and-play tools (Slack, Zoom, Canva)
- Consumer apps, hardware, or non-SaaS
- Marketplace/aggregator platforms
- Dev tools with no implementation complexity

### FIT TYPE classification:
- **System-of-Record**: Central data store, regulatory compliance, replaces manual processes
- **Complex Implementation**: Multi-month, needs SI/PS, phased rollouts, extensive config
- **Both**: Meets SoR AND Complex Impl criteria
- **Neither**: Fails both criteria → exclude from TAL

## ICP FIT SCORE (0-10 scale)

Score across 4 dimensions:
1. **SoR Signals (0-3)**: Evidence the product is a system of record
2. **Implementation Complexity (0-3)**: Evidence of complex deployments
3. **Financial Capacity (0-2)**: Revenue/funding for 150K+ ACV
4. **Domain Match (0-2)**: How well the company's domain aligns with Beacon's sweet spots (HCM, ERP, FinTech, InsureTech, HealthTech, LegalTech, Procurement, CPQ, Field Service)

## INTENT SCORE (0-10 scale)

### Positive Signals (each +2 max):
1. **PS/Impl Hiring**: Actively hiring implementation, PS, onboarding, deployment, solutions engineers
2. **Leadership/Org Moves**: New VP/C-suite hires, restructuring PS/CS orgs
3. **PR/Funding/Expansion**: Recent funding rounds, acquisitions, market expansion
4. **Events/Thought Leadership**: Speaking at conferences, publishing impl methodology
5. **Reviews/Case Studies**: G2/Gartner reviews mentioning implementation complexity, customer case studies showing multi-month deployments

### Negative Signals (each -2 max):
1. **Internal AI/Agentic Overlap**: Building their own implementation automation
2. **M&A/IPO/Strategic Constraints**: In acquisition mode, IPO quiet period
3. **PS/CS Contraction**: Laying off PS/CS teams, outsourcing delivery
4. **Build vs Buy for Impl. Auto**: Already built internal tools for implementation
5. **AI Acquisition for Impl**: Acquired companies doing what Beacon does

## ICP PERSONAS (who to target at this company)

Delivery & Implementation personas:
- VP Implementation/Deployment, VP Professional Services, VP Delivery
- Director of Implementation/PS, Head of Customer Onboarding
- Chief Customer Officer, SVP Customer Success

Professional Services personas:
- VP/Director Professional Services, PS Practice Lead
- Implementation Program Manager, Delivery Operations Lead

## OUTPUT FORMAT

You MUST respond with valid JSON containing these exact keys:

{
  "company_overview": "2-3 sentences: what they do, who they serve, why they're notable",
  "industry": "primary industry",
  "category": "specific product category (e.g., 'HCM/Payroll', 'ERP', 'InsureTech/Claims')",
  "core_focus": "what is their core SoR / complex implementation focus — be specific",
  "fit_type": "System-of-Record | Complex Implementation | Both | Neither",
  "classification": "Target | Watch | Exclude",
  "revenue_funding": "what you know about their financials — ARR, funding, revenue, etc.",
  "financial_capacity_met": true/false,

  "icp_fit_score": 0-10,
  "icp_why": "2-3 sentences explaining the ICP score with SPECIFIC evidence (cite G2 reviews, case studies, job postings, etc.)",

  "intent_score": 0-10,
  "intent_why": "2-3 sentences explaining the intent score with SPECIFIC evidence",

  "ps_impl_hiring": "specific findings about PS/implementation hiring — job titles, numbers, or 'None observed'",
  "leadership_org_moves": "specific findings about leadership changes or org restructuring",
  "pr_funding_expansion": "specific findings about recent funding, acquisitions, partnerships",
  "events_thought_leadership": "specific findings about conference appearances, publications",
  "reviews_case_studies": "specific findings from G2 reviews, case studies about implementation complexity",

  "internal_ai_overlap": "evidence of internal AI/automation that overlaps with Beacon — or 'None observed'",
  "strategic_constraints": "M&A, IPO, or other strategic constraints — or 'None observed'",
  "ps_cs_contraction": "evidence of PS/CS team contraction — or 'None observed'",
  "build_vs_buy": "evidence they're building vs buying impl automation — or 'None observed'",
  "ai_acquisition": "evidence of AI acquisitions for implementation — or 'None observed'",

  "region": "geographic region: US, EU, APAC, LATAM, MEA, or multi-region — infer from HQ location, website, job postings",
  "headquarters": "city and country of HQ — e.g. 'San Francisco, USA', 'London, UK', 'Paris, France'",
  "employee_count": number or null,
  "funding_stage": "funding stage if known",
  "arr_estimate": "ARR estimate if available",

  "icp_personas": [
    {"title": "VP Implementation", "name": "if found", "relevance": "why this person matters"},
    ...
  ],
  "committee_coverage": "which buying committee roles we have contacts for, and which are OPEN GAPS — be specific",
  "open_gaps": ["list of specific missing personas we need to find"],
    "competitive_landscape": ["top 3-5 direct competitors by name"],

  "implementation_cycle": {
    "enterprise": "e.g. '6-18 months with Big 4 SI involvement'",
    "midmarket": "e.g. '3-6 months with certified partner' or null if not applicable",
    "minimum": "e.g. '3 months with minimal customization'",
    "key_drivers": ["list of factors that drive implementation complexity — e.g. 'ERP complexity', 'data migration', 'regulatory compliance', 'change management'"],
    "review_signals": "what G2/Capterra/Reddit reviews say about implementation difficulty — quote actual reviews"
  },

  "account_thesis": "1-2 sentences: the strategic thesis for why Beacon should pursue this company",
  "why_now": "what creates urgency RIGHT NOW — be specific (funding round, hiring surge, expansion, etc.)",
  "beacon_angle": "the specific angle Beacon should use to engage this company",
  "recommended_outreach_strategy": "concrete outreach approach — who to contact first, what to say, which channels",
  "conversation_starter": "a specific opening line or hook for outreach",
  "next_steps": "2-3 concrete next steps for the sales rep",

  "confidence": "high | medium | low"
}

CRITICAL RULES:
1. Be SPECIFIC and cite actual evidence. Never say "ICP Score 70" — explain WHY with real data points.
2. For committee_coverage: list actual titles found AND specific gaps (e.g., "Have VP Engineering and CFO; MISSING: VP Implementation, Director of PS")
3. For signals: quote actual job postings, news articles, G2 reviews — not vague summaries
4. If you can't find evidence for a field, say exactly what's missing — don't make things up
5. The classification must follow TAL filter logic: Target (meets inclusion AND no exclusions), Watch (partial fit), Exclude (fails inclusion or meets exclusion)
6. Financial capacity is binary: met or not met based on the thresholds above
7. Return ONE JSON object only. Do not add markdown fences, prose before the JSON, or prose after the JSON.
8. Be concise. Keep string values tight and evidence-focused. Avoid long marketing descriptions."""


def _try_parse_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None

    candidates = [raw]
    normalized = (
        raw.replace("\ufeff", "")
        .replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )
    if normalized != raw:
        candidates.append(normalized)

    no_trailing_commas = re.sub(r",(\s*[}\]])", r"\1", normalized)
    if no_trailing_commas not in candidates:
        candidates.append(no_trailing_commas)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        try:
            parsed = ast.literal_eval(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    return None


def _find_nested_dict_with_keys(value: Any, required_keys: set[str]) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if required_keys.issubset(value.keys()):
            return value
        for nested in value.values():
            found = _find_nested_dict_with_keys(nested, required_keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_nested_dict_with_keys(item, required_keys)
            if found:
                return found
    return None


def _extract_icp_payload(raw_text: str, required_keys: set[str]) -> dict[str, Any] | None:
    from app.clients.claude_enrichment import _extract_json_object

    direct = _extract_json_object(raw_text)
    if direct:
        found = _find_nested_dict_with_keys(direct, required_keys)
        if found:
            return found

    text = (raw_text or "").strip()
    if "```" in text:
        fence_matches = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
        for fenced in fence_matches:
            parsed = _try_parse_object(fenced)
            found = _find_nested_dict_with_keys(parsed, required_keys) if parsed else None
            if found:
                return found

    best: dict[str, Any] | None = None
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        brace_match = re.search(r"\{.*", text[idx:], flags=re.DOTALL)
        if not brace_match:
            continue
        parsed = _try_parse_object(brace_match.group(0))
        found = _find_nested_dict_with_keys(parsed, required_keys) if parsed else None
        if found and (best is None or len(json.dumps(found, default=str)) > len(json.dumps(best, default=str))):
            best = found
    return best


async def _run_icp_analysis(
    company_name: str,
    domain: str,
    collected: dict[str, Any],
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Call Claude with all collected data and the ICP system prompt.  Returns None when unavailable."""
    if not settings.claude_api_key:
        logger.warning("Claude API key not configured — skipping ICP analysis for %s", company_name)
        return None

    import anthropic
    client = anthropic.AsyncAnthropic(api_key=settings.claude_api_key)

    # Build the user message with all collected intelligence
    user_data = f"## Company: {company_name}\n"
    if domain and not domain.endswith(".unknown"):
        user_data += f"## Domain: {domain}\n\n"
    user_data += (
        "### Output constraints:\n"
        "- Keep the response compact enough to fit in one complete JSON object.\n"
        "- Prefer short evidence-rich bullets inside strings over long paragraphs.\n"
        "- Keep `company_overview`, `icp_why`, and `intent_why` to 2 sentences each.\n\n"
    )

    # Extra context from CSV (if user provided additional columns)
    if extra_context:
        user_data += "### Analyst-Provided Context (from upload):\n"
        for key, val in extra_context.items():
            if val:
                user_data += f"- {key}: {val}\n"
        user_data += "\n"

    # Website content
    scraped = collected.get("scraped", {})
    if isinstance(scraped, dict) and scraped.get("text"):
        user_data += f"### Company Website Content ({scraped.get('pages_scraped', 0)} pages scraped):\n"
        user_data += scraped["text"][:2500] + "\n\n"

    # General search results
    general = collected.get("general_search", [])
    if general:
        user_data += "### General Company Search Results:\n"
        for r in general[:3]:
            if isinstance(r, dict):
                user_data += f"- [{r.get('title', '')}] {r.get('snippet', '')}\n"
        user_data += "\n"

    # Apollo firmographics
    apollo_co = collected.get("apollo_company")
    if isinstance(apollo_co, dict) and apollo_co:
        user_data += "### Apollo Firmographic Data:\n"
        user_data += json.dumps(apollo_co, default=str) + "\n\n"

    # Merged contacts (Apollo + Hunter)
    all_contacts = collected.get("all_contacts", [])
    if isinstance(all_contacts, list) and all_contacts:
        user_data += f"### Discovered Contacts ({len(all_contacts)} found via Apollo + Hunter):\n"
        for c in all_contacts[:10]:
            if isinstance(c, dict):
                source = c.get("_source", "unknown")
                email_info = ""
                if c.get("email"):
                    verified = c.get("email_verified", "")
                    confidence = c.get("confidence")
                    parts = [c["email"]]
                    if verified == "verified":
                        parts.append("VERIFIED")
                    elif confidence and isinstance(confidence, (int, float)):
                        parts.append(f"{confidence}% confidence")
                    email_info = f" | email: {', '.join(parts)}"
                dept = f" | dept: {c['department']}" if c.get("department") else ""
                user_data += (
                    f"- {c.get('first_name', '')} {c.get('last_name', '')} — "
                    f"{c.get('title', 'Unknown title')} "
                    f"(seniority: {c.get('seniority', 'unknown')}, "
                    f"source: {source}{email_info}{dept})\n"
                )
        user_data += "\n"

    # PS/Implementation hiring signals
    ps_hiring = collected.get("ps_hiring", [])
    if ps_hiring:
        user_data += "### PS/Implementation Hiring Search Results:\n"
        for r in ps_hiring[:4]:
            if isinstance(r, dict):
                user_data += f"- [{r.get('title', '')}] {r.get('snippet', '')}\n"
        user_data += "\n"

    # Leadership moves
    leadership = collected.get("leadership", [])
    if leadership:
        user_data += "### Leadership/Org Moves Search Results:\n"
        for r in leadership[:4]:
            if isinstance(r, dict):
                user_data += f"- [{r.get('title', '')}] {r.get('snippet', '')}\n"
        user_data += "\n"

    # Funding/PR/Expansion
    funding = collected.get("funding", [])
    if funding:
        user_data += "### PR/Funding/Expansion Search Results:\n"
        for r in funding[:3]:
            if isinstance(r, dict):
                user_data += f"- [{r.get('title', '')}] {r.get('snippet', '')}\n"
        user_data += "\n"

    # G2 Reviews / Case Studies
    reviews = collected.get("reviews", [])
    if reviews:
        user_data += "### Reviews/Case Studies Search Results:\n"
        for r in reviews[:4]:
            if isinstance(r, dict):
                user_data += f"- [{r.get('title', '')}] {r.get('snippet', '')}\n"
        user_data += "\n"

    # Events / Thought Leadership
    events = collected.get("events", [])
    if events:
        user_data += "### Events/Thought Leadership Search Results:\n"
        for r in events[:3]:
            if isinstance(r, dict):
                user_data += f"- [{r.get('title', '')}] {r.get('snippet', '')}\n"
        user_data += "\n"

    # AI overlap (negative signals)
    ai_overlap = collected.get("ai_overlap", [])
    if ai_overlap:
        user_data += "### Internal AI/Agentic Overlap Search Results:\n"
        for r in ai_overlap[:3]:
            if isinstance(r, dict):
                user_data += f"- [{r.get('title', '')}] {r.get('snippet', '')}\n"
        user_data += "\n"

    # Recent news (from Google News RSS — free)
    news_data = collected.get("news")
    if isinstance(news_data, dict):
        # NewsClient returns {funding_signals: [...], pr_signals: [...]}
        news_items = (news_data.get("funding_signals") or []) + (news_data.get("pr_signals") or [])
    elif isinstance(news_data, list):
        news_items = news_data
    else:
        news_items = []
    if news_items:
        user_data += "### Recent News:\n"
        for r in news_items[:3]:
            if isinstance(r, dict):
                title = r.get("title", "")
                snippet = r.get("snippet") or r.get("description", "")
                user_data += f"- [{title}] {snippet}\n"
        user_data += "\n"

    try:
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=5500,
            # Cache the large static ICP framework prompt — batch ICP research
            # reuses the identical prefix within the 5-min window, cutting input
            # tokens on every call after the first.
            system=[{"type": "text", "text": _ICP_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_data}],
        )
        text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        text = "\n".join(text_blocks).strip()

        _ICP_REQUIRED_KEYS = {"icp_fit_score", "classification", "confidence"}
        payload = _extract_icp_payload(text, _ICP_REQUIRED_KEYS)

        if payload is None:
            logger.warning(
                "Claude ICP analysis returned no valid ICP JSON for %s. stop_reason=%s Raw preview: %s",
                company_name,
                getattr(response, "stop_reason", None),
                text[:600],
            )
            return None

        payload["_source"] = "claude_icp_pipeline"
        payload["_model"] = settings.ANTHROPIC_MODEL
        return payload

    except Exception as e:
        logger.error("Claude ICP analysis failed for %s: %s", company_name, safe_error_message(e))
        return None




_NONE_OBSERVED_MARKERS = {
    "",
    "none",
    "none observed",
    "n/a",
    "not analyzed",
    "not found",
    "pending analysis",
    "unknown",
}


def _clip_text(value: Any, limit: int = 4000) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _clean_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = _clip_text(item, 500)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _signal_value(icp: dict[str, Any], key: str) -> str | None:
    value = _clip_text(icp.get(key), 2000)
    if not value:
        return None
    if value.strip().lower() in _NONE_OBSERVED_MARKERS:
        return None
    return value


def _analysis_signals(icp: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    positive_fields = {
        "ps_impl_hiring": "PS / Implementation Hiring",
        "leadership_org_moves": "Leadership / Org Moves",
        "pr_funding_expansion": "PR / Funding / Expansion",
        "events_thought_leadership": "Events / Thought Leadership",
        "reviews_case_studies": "Reviews / Case Studies",
    }
    negative_fields = {
        "internal_ai_overlap": "Internal AI Overlap",
        "strategic_constraints": "Strategic Constraints",
        "ps_cs_contraction": "PS / CS Contraction",
        "build_vs_buy": "Build vs Buy",
        "ai_acquisition": "AI Acquisition",
    }
    positive = [
        {"key": key, "label": label, "value": value}
        for key, label in positive_fields.items()
        if (value := _signal_value(icp, key))
    ]
    negative = [
        {"key": key, "label": label, "value": value}
        for key, label in negative_fields.items()
        if (value := _signal_value(icp, key))
    ]
    return positive, negative


def _best_persona(icp: dict[str, Any]) -> dict[str, str] | None:
    personas = icp.get("icp_personas")
    if not isinstance(personas, list):
        return None

    def score(item: dict[str, Any]) -> int:
        title = str(item.get("title") or "").lower()
        ranking = [
            "vp implementation",
            "vp professional services",
            "vp delivery",
            "director implementation",
            "director professional services",
            "head of customer onboarding",
            "chief customer officer",
            "svp customer success",
        ]
        for idx, needle in enumerate(ranking):
            if needle in title:
                return 100 - idx
        if "implementation" in title or "professional services" in title or "delivery" in title:
            return 70
        if "customer success" in title or "onboarding" in title:
            return 60
        if "chief" in title or "vp" in title or "head" in title:
            return 50
        return 10

    candidates = [item for item in personas if isinstance(item, dict) and _clip_text(item.get("title"), 200)]
    if not candidates:
        return None
    best = max(candidates, key=score)
    return {
        "title": _clip_text(best.get("title"), 200) or "",
        "name": _clip_text(best.get("name"), 200) or "",
        "relevance": _clip_text(best.get("relevance"), 600) or "",
    }


def _domain_root(domain: str) -> str:
    raw = (domain or "").strip().lower()
    if not raw:
        return ""
    host = raw.split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in host.split(".") if part]
    if len(parts) >= 2:
        return parts[-2]
    return host


def _company_keywords(company_name: str) -> list[str]:
    text = re.sub(r"[^a-z0-9\s&-]", " ", (company_name or "").lower())
    stopwords = {
        "inc", "incorporated", "corp", "corporation", "co", "company", "llc", "ltd",
        "limited", "plc", "group", "holdings", "holding", "technologies", "technology",
        "software", "systems", "solutions", "global", "international", "partners",
    }
    tokens = []
    for token in re.split(r"[\s&/-]+", text):
        token = token.strip()
        if len(token) < 3 or token in stopwords:
            continue
        tokens.append(token)
    deduped: list[str] = []
    for token in tokens:
        if token not in deduped:
            deduped.append(token)
    return deduped


def _relevant_source(item: dict[str, Any], company_name: str, domain: str) -> bool:
    url = str(item.get("url") or item.get("href") or "").strip()
    if not url:
        return False

    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]

    domain = (domain or "").strip().lower()
    root = _domain_root(domain)
    if root and (host == domain or host.endswith(f".{domain}") or root in host):
        return True

    text = " ".join(
        str(part or "")
        for part in (item.get("title"), item.get("snippet"), item.get("body"), url)
    ).lower()
    keywords = _company_keywords(company_name)
    if not keywords:
        return False

    matches = sum(1 for token in keywords if token in text)
    min_matches = 2 if len(keywords) >= 2 else 1
    return matches >= min_matches


def _source_links(items: Any, company_name: str = "", domain: str = "", limit: int = 4) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        if company_name and not _relevant_source(item, company_name, domain):
            continue
        url = _clip_text(item.get("url") or item.get("href"), 500)
        if not url or url in seen:
            continue
        seen.add(url)
        links.append(
            {
                "title": _clip_text(item.get("title"), 180) or url,
                "url": url,
                "snippet": _clip_text(item.get("snippet") or item.get("body"), 280) or "",
            }
        )
        if len(links) >= limit:
            break
    return links


def _website_source_links(scraped: Any, domain: str = "", limit: int = 4) -> list[dict[str, str]]:
    if not isinstance(scraped, dict):
        return []
    raw_urls = scraped.get("urls_scraped")
    urls = [str(url).strip() for url in raw_urls] if isinstance(raw_urls, list) else []
    if not urls:
        text = str(scraped.get("text") or "")
        urls = re.findall(r"\[(https?://[^\]]+)\]", text)
    domain = (domain or "").strip().lower()
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for url in urls:
        if not url or url in seen:
            continue
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if domain and host and host != domain and not host.endswith(f".{domain}"):
            continue
        seen.add(url)
        links.append({"title": url.replace("https://", "").replace("http://", ""), "url": url, "snippet": ""})
        if len(links) >= limit:
            break
    return links


def _build_research_sources(collected: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    company_name = str(collected.get("_company_name") or "")
    domain = str(collected.get("_domain") or "")
    scraped = collected.get("scraped")
    general = collected.get("general_search")
    ps_hiring = collected.get("ps_hiring")
    leadership = collected.get("leadership")
    funding = collected.get("funding")
    reviews = collected.get("reviews")
    events = collected.get("events")
    ai_overlap = collected.get("ai_overlap")
    news = collected.get("news")

    return {
        "company_overview": _website_source_links(scraped, domain, 2) + _source_links(general, company_name, domain, 3),
        "icp_why": _website_source_links(scraped, domain, 2) + _source_links(reviews, company_name, domain, 3) + _source_links(general, company_name, domain, 2),
        "intent_why": _source_links(ps_hiring, company_name, domain, 2) + _source_links(funding, company_name, domain, 2) + _source_links(events, company_name, domain, 2) + _source_links(news, company_name, domain, 2),
        "revenue_funding": _source_links(funding, company_name, domain, 3) + _source_links(news, company_name, domain, 2) + _website_source_links(scraped, domain, 2),
        "category": _website_source_links(scraped, domain, 2) + _source_links(general, company_name, domain, 2),
        "core_focus": _website_source_links(scraped, domain, 2) + _source_links(reviews, company_name, domain, 2) + _source_links(general, company_name, domain, 2),
        "fit_type": _website_source_links(scraped, domain, 2) + _source_links(reviews, company_name, domain, 2) + _source_links(general, company_name, domain, 2),
        "ps_impl_hiring": _source_links(ps_hiring, company_name, domain, 4),
        "leadership_org_moves": _source_links(leadership, company_name, domain, 4),
        "pr_funding_expansion": _source_links(funding, company_name, domain, 3) + _source_links(news, company_name, domain, 3),
        "events_thought_leadership": _source_links(events, company_name, domain, 4),
        "reviews_case_studies": _source_links(reviews, company_name, domain, 4),
        "internal_ai_overlap": _source_links(ai_overlap, company_name, domain, 4),
        "strategic_constraints": _source_links(funding, company_name, domain, 2) + _source_links(news, company_name, domain, 2),
        "account_thesis": _website_source_links(scraped, domain, 2) + _source_links(general, company_name, domain, 2) + _source_links(reviews, company_name, domain, 2),
        "why_now": _source_links(funding, company_name, domain, 2) + _source_links(news, company_name, domain, 2) + _source_links(ps_hiring, company_name, domain, 2) + _source_links(leadership, company_name, domain, 2),
        "beacon_angle": _source_links(reviews, company_name, domain, 2) + _source_links(ps_hiring, company_name, domain, 2) + _website_source_links(scraped, domain, 2),
        "recommended_outreach_strategy": _source_links(ps_hiring, company_name, domain, 2) + _source_links(leadership, company_name, domain, 2) + _source_links(reviews, company_name, domain, 2),
        "conversation_starter": _source_links(reviews, company_name, domain, 2) + _source_links(events, company_name, domain, 1) + _source_links(general, company_name, domain, 1),
        "committee_coverage": _source_links(ps_hiring, company_name, domain, 2) + _source_links(leadership, company_name, domain, 2) + _source_links(reviews, company_name, domain, 2),
        "next_steps": _source_links(ps_hiring, company_name, domain, 2) + _source_links(reviews, company_name, domain, 2) + _source_links(events, company_name, domain, 1),
    }


def _normalize_category(value: Any) -> str | None:
    text = (_clip_text(value, 200) or "").strip()
    if not text:
        return None
    slug = text.lower()
    if any(token in slug for token in ("procurement", "source-to-pay", "s2p")):
        return "Procurement/S2P"
    if "insur" in slug or "policy" in slug or "claims" in slug:
        return "Insuretech"
    if "ehr" in slug or "clinical" in slug or "patient" in slug:
        return "HealthTech"
    if "legal" in slug or "clm" in slug or "grc" in slug:
        return "Legal Ops"
    if "supply chain" in slug or "wms" in slug or "oms" in slug or "tms" in slug:
        return "Supply Chain"
    if "payroll" in slug or "hris" in slug or "hcm" in slug:
        return "HRIS/Payroll"
    if "finance" in slug or "financial close" in slug or "accounting" in slug:
        if "billing" in slug or "subscription" in slug or "monetization" in slug:
            return "Finance Ops/Monetization"
        if "ap/ar" in slug or "ap-AR".lower() in slug or "order-to-cash" in slug or "procure-to-pay" in slug:
            return "Finance Ops/AP/AR"
        return "Finance Ops"
    if "esg" in slug or "reporting" in slug or "compliance" in slug:
        return "Finance Ops"
    if "ehs" in slug or "safety" in slug:
        return "EHS/Compliance"
    if "erp" in slug or "accounting" in slug:
        return "ERP/Accounting/Payroll"
    return text


def _normalize_confidence(value: Any) -> str | None:
    text = (_clip_text(value, 40) or "").strip().lower()
    if not text:
        return None
    mapping = {"high": "High", "medium": "Medium", "low": "Low"}
    return mapping.get(text, text.title())


def _calibrate_icp_output(icp: dict[str, Any], research_quality: dict[str, Any]) -> dict[str, Any]:
    calibrated = copy.deepcopy(icp)
    category = _normalize_category(calibrated.get("category"))
    if category:
        calibrated["category"] = category
    confidence = _normalize_confidence(calibrated.get("confidence"))
    if confidence:
        calibrated["confidence"] = confidence

    try:
        icp_score = int(calibrated.get("icp_fit_score"))
    except (TypeError, ValueError):
        icp_score = None
    try:
        intent_score = int(calibrated.get("intent_score"))
    except (TypeError, ValueError):
        intent_score = None

    positive_count = len(_analysis_signals(calibrated)[0] or [])
    evidence_level = str(research_quality.get("evidence_level") or "").lower()
    fit_type = str(calibrated.get("fit_type") or "")
    classification = str(calibrated.get("classification") or "")
    financial_capacity_met = bool(calibrated.get("financial_capacity_met"))

    if (
        classification == "Watch"
        and fit_type == "System-of-Record"
        and financial_capacity_met
        and category in {"HRIS/Payroll", "Finance Ops", "Finance Ops/Monetization", "Procurement/S2P"}
        and icp_score is not None
        and icp_score >= 5
    ):
        calibrated["classification"] = "Target"

    if (
        intent_score is not None
        and intent_score <= 6
        and classification == "Target"
        and fit_type == "Both"
        and evidence_level == "strong"
        and positive_count >= 3
        and category not in {"EHS/Compliance"}
    ):
        calibrated["intent_score"] = min(10, intent_score + 1)

    return calibrated


def _research_quality_snapshot(result: dict[str, Any], icp: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("raw_data", {}) if isinstance(result.get("raw_data"), dict) else {}
    search_results = int(raw.get("search_results_count", 0) or 0)
    scraped_pages = int(raw.get("scraped_pages", 0) or 0)
    contacts_found = int(raw.get("contacts_found", 0) or 0)
    hunter_contacts = int(raw.get("hunter_contacts_found", 0) or 0)
    apollo_found = bool(raw.get("apollo_found"))
    news_results = int(raw.get("news_results", 0) or 0)
    domain = str(result.get("domain") or "").strip().lower()

    evidence_score = 0
    evidence_score += 2 if domain and not domain.endswith(".unknown") else 0
    evidence_score += 2 if scraped_pages > 0 else 0
    evidence_score += 1 if search_results > 0 else 0
    evidence_score += 2 if apollo_found else 0
    evidence_score += 2 if contacts_found > 0 else 0
    evidence_score += 1 if hunter_contacts > 0 else 0
    evidence_score += 1 if news_results > 0 else 0
    evidence_score += 1 if str(icp.get("confidence") or "").lower() == "high" else 0

    if evidence_score >= 8:
        level = "strong"
    elif evidence_score >= 4:
        level = "partial"
    else:
        level = "thin"

    return {
        "domain_available": bool(domain) and not domain.endswith(".unknown"),
        "search_results_count": search_results,
        "scraped_pages": scraped_pages,
        "apollo_company_found": apollo_found,
        "contacts_found": contacts_found,
        "hunter_contacts_found": hunter_contacts,
        "news_results": news_results,
        "confidence": _clip_text(icp.get("confidence"), 20),
        "evidence_score": evidence_score,
        "evidence_level": level,
    }


def _build_sales_play(icp: dict[str, Any]) -> dict[str, Any]:
    positive_signals, negative_signals = _analysis_signals(icp)
    best_persona = _best_persona(icp)
    open_gaps = _clean_text_list(icp.get("open_gaps"))

    return {
        "tal_verdict": _clip_text(icp.get("classification"), 50),
        "fit_type": _clip_text(icp.get("fit_type"), 80),
        "company_overview": _clip_text(icp.get("company_overview"), 1500),
        "core_focus": _clip_text(icp.get("core_focus"), 1000),
        "account_thesis": _clip_text(icp.get("account_thesis"), 2000),
        "why_now": _clip_text(icp.get("why_now"), 2000),
        "beacon_angle": _clip_text(icp.get("beacon_angle"), 2000),
        "recommended_outreach_strategy": _clip_text(icp.get("recommended_outreach_strategy"), 2000),
        "conversation_starter": _clip_text(icp.get("conversation_starter"), 1000),
        "next_steps": _clip_text(icp.get("next_steps"), 1500),
        "committee_coverage": _clip_text(icp.get("committee_coverage"), 1500),
        "best_persona": best_persona,
        "open_gaps": open_gaps,
        "proof_points": [item["value"] for item in positive_signals[:5]],
        "risk_flags": [item["value"] for item in negative_signals[:5]],
    }


def _extract_competitor_names(icp: dict[str, Any]) -> list[str]:
    raw_candidates = []
    for key in ("competitive_landscape", "competitors"):
        value = icp.get(key)
        if isinstance(value, list):
            raw_candidates.extend(value)

    names: list[str] = []
    seen: set[str] = set()
    for item in raw_candidates:
        if isinstance(item, dict):
            text = _clip_text(item.get("name") or item.get("competitor"), 200)
        else:
            text = _clip_text(item, 200)
        if not text:
            continue
        cleaned = re.sub(r"\s+", " ", text).strip(" ,.-")
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(cleaned)
        if len(names) >= 6:
            break
    return names


def _build_competitive_landscape_cards(company_name: str, icp: dict[str, Any]) -> list[dict[str, str]]:
    competitors = _extract_competitor_names(icp)
    if not competitors:
        return []

    category = _clip_text(icp.get("category"), 140)
    core_focus = _clip_text(icp.get("core_focus"), 280)
    beacon_angle = _clip_text(icp.get("beacon_angle"), 320)
    recommended_strategy = _clip_text(icp.get("recommended_outreach_strategy"), 280)
    implementation_cycle = _clip_text(icp.get("implementation_cycle"), 220)

    raw_open_gaps = icp.get("open_gaps")
    open_gaps: list[str] = []
    if isinstance(raw_open_gaps, list):
        open_gaps = [
            _clip_text(item, 160)
            for item in raw_open_gaps
            if _clip_text(item, 160)
        ]

    differentiation_tracks = [
        "de-risk implementation execution",
        "shorten time-to-value with fewer handoffs",
        "reduce services dependency and delivery overhead",
        "improve cross-functional rollout coordination",
    ]

    context_snippets = [
        snippet
        for snippet in [beacon_angle, recommended_strategy, implementation_cycle, *(open_gaps[:2])]
        if snippet
    ]

    cards: list[dict[str, str]] = []
    for idx, name in enumerate(competitors[:4]):
        summary_parts = [f"{name} is a comparable option buyers evaluate alongside {company_name}."]
        if category:
            summary_parts.append(f"Category context: {category}.")
        if core_focus:
            summary_parts.append(core_focus)

        track = differentiation_tracks[idx % len(differentiation_tracks)]
        if context_snippets:
            context = context_snippets[idx % len(context_snippets)]
            pitch_angle = f"Against {name}: focus on how Beacon can {track}. {context}"
        else:
            pitch_angle = f"Against {name}: focus on how Beacon can {track}."

        cards.append(
            {
                "name": name,
                "website": "",
                "summary": " ".join(summary_parts)[:320],
                "pitch_angle": pitch_angle[:320],
                "source": "icp_analysis",
            }
        )

    return cards


def _parse_arr_estimate(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if not text:
        return None
    text = text.replace("$", "").replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    amount = float(match.group(1))
    if "b" in text or "billion" in text:
        amount *= 1_000_000_000
    elif "m" in text or "million" in text:
        amount *= 1_000_000
    elif "k" in text or "thousand" in text:
        amount *= 1_000
    return amount


def _build_extra_context(company) -> dict[str, Any]:
    extra_context: dict[str, Any] = {}
    if company.description:
        extra_context["description"] = company.description
    if company.industry:
        extra_context["industry"] = company.industry
    if company.vertical:
        extra_context["category"] = company.vertical

    sources = company.enrichment_sources if isinstance(company.enrichment_sources, dict) else {}
    import_block = sources.get("import") if isinstance(sources.get("import"), dict) else {}
    analyst = import_block.get("analyst") if isinstance(import_block.get("analyst"), dict) else {}
    uploaded_analyst = import_block.get("uploaded_analyst") if isinstance(import_block.get("uploaded_analyst"), dict) else {}
    raw_row = import_block.get("raw_row") if isinstance(import_block.get("raw_row"), dict) else {}

    for source in (uploaded_analyst, analyst):
        for key, val in source.items():
            text = _clip_text(val, 1000)
            if text and key not in extra_context:
                extra_context[key] = text

    ignored_prefixes = ("contact_", "angel_")
    ignored_keys = {
        "name",
        "company",
        "company_name",
        "domain",
        "website",
        "contact_name",
        "contact_title",
        "contact_email",
        "linkedin_url",
    }
    for key, val in raw_row.items():
        normalized = str(key).strip().lower()
        if normalized in ignored_keys or normalized.startswith(ignored_prefixes):
            continue
        text = _clip_text(val, 1000)
        if text and key not in extra_context:
            extra_context[key] = text

    return extra_context


# ── Batch Processing (called from Celery task) ───────────────────────────────


async def research_company_and_update(
    company_id: UUID,
    session,
) -> dict[str, Any] | None:
    """
    Research a company by ID, run ICP analysis, and update the DB record
    with the results. Returns the ICP analysis dict or None on failure.
    """
    from app.models.company import Company
    from app.services.account_sourcing import (
        _build_committee_coverage,
        _build_prospecting_priorities,
        refresh_company_prospecting_fields,
        refresh_contact_sequence_plan,
    )
    from app.models.contact import Contact
    from app.services.icp_scorer import score_company

    company = await session.get(Company, company_id)
    if not company:
        logger.warning(f"ICP research: company {company_id} not found")
        return None

    extra_context = _build_extra_context(company)

    # Run the research pipeline
    result = await research_company(
        company_name=company.name,
        domain=company.domain,
        extra_context=extra_context if extra_context else None,
    )

    icp = result.get("icp_analysis", {})
    if not icp:
        return None

    collected = result.get("_collected", {}) if isinstance(result.get("_collected"), dict) else {}
    research_quality = _research_quality_snapshot(result, icp)
    icp = _calibrate_icp_output(icp, research_quality)
    icp["sources"] = _build_research_sources(collected)
    positive_signals, negative_signals = _analysis_signals(icp)
    sales_play = _build_sales_play(icp)
    analyzed_at = datetime.utcnow().isoformat()

    # ── Update company fields from ICP analysis ──────────────────────────
    # Update domain if we resolved a better one
    resolved_domain = result.get("domain", "")
    domain_resolution = await _safe_apply_resolved_domain(session, company, resolved_domain)

    # Core fields
    if icp.get("company_overview"):
        company.description = str(icp["company_overview"])[:2000]
    if icp.get("industry") and icp["industry"] != "Unknown":
        company.industry = str(icp["industry"])[:200]
    if icp.get("category"):
        company.vertical = str(icp["category"])[:200]
    if icp.get("employee_count"):
        try:
            company.employee_count = int(icp["employee_count"])
        except (TypeError, ValueError):
            pass
    if icp.get("funding_stage"):
        company.funding_stage = str(icp["funding_stage"])[:100]
    arr_estimate = _parse_arr_estimate(icp.get("arr_estimate"))
    if arr_estimate and not company.arr_estimate:
        company.arr_estimate = arr_estimate
    if icp.get("region") and icp["region"] not in ("Unknown", "unknown", ""):
        company.region = str(icp["region"])[:50]
    if icp.get("headquarters") and icp["headquarters"] not in ("Unknown", "unknown", ""):
        company.headquarters = str(icp["headquarters"])[:200]

    # ICP fields
    if icp.get("account_thesis"):
        company.account_thesis = str(icp["account_thesis"])[:2000]
    if icp.get("why_now"):
        company.why_now = str(icp["why_now"])[:2000]
    if icp.get("beacon_angle"):
        company.beacon_angle = str(icp["beacon_angle"])[:2000]
    if icp.get("recommended_outreach_strategy"):
        company.recommended_outreach_lane = str(icp["recommended_outreach_strategy"])[:500]

    # Store full ICP analysis in enrichment_cache
    cache = copy.deepcopy(company.enrichment_cache or {})
    cache["icp_analysis"] = {
        "data": icp,
        "raw_data_summary": result.get("raw_data", {}),
        "research_quality": research_quality,
        "sales_play": sales_play,
        "analyzed_at": analyzed_at,
    }
    cache["research_quality"] = {
        "data": research_quality,
        "fetched_at": analyzed_at,
    }
    scraped = collected.get("scraped")
    if isinstance(scraped, dict):
        cache["web_scrape"] = {
            "data": scraped,
            "fetched_at": analyzed_at,
        }
    apollo_company = collected.get("apollo_company")
    if isinstance(apollo_company, dict) and apollo_company:
        cache["apollo_company"] = {
            "data": apollo_company,
            "fetched_at": analyzed_at,
        }
    apollo_contacts = collected.get("apollo_contacts")
    if isinstance(apollo_contacts, list):
        cache["apollo_contacts"] = {
            "data": apollo_contacts,
            "fetched_at": analyzed_at,
        }
    hunter_contacts = collected.get("hunter_contacts")
    if isinstance(hunter_contacts, list):
        cache["hunter_contacts"] = {
            "data": hunter_contacts,
            "fetched_at": analyzed_at,
        }
    intent_signal_snapshot = {
        "general_search": collected.get("general_search") if isinstance(collected.get("general_search"), list) else [],
        "ps_hiring": collected.get("ps_hiring") if isinstance(collected.get("ps_hiring"), list) else [],
        "leadership": collected.get("leadership") if isinstance(collected.get("leadership"), list) else [],
        "funding": collected.get("funding") if isinstance(collected.get("funding"), list) else [],
        "reviews": collected.get("reviews") if isinstance(collected.get("reviews"), list) else [],
        "events": collected.get("events") if isinstance(collected.get("events"), list) else [],
        "ai_overlap": collected.get("ai_overlap") if isinstance(collected.get("ai_overlap"), list) else [],
        "news": collected.get("news") if isinstance(collected.get("news"), list) else [],
    }
    cache["intent_signals"] = {
        "data": intent_signal_snapshot,
        "fetched_at": analyzed_at,
    }
    cache["ai_summary"] = {
        "data": {
            "description": icp.get("company_overview"),
            "icp_fit_reasoning": icp.get("icp_why"),
            "intent_signals_summary": icp.get("intent_why"),
            "recommended_approach": icp.get("recommended_outreach_strategy"),
            "pain_points": sales_play.get("proof_points", []),
            "talking_points": [
                item for item in [
                    icp.get("conversation_starter"),
                    icp.get("beacon_angle"),
                    icp.get("why_now"),
                ] if item
            ],
            "competitive_landscape": _extract_competitor_names(icp),
            "tech_stack_signals": [],
        },
        "fetched_at": analyzed_at,
    }
    cache["competitive_landscape_v2"] = _build_competitive_landscape_cards(company.name, icp)
    cache["domain_resolution"] = {
        **domain_resolution,
        "attempted_at": analyzed_at,
    }
    company.enrichment_cache = cache

    # Store structured analyst data in enrichment_sources.import without losing
    # the original uploaded worksheet values.
    enrichment_sources = copy.deepcopy(company.enrichment_sources or {})
    import_data = enrichment_sources.get("import", {}) if isinstance(enrichment_sources.get("import"), dict) else {}
    existing_uploaded_analyst = import_data.get("analyst") if isinstance(import_data.get("analyst"), dict) else {}
    if existing_uploaded_analyst and not isinstance(import_data.get("uploaded_analyst"), dict):
        import_data["uploaded_analyst"] = copy.deepcopy(existing_uploaded_analyst)

    generated_analyst = {
        "classification": icp.get("classification"),
        "fit_type": icp.get("fit_type"),
        "confidence": icp.get("confidence"),
        "icp_fit_score": icp.get("icp_fit_score"),
        "intent_score": icp.get("intent_score"),
        "icp_why": icp.get("icp_why"),
        "intent_why": icp.get("intent_why"),
        "category": icp.get("category"),
        "core_focus": icp.get("core_focus"),
        "revenue_funding": icp.get("revenue_funding"),
        "company_overview": icp.get("company_overview"),
        "industry": icp.get("industry"),
        "financial_capacity_met": icp.get("financial_capacity_met"),
        "employee_count": icp.get("employee_count"),
        "funding_stage": icp.get("funding_stage"),
        "arr_estimate": icp.get("arr_estimate"),
        "committee_coverage": icp.get("committee_coverage"),
        "open_gaps": icp.get("open_gaps"),
        "account_thesis": icp.get("account_thesis"),
        "why_now": icp.get("why_now"),
        "beacon_angle": icp.get("beacon_angle"),
        "recommended_outreach_strategy": icp.get("recommended_outreach_strategy"),
        "conversation_starter": icp.get("conversation_starter"),
        "next_steps": icp.get("next_steps"),
        "implementation_cycle": icp.get("implementation_cycle"),
    }
    import_data["generated_analyst"] = generated_analyst
    # Merge generated analyst into analyst — uploaded values take precedence,
    # but Claude fills in any fields the CSV didn't provide.
    merged_analyst = dict(generated_analyst)
    for key, value in existing_uploaded_analyst.items():
        if value is not None and value != "":
            merged_analyst[key] = value
    import_data["analyst"] = merged_analyst

    existing_uploaded_signals = (
        import_data.get("uploaded_signals")
        if isinstance(import_data.get("uploaded_signals"), dict)
        else {}
    )
    if not existing_uploaded_signals:
        import_data["uploaded_signals"] = {
            "positive": positive_signals,
            "negative": negative_signals,
        }
    import_data["generated_signals"] = {
        "positive": positive_signals,
        "negative": negative_signals,
    }
    enrichment_sources["import"] = import_data
    company.enrichment_sources = enrichment_sources

    # Build intent_signals JSONB
    intent_signals = copy.deepcopy(company.intent_signals or {})
    intent_signals.update({
        "uploaded_intent_score": intent_signals.get("uploaded_intent_score", icp.get("intent_score")),
        "uploaded_fit_type": intent_signals.get("uploaded_fit_type", icp.get("fit_type")),
        "uploaded_classification": intent_signals.get("uploaded_classification", icp.get("classification")),
        "uploaded_confidence": intent_signals.get("uploaded_confidence", icp.get("confidence")),
        "positive_signal_count": intent_signals.get("positive_signal_count", len(positive_signals or [])),
        "negative_signal_count": intent_signals.get("negative_signal_count", len(negative_signals or [])),
        "uploaded_signals": intent_signals.get("uploaded_signals") or {
            "positive": positive_signals,
            "negative": negative_signals,
        },
        "generated_intent_score": icp.get("intent_score"),
        "generated_fit_type": icp.get("fit_type"),
        "generated_classification": icp.get("classification"),
        "generated_confidence": icp.get("confidence"),
        "generated_positive_signal_count": len(positive_signals or []),
        "generated_negative_signal_count": len(negative_signals or []),
        "generated_signals": {
            "positive": positive_signals,
            "negative": negative_signals,
        },
        "research_evidence_level": research_quality.get("evidence_level"),
    })
    company.intent_signals = intent_signals

    # Build prospecting_profile JSONB
    profile = copy.deepcopy(company.prospecting_profile or {})
    if icp.get("recommended_outreach_strategy"):
        profile["recommended_outreach_strategy"] = icp["recommended_outreach_strategy"]
    if icp.get("conversation_starter"):
        profile["conversation_starter"] = icp["conversation_starter"]
    if icp.get("icp_personas"):
        profile["icp_personas"] = icp["icp_personas"]
    if icp.get("committee_coverage"):
        profile["committee_coverage"] = icp["committee_coverage"]
    if icp.get("open_gaps"):
        profile["open_gaps"] = icp["open_gaps"]
    if icp.get("next_steps"):
        profile["next_steps"] = icp["next_steps"]
    profile["sales_play"] = sales_play
    profile["proof_points"] = sales_play.get("proof_points", [])
    profile["risk_flags"] = sales_play.get("risk_flags", [])
    profile["best_entry_persona"] = sales_play.get("best_persona")
    profile["research_quality"] = research_quality
    company.prospecting_profile = profile

    contacts_created = 0
    profile["contact_discovery_status"] = "paused"
    profile["contact_discovery_note"] = (
        "Company research is temporarily not creating contacts automatically. "
        "Upload prospects from the Prospecting page to attach stakeholders."
    )

    company.icp_score, company.icp_tier = score_company(company)
    company.enriched_at = datetime.utcnow()
    company.updated_at = datetime.utcnow()
    session.add(company)
    await session.flush()

    try:
        await session.commit()
    except Exception as exc:
        logger.warning(
            "Failed to persist ICP research for %s: %s",
            company.name,
            safe_error_message(exc),
        )
        await session.rollback()
        return None

    await session.refresh(company)

    committee_coverage = await _build_committee_coverage(company, session)
    cache = copy.deepcopy(company.enrichment_cache or {})
    cache["committee_coverage"] = {
        "data": committee_coverage,
        "fetched_at": datetime.utcnow().isoformat(),
    }
    priorities = _build_prospecting_priorities(
        company,
        committee_coverage,
        company.intent_signals if isinstance(company.intent_signals, dict) else {},
    )
    cache["prospecting_priorities"] = {
        "data": priorities,
        "fetched_at": datetime.utcnow().isoformat(),
    }
    company.enrichment_cache = cache

    refreshed_profile = copy.deepcopy(company.prospecting_profile or {})
    if icp.get("committee_coverage"):
        refreshed_profile["committee_coverage"] = icp.get("committee_coverage")
    refreshed_profile["priorities"] = priorities
    refreshed_profile["committee_snapshot"] = committee_coverage
    company.prospecting_profile = refreshed_profile

    from sqlmodel import select
    contacts = (
        await session.execute(select(Contact).where(Contact.company_id == company.id))
    ).scalars().all()
    company = refresh_company_prospecting_fields(company, contacts)
    for contact in contacts:
        refresh_contact_sequence_plan(contact, company)
        session.add(contact)

    company.icp_score, company.icp_tier = score_company(company)
    company.enriched_at = datetime.utcnow()
    company.updated_at = datetime.utcnow()
    session.add(company)
    await session.commit()
    await session.refresh(company)

    logger.info(
        f"ICP research complete: {company.name} ({company.domain}) "
        f"— Score {company.icp_score} ({company.icp_tier}), "
        f"Classification: {icp.get('classification')}, "
        f"Fit: {icp.get('fit_type')}, "
        f"Contacts saved: {contacts_created}"
    )

    return icp


# ── Free ICP Research (no Apollo/Hunter credits) ─────────────────────────────

async def research_company_free(
    company_name: str,
    domain: str = "",
    extra_context: dict[str, Any] | None = None,
    db_contacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    ICP research pipeline that uses only free data sources.
    Skips Apollo and Hunter — relies on web scraping, Serper searches, news,
    and any contacts already stored in the database.
    """
    from app.clients.web_search import WebSearchClient

    ws = WebSearchClient()

    domain = domain.strip().lower() if domain else ""
    has_domain = bool(domain) and not domain.endswith(".unknown")

    if not has_domain:
        domain = await _resolve_domain(ws, company_name)
        has_domain = bool(domain) and not domain.endswith(".unknown")

    # Run only free collection tasks (no Apollo, no Hunter)
    collected = await _collect_free_data(ws, company_name, domain, has_domain)

    # Inject existing DB contacts so Claude can factor them into coverage analysis
    if db_contacts:
        collected["all_contacts"] = db_contacts
        collected["db_contacts"] = db_contacts
    else:
        collected["all_contacts"] = []

    icp_analysis = await _run_icp_analysis(
        company_name=company_name,
        domain=domain,
        collected=collected,
        extra_context=extra_context,
    )

    return {
        "company_name": company_name,
        "domain": domain,
        "collected_at": datetime.utcnow().isoformat(),
        "raw_data": {
            "scraped_pages": collected.get("scraped", {}).get("pages_scraped", 0),
            "search_results_count": len(collected.get("general_search") or []),
            "apollo_found": False,
            "contacts_found": len(db_contacts or []),
            "hunter_contacts_found": 0,
            "news_results": (collected.get("news") or {}).get("total_articles", 0)
            if isinstance(collected.get("news"), dict)
            else len(collected.get("news") or []),
        },
        "icp_analysis": icp_analysis,
        "_all_contacts": db_contacts or [],
        "_collected": collected,
    }


async def _collect_free_data(
    ws, company_name: str, domain: str, has_domain: bool
) -> dict[str, Any]:
    """Run all free (non-paid-API) data collection tasks in parallel."""
    tasks: dict[str, Any] = {}
    domain_hint = f" {domain}" if has_domain and domain and not domain.endswith(".unknown") else ""

    if has_domain:
        tasks["scraped"] = ws.scrape_company_pages(domain)

    tasks["general_search"] = ws.search(
        f'"{company_name}"{domain_hint} company enterprise SaaS what they do', max_results=5
    )
    tasks["ps_hiring"] = ws.search(
        f'"{company_name}"{domain_hint} hiring "professional services" OR "implementation" OR '
        f'"solutions engineer" OR "delivery" OR "onboarding" OR "customer success"',
        max_results=4,
    )
    tasks["leadership"] = ws.search(
        f'"{company_name}"{domain_hint} "VP" OR "hired" OR "appointed" OR "new CTO" OR '
        f'"Chief" OR "Head of" leadership 2025 OR 2026',
        max_results=4,
    )
    tasks["funding"] = ws.search(
        f'"{company_name}"{domain_hint} funding OR raised OR acquisition OR partnership OR '
        f'expansion OR "Series" OR IPO 2024 OR 2025 OR 2026',
        max_results=5,
    )
    tasks["reviews"] = ws.search(
        f'"{company_name}"{domain_hint} site:g2.com OR "case study" OR "implementation" OR '
        f'"took months" OR "complex" OR "configuration" OR review',
        max_results=4,
    )
    tasks["events"] = ws.search(
        f'"{company_name}"{domain_hint} conference OR webinar OR summit OR "thought leadership" '
        f'OR keynote 2025 OR 2026',
        max_results=3,
    )
    tasks["ai_overlap"] = ws.search(
        f'"{company_name}"{domain_hint} "AI" OR "agentic" OR "automation" OR "built-in" OR '
        f'"internal AI" OR "machine learning" implementation',
        max_results=3,
    )

    from app.clients.news import NewsClient
    news_client = NewsClient()
    tasks["news"] = news_client.get_company_signals(company_name, domain)

    keys = list(tasks.keys())
    coros = list(tasks.values())

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=45,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Free data collection timed out for {company_name}")
        results = [None] * len(coros)

    collected: dict[str, Any] = {}
    for key, result in zip(keys, results):
        if isinstance(result, Exception):
            logger.warning(
                "Free research task '%s' failed for %s: %s",
                key,
                company_name,
                safe_error_message(result),
            )
            collected[key] = [] if key != "scraped" else {"text": "", "pages_scraped": 0}
        else:
            collected[key] = result

    collected["_company_name"] = company_name
    collected["_domain"] = domain
    return collected


async def research_company_and_update_free(
    company_id: UUID,
    session,
) -> dict[str, Any] | None:
    """
    Run ICP research without spending Apollo or Hunter credits.

    Uses web research (Serper/scraping/news) + Claude analysis.
    Pulls any contacts already in the DB for this company and passes them
    to Claude so committee coverage is informed by existing stakeholders,
    prospects, and deal contacts.
    """
    from app.models.company import Company
    from app.models.contact import Contact
    from app.services.account_sourcing import (
        _build_committee_coverage,
        _build_prospecting_priorities,
        refresh_company_prospecting_fields,
        refresh_contact_sequence_plan,
    )
    from app.services.icp_scorer import score_company
    from sqlmodel import select

    company = await session.get(Company, company_id)
    if not company:
        logger.warning(f"ICP free research: company {company_id} not found")
        return None

    # Pull existing contacts from DB (prospects, stakeholders, deal contacts)
    contact_rows = (
        await session.execute(select(Contact).where(Contact.company_id == company_id))
    ).scalars().all()

    db_contacts = [
        {
            "first_name": c.first_name or "",
            "last_name": c.last_name or "",
            "name": c.name or "",
            "email": c.email or "",
            "title": c.title or "",
            "linkedin_url": c.linkedin_url or "",
            "phone": c.phone or "",
            "_source": "database",
        }
        for c in contact_rows
    ]

    extra_context = _build_extra_context(company)
    if db_contacts:
        extra_context["existing_contacts"] = "; ".join(
            f"{c['name']} ({c['title']})" for c in db_contacts if c.get("name") or c.get("title")
        )

    result = await research_company_free(
        company_name=company.name,
        domain=company.domain,
        extra_context=extra_context if extra_context else None,
        db_contacts=db_contacts,
    )

    icp = result.get("icp_analysis", {})
    if not icp:
        return None

    collected = result.get("_collected", {}) if isinstance(result.get("_collected"), dict) else {}
    research_quality = _research_quality_snapshot(result, icp)
    icp = _calibrate_icp_output(icp, research_quality)
    icp["sources"] = _build_research_sources(collected)
    positive_signals, negative_signals = _analysis_signals(icp)
    sales_play = _build_sales_play(icp)
    analyzed_at = datetime.utcnow().isoformat()

    # Update domain if resolved
    resolved_domain = result.get("domain", "")
    domain_resolution = await _safe_apply_resolved_domain(session, company, resolved_domain)

    if icp.get("company_overview"):
        company.description = str(icp["company_overview"])[:2000]
    if icp.get("industry") and icp["industry"] != "Unknown":
        company.industry = str(icp["industry"])[:200]
    if icp.get("category"):
        company.vertical = str(icp["category"])[:200]
    if icp.get("employee_count"):
        try:
            company.employee_count = int(icp["employee_count"])
        except (TypeError, ValueError):
            pass
    if icp.get("funding_stage"):
        company.funding_stage = str(icp["funding_stage"])[:100]
    arr_estimate = _parse_arr_estimate(icp.get("arr_estimate"))
    if arr_estimate and not company.arr_estimate:
        company.arr_estimate = arr_estimate
    if icp.get("region") and icp["region"] not in ("Unknown", "unknown", ""):
        company.region = str(icp["region"])[:50]
    if icp.get("headquarters") and icp["headquarters"] not in ("Unknown", "unknown", ""):
        company.headquarters = str(icp["headquarters"])[:200]
    if icp.get("account_thesis"):
        company.account_thesis = str(icp["account_thesis"])[:2000]
    if icp.get("why_now"):
        company.why_now = str(icp["why_now"])[:2000]
    if icp.get("beacon_angle"):
        company.beacon_angle = str(icp["beacon_angle"])[:2000]
    if icp.get("recommended_outreach_strategy"):
        company.recommended_outreach_lane = str(icp["recommended_outreach_strategy"])[:500]

    cache = copy.deepcopy(company.enrichment_cache or {})
    cache["icp_analysis"] = {
        "data": icp,
        "raw_data_summary": result.get("raw_data", {}),
        "research_quality": research_quality,
        "sales_play": sales_play,
        "analyzed_at": analyzed_at,
    }
    cache["research_quality"] = {"data": research_quality, "fetched_at": analyzed_at}
    scraped = collected.get("scraped")
    if isinstance(scraped, dict):
        cache["web_scrape"] = {"data": scraped, "fetched_at": analyzed_at}
    intent_signal_snapshot = {
        k: collected.get(k) if isinstance(collected.get(k), list) else []
        for k in ("general_search", "ps_hiring", "leadership", "funding", "reviews", "events", "ai_overlap", "news")
    }
    cache["intent_signals"] = {"data": intent_signal_snapshot, "fetched_at": analyzed_at}
    cache["ai_summary"] = {
        "data": {
            "description": icp.get("company_overview"),
            "icp_fit_reasoning": icp.get("icp_why"),
            "intent_signals_summary": icp.get("intent_why"),
            "recommended_approach": icp.get("recommended_outreach_strategy"),
            "pain_points": sales_play.get("proof_points", []),
            "talking_points": [
                item for item in [
                    icp.get("conversation_starter"),
                    icp.get("beacon_angle"),
                    icp.get("why_now"),
                ] if item
            ],
            "competitive_landscape": _extract_competitor_names(icp),
            "tech_stack_signals": [],
        },
        "fetched_at": analyzed_at,
    }
    cache["competitive_landscape_v2"] = _build_competitive_landscape_cards(company.name, icp)
    cache["domain_resolution"] = {
        **domain_resolution,
        "attempted_at": analyzed_at,
    }
    company.enrichment_cache = cache

    enrichment_sources = copy.deepcopy(company.enrichment_sources or {})
    import_data = enrichment_sources.get("import", {}) if isinstance(enrichment_sources.get("import"), dict) else {}
    existing_uploaded_analyst = import_data.get("uploaded_analyst") if isinstance(import_data.get("uploaded_analyst"), dict) else {}
    if not existing_uploaded_analyst:
        analyst_block = import_data.get("analyst") if isinstance(import_data.get("analyst"), dict) else {}
        if analyst_block:
            import_data["uploaded_analyst"] = copy.deepcopy(analyst_block)

    generated_analyst = {
        k: icp.get(k) for k in (
            "classification", "fit_type", "confidence", "icp_fit_score", "intent_score",
            "icp_why", "intent_why", "category", "core_focus", "revenue_funding",
            "company_overview", "industry", "financial_capacity_met", "employee_count",
            "funding_stage", "arr_estimate", "committee_coverage", "open_gaps",
            "account_thesis", "why_now", "beacon_angle", "recommended_outreach_strategy",
            "conversation_starter", "next_steps", "implementation_cycle",
        )
    }
    import_data["generated_analyst"] = generated_analyst
    merged_analyst = dict(generated_analyst)
    for key, value in (import_data.get("uploaded_analyst") or {}).items():
        if value is not None and value != "":
            merged_analyst[key] = value
    import_data["analyst"] = merged_analyst
    import_data["generated_signals"] = {"positive": positive_signals, "negative": negative_signals}
    if not import_data.get("uploaded_signals"):
        import_data["uploaded_signals"] = {"positive": positive_signals, "negative": negative_signals}
    enrichment_sources["import"] = import_data
    company.enrichment_sources = enrichment_sources

    intent_signals = copy.deepcopy(company.intent_signals or {})
    intent_signals.update({
        "uploaded_intent_score": intent_signals.get("uploaded_intent_score", icp.get("intent_score")),
        "uploaded_fit_type": intent_signals.get("uploaded_fit_type", icp.get("fit_type")),
        "uploaded_classification": intent_signals.get("uploaded_classification", icp.get("classification")),
        "uploaded_confidence": intent_signals.get("uploaded_confidence", icp.get("confidence")),
        "positive_signal_count": intent_signals.get("positive_signal_count", len(positive_signals or [])),
        "negative_signal_count": intent_signals.get("negative_signal_count", len(negative_signals or [])),
        "uploaded_signals": intent_signals.get("uploaded_signals") or {"positive": positive_signals, "negative": negative_signals},
        "generated_intent_score": icp.get("intent_score"),
        "generated_fit_type": icp.get("fit_type"),
        "generated_classification": icp.get("classification"),
        "generated_confidence": icp.get("confidence"),
        "generated_positive_signal_count": len(positive_signals or []),
        "generated_negative_signal_count": len(negative_signals or []),
        "generated_signals": {"positive": positive_signals, "negative": negative_signals},
        "research_evidence_level": research_quality.get("evidence_level"),
    })
    company.intent_signals = intent_signals

    profile = copy.deepcopy(company.prospecting_profile or {})
    for key in ("recommended_outreach_strategy", "conversation_starter", "icp_personas",
                "committee_coverage", "open_gaps", "next_steps"):
        if icp.get(key):
            profile[key] = icp[key]
    profile["sales_play"] = sales_play
    profile["proof_points"] = sales_play.get("proof_points", [])
    profile["risk_flags"] = sales_play.get("risk_flags", [])
    profile["best_entry_persona"] = sales_play.get("best_persona")
    profile["research_quality"] = research_quality
    profile["contact_discovery_status"] = "db_only"
    profile["contact_discovery_note"] = (
        f"Used {len(db_contacts)} existing contact(s) from database. "
        "No Apollo/Hunter credits spent."
    )
    company.prospecting_profile = profile

    company.icp_score, company.icp_tier = score_company(company)
    company.enriched_at = datetime.utcnow()
    company.updated_at = datetime.utcnow()
    session.add(company)
    await session.flush()

    try:
        await session.commit()
    except Exception as exc:
        logger.warning(
            "Failed to persist free ICP research for %s: %s",
            company.name,
            safe_error_message(exc),
        )
        await session.rollback()
        return None

    await session.refresh(company)

    committee_coverage = await _build_committee_coverage(company, session)
    cache = copy.deepcopy(company.enrichment_cache or {})
    cache["committee_coverage"] = {"data": committee_coverage, "fetched_at": datetime.utcnow().isoformat()}
    priorities = _build_prospecting_priorities(
        company,
        committee_coverage,
        company.intent_signals if isinstance(company.intent_signals, dict) else {},
    )
    cache["prospecting_priorities"] = {"data": priorities, "fetched_at": datetime.utcnow().isoformat()}
    company.enrichment_cache = cache

    refreshed_profile = copy.deepcopy(company.prospecting_profile or {})
    if icp.get("committee_coverage"):
        refreshed_profile["committee_coverage"] = icp.get("committee_coverage")
    refreshed_profile["priorities"] = priorities
    refreshed_profile["committee_snapshot"] = committee_coverage
    company.prospecting_profile = refreshed_profile

    contacts_all = (
        await session.execute(select(Contact).where(Contact.company_id == company.id))
    ).scalars().all()
    company = refresh_company_prospecting_fields(company, contacts_all)
    for contact in contacts_all:
        refresh_contact_sequence_plan(contact, company)
        session.add(contact)

    company.icp_score, company.icp_tier = score_company(company)
    company.enriched_at = datetime.utcnow()
    company.updated_at = datetime.utcnow()
    session.add(company)
    await session.commit()
    await session.refresh(company)

    logger.info(
        f"Free ICP research complete: {company.name} ({company.domain}) "
        f"— Score {company.icp_score} ({company.icp_tier}), "
        f"DB contacts used: {len(db_contacts)}"
    )
    return icp
