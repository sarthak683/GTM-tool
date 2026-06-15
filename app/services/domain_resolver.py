"""
Verified domain resolver for companies imported without a known website.

The previous implementation relied mainly on AI guesses. That was good enough to
recover some domains, but it could still:
  - fall back to `.unknown` too often
  - accept the wrong site for a company with a common/ambiguous name

This resolver now uses multiple strategies and verifies candidates before
accepting them:
  1. obvious `.com` guesses from normalized company names
  2. web-search derived hosts
  3. AI suggestions as a last resort

Candidates are verified against the actual website content so we do not accept a
domain whose title/body clearly belongs to a different company.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.company import Company
from app.services.log_safety import safe_error_message

logger = logging.getLogger(__name__)

_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_SKIP_HOSTS = {
    "linkedin.com",
    "crunchbase.com",
    "wikipedia.org",
    "bloomberg.com",
    "glassdoor.com",
    "g2.com",
    "trustpilot.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "github.com",
    "zoominfo.com",
    "pitchbook.com",
    "tracxn.com",
    "owler.com",
}

_LEGAL_SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "llc",
    "ltd",
    "limited",
    "plc",
    "sa",
    "ag",
    "gmbh",
    "bv",
    "holdings",
    "holding",
}


@dataclass
class VerifiedDomain:
    domain: str
    strategy: str
    score: int


def _strip_parens(value: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", value or "").strip()


def _company_name_variants(name: str) -> list[str]:
    raw = (name or "").strip()
    variants: list[str] = []
    if raw:
        variants.append(raw)

    without_parens = _strip_parens(raw)
    if without_parens and without_parens not in variants:
        variants.append(without_parens)

    for separator in (" - ", " / ", " | "):
        if separator in without_parens:
            first = without_parens.split(separator)[0].strip()
            if first and first not in variants:
                variants.append(first)

    legal_trimmed = _trim_legal_suffixes(without_parens)
    if legal_trimmed and legal_trimmed not in variants:
        variants.append(legal_trimmed)

    return variants[:4]


def _trim_legal_suffixes(name: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", (name or "").lower())
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens).strip()


def _name_tokens(name: str) -> list[str]:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", _trim_legal_suffixes(name))
        if len(token) >= 3
    ]
    return tokens or [token for token in re.findall(r"[a-z0-9]+", name.lower()) if len(token) >= 3]


def _canonicalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _candidate_domains(company_name: str) -> list[str]:
    seen: set[str] = set()
    candidates: list[str] = []

    def _add(root: str) -> None:
        if len(root) < 3:
            return
        domain = f"{root}.com"
        if domain not in seen:
            seen.add(domain)
            candidates.append(domain)

    # Try parenthetical content first (e.g., "Ceridian (Dayforce)" → dayforce.com)
    parens = re.findall(r"\(([^)]+)\)", company_name or "")
    for paren in parens:
        paren_tokens = re.findall(r"[a-z0-9]+", paren.lower())
        for token in paren_tokens:
            if len(token) >= 4 and token not in _LEGAL_SUFFIXES:
                _add(token)

    for variant in _company_name_variants(company_name):
        tokens = re.findall(r"[a-z0-9]+", variant.lower())
        if not tokens:
            continue
        trimmed = [token for token in tokens if token not in _LEGAL_SUFFIXES] or tokens
        # Try first token first — most SaaS companies use company.com (veeva.com, not veevasystems.com)
        if len(trimmed) >= 2:
            _add(trimmed[0])
        # Then try joined and hyphenated
        _add("".join(trimmed))       # veevasystems.com
        _add("-".join(trimmed))      # veeva-systems.com
        # Then individual significant tokens
        for token in trimmed[1:]:
            if len(token) >= 4 and token not in _LEGAL_SUFFIXES:
                _add(token)

    return candidates[:12]


def _clean_host(host: str) -> str:
    host = (host or "").strip().lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host.split("/")[0]


def _root_domain(host: str) -> str:
    host = _clean_host(host)
    parts = [part for part in host.split(".") if part]
    if len(parts) <= 2:
        return host
    if parts[-2] in {"co", "com", "org", "net"} and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _host_is_skippable(host: str) -> bool:
    host = _root_domain(host)
    return any(host == blocked or host.endswith(f".{blocked}") for blocked in _SKIP_HOSTS)


def _extract_candidate_hosts(results: list[dict[str, Any]]) -> list[str]:
    hosts: list[str] = []
    seen: set[str] = set()
    for result in results:
        url = result.get("url") or result.get("href") or ""
        if not url:
            continue
        parsed = urlparse(url)
        host = _root_domain(parsed.netloc)
        if not host or _host_is_skippable(host) or host in seen:
            continue
        seen.add(host)
        hosts.append(host)
    return hosts


async def _fetch_domain_evidence(domain: str) -> tuple[str, str, str]:
    urls = [f"https://{domain}", f"https://www.{domain}", f"http://{domain}"]
    async with httpx.AsyncClient(
        timeout=8,
        follow_redirects=True,
        headers=_SCRAPE_HEADERS,
    ) as client:
        for url in urls:
            try:
                response = await client.get(url)
            except Exception:
                continue
            if response.status_code >= 400 or "text/html" not in response.headers.get("content-type", ""):
                continue
            final_host = _root_domain(urlparse(str(response.url)).netloc or domain)
            soup = BeautifulSoup(response.text, "html.parser")
            title = (soup.title.text or "").strip() if soup.title else ""
            meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
            description = (meta.get("content") or "").strip() if meta else ""
            text = soup.get_text(" ", strip=True)
            return final_host, title, f"{description} {text[:2500]}".strip()
    return "", "", ""


def _token_match_score_single(name: str, host: str, title: str, body: str, blob: str) -> int:
    """Score a single name variant against the domain evidence."""
    tokens = _name_tokens(name)
    if not tokens:
        return 0

    company_canonical = _canonicalize_text(_trim_legal_suffixes(name))
    host_root = _canonicalize_text(host.split(".")[0].replace("-", " "))

    score = 0
    if company_canonical and company_canonical in blob.replace(" ", ""):
        score += 55
    if host_root and company_canonical and (host_root == company_canonical or company_canonical.startswith(host_root) or host_root.startswith(company_canonical)):
        score += 30

    token_hits = sum(1 for token in tokens if token in blob)
    if token_hits:
        score += min(token_hits * 12, 36)

    required_hits = max(1, math.ceil(len(tokens) * 0.6))
    if token_hits >= required_hits:
        score += 12

    return score


def _token_match_score(company_name: str, host: str, title: str, body: str) -> int:
    blob = " ".join(part for part in (host, title, body) if part).lower()

    # Try each name variant (including parenthetical content) and take the best
    best = _token_match_score_single(company_name, host, title, body, blob)
    for variant in _company_name_variants(company_name):
        best = max(best, _token_match_score_single(variant, host, title, body, blob))

    # Also try parenthetical content as standalone name (e.g. "Dayforce" from "Ceridian (Dayforce)")
    for paren in re.findall(r"\(([^)]+)\)", company_name or ""):
        paren_stripped = paren.strip()
        if len(paren_stripped) >= 3 and paren_stripped.lower() not in _LEGAL_SUFFIXES:
            best = max(best, _token_match_score_single(paren_stripped, host, title, body, blob))

    return best


def _names_roughly_match(reference: str, candidate: str) -> bool:
    ref_tokens = set(_name_tokens(reference))
    cand_tokens = set(_name_tokens(candidate))
    if not ref_tokens or not cand_tokens:
        return False
    overlap = len(ref_tokens & cand_tokens)
    return overlap >= max(1, math.ceil(len(ref_tokens) * 0.6))


async def _provider_name_match(domain: str, company_name: str) -> int:
    score = 0

    try:
        from app.clients.apollo import ApolloClient

        apollo = ApolloClient()
        if not apollo.mock:
            apollo_data = await apollo.enrich_company(domain)
            apollo_name = apollo_data.get("name") if isinstance(apollo_data, dict) else None
            if isinstance(apollo_name, str) and _names_roughly_match(company_name, apollo_name):
                score += 30
    except Exception as exc:
        logger.debug("Apollo verification skipped for %s: %s", domain, safe_error_message(exc))

    try:
        from app.clients.hunter import HunterClient

        hunter = HunterClient()
        if not hunter.mock:
            hunter_data = await hunter.company_enrichment(domain)
            hunter_name = hunter_data.get("name") if isinstance(hunter_data, dict) else None
            if isinstance(hunter_name, str) and _names_roughly_match(company_name, hunter_name):
                score += 25
    except Exception as exc:
        logger.debug("Hunter verification skipped for %s: %s", domain, safe_error_message(exc))

    return score


async def _verify_candidate(domain: str, company_name: str) -> Optional[int]:
    candidate = _root_domain(domain)
    if not candidate or _host_is_skippable(candidate) or candidate.endswith(".unknown"):
        return None

    final_host, title, body = await _fetch_domain_evidence(candidate)
    verified_host = final_host or candidate
    if _host_is_skippable(verified_host):
        return None

    score = _token_match_score(company_name, verified_host, title, body) if final_host else 0
    provider_bonus = await _provider_name_match(verified_host, company_name)
    score += provider_bonus
    if not final_host and provider_bonus:
        score += 20
    return score if score >= 45 else None


async def resolve_company_domain(
    company_name: str,
    *,
    industry: str | None = None,
    description: str | None = None,
) -> tuple[Optional[str], dict[str, Any]]:
    """
    Resolve a company's domain using multiple strategies and verification.

    Returns `(domain, metadata)`.
    """
    from app.clients.claude import ClaudeClient
    from app.clients.web_search import WebSearchClient

    attempts: list[dict[str, Any]] = []
    seen: set[str] = set()

    async def _try_candidates(candidates: list[str], strategy: str) -> Optional[VerifiedDomain]:
        for raw_candidate in candidates:
            candidate = _root_domain(raw_candidate)
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            score = await _verify_candidate(candidate, company_name)
            attempts.append({"candidate": candidate, "strategy": strategy, "accepted": bool(score), "score": score or 0})
            if score:
                return VerifiedDomain(domain=candidate, strategy=strategy, score=score)
        return None

    obvious = await _try_candidates(_candidate_domains(company_name), "obvious_guess")
    if obvious:
        return obvious.domain, {"strategy": obvious.strategy, "score": obvious.score, "attempts": attempts}

    ws = WebSearchClient()
    search_queries = [
        f'"{company_name}" official website',
        f'"{company_name}" company official site',
    ]
    for query in search_queries:
        results = await ws.search(query, max_results=5)
        verified = await _try_candidates(_extract_candidate_hosts(results), "web_search")
        if verified:
            return verified.domain, {"strategy": verified.strategy, "score": verified.score, "attempts": attempts}

    ai = ClaudeClient()
    ai_candidate = await ai.resolve_domain(
        company_name=company_name,
        industry=industry,
        description=description,
    )
    verified_ai = await _try_candidates([ai_candidate] if ai_candidate else [], "ai_guess")
    if verified_ai:
        return verified_ai.domain, {"strategy": verified_ai.strategy, "score": verified_ai.score, "attempts": attempts}

    return None, {"strategy": "unresolved", "score": 0, "attempts": attempts}


async def resolve_and_update_domain(company: Company, session: AsyncSession) -> bool:
    """
    Attempt to resolve an unknown domain for `company`.

    Returns True if the domain was successfully resolved and updated, False otherwise.
    The caller is responsible for continuing the pipeline either way.
    """
    if not company.domain.endswith(".unknown"):
        return False

    description: str | None = company.description
    if isinstance(company.enrichment_sources, dict):
        import_block = company.enrichment_sources.get("import") or {}
        if isinstance(import_block, dict):
            analyst = import_block.get("analyst") or {}
            if isinstance(analyst, dict):
                description = (
                    analyst.get("core_focus")
                    or analyst.get("icp_why")
                    or analyst.get("intent_why")
                    or description
                )

    resolved, meta = await resolve_company_domain(
        company.name,
        industry=company.industry,
        description=description,
    )
    if not resolved:
        logger.info(f"Domain resolver: no verified domain found for '{company.name}'")
        return False

    existing = await session.execute(
        select(Company).where(Company.domain == resolved, Company.id != company.id)
    )
    if existing.scalar_one_or_none():
        logger.warning(
            f"Domain resolver: '{resolved}' already belongs to another company — skipping update for '{company.name}'"
        )
        return False

    logger.info(
        "Domain resolver: '%s' -> '%s' via %s (score=%s)",
        company.name,
        resolved,
        meta.get("strategy"),
        meta.get("score"),
    )
    company.domain = resolved
    company.updated_at = datetime.utcnow()
    session.add(company)
    await session.commit()
    await session.refresh(company)
    return True
