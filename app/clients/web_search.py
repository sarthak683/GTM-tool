"""
Web research client — company website scraping + Serper search.

Sources:
  - Company's own website: fetches homepage + /about, extracts key text,
    then uses GPT-4o to produce a clean company background summary.
  - Serper: Google-backed search API for recent news and milestones.

Used by the pre-meeting intelligence pipeline.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from app.config import settings
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


class WebSearchClient:
    # ── Serper ────────────────────────────────────────────────────────────────

    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        """
        Serper web search.
        Returns list of {title, url, snippet}.
        """
        api_key = settings.SERPER_API_KEY.strip()
        if not api_key:
            logger.warning("Serper search requested but SERPER_API_KEY is empty")
            return []

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    "https://google.serper.dev/search",
                    headers={
                        "X-API-KEY": api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "q": query,
                        "num": max_results,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                organic = payload.get("organic", [])
                results = []
                for item in organic[:max_results]:
                    if not isinstance(item, dict):
                        continue
                    results.append(
                        {
                            "title": item.get("title", ""),
                            "url": item.get("link", ""),
                            "snippet": item.get("snippet", ""),
                        }
                    )
                return results
        except Exception as e:
            logger.warning("Serper search failed for '%s': %s", query, safe_error_message(e))
            return []

    async def recent_news(self, company_name: str, domain: str) -> list[dict]:
        """Recent funding rounds, launches, PR — uses domain to disambiguate common names."""
        # Include domain so "Rippling rippling.com" doesn't return medical results
        domain_hint = f" {domain}" if domain and not domain.endswith(".unknown") else ""
        query = f'"{company_name}"{domain_hint} funding OR acquisition OR launch OR partnership 2024 OR 2025 OR 2026'
        results = await self.search(query, max_results=5)
        if not results:
            results = await self.search(
                f"{company_name}{domain_hint} company news 2025 2026", max_results=5
            )
        return results

    async def company_milestones(self, company_name: str, domain: str = "") -> list[dict]:
        """Company history and milestone search."""
        domain_hint = f" {domain}" if domain and not domain.endswith(".unknown") else ""
        query = f'"{company_name}"{domain_hint} company history milestones founded'
        return await self.search(query, max_results=4)

    async def search_intent_signals(self, company_name: str, domain: str) -> dict:
        """
        Single Serper call to detect buying intent signals, then classify
        results locally into hiring/funding/product buckets via keyword matching.
        Previously used 3 separate API calls — now uses 1 (saves ~66% Serper credits).
        """
        signals: dict = {"hiring": [], "funding": [], "product": [], "tech": [], "raw_results": []}
        domain_hint = f" {domain}" if domain and not domain.endswith(".unknown") else ""

        # One broad query covering all intent signal types
        results = await self.search(
            f'"{company_name}"{domain_hint} hiring OR funding OR raised OR launch OR '
            f'expansion OR partnership OR recruiting OR series 2025 OR 2026',
            max_results=10,
        )
        if not results:
            return signals

        # Classify each result by keyword matching
        _hiring_kw = {"hiring", "recruiting", "open positions", "job", "careers", "we're hiring", "team"}
        _funding_kw = {"funding", "raised", "investment", "series", "valuation", "capital", "ipo", "acquisition"}
        _product_kw = {"launch", "expansion", "partnership", "new product", "release", "growth", "customers"}

        for r in results:
            text = f"{r.get('title', '')} {r.get('snippet', '')}".lower()
            entry = {"title": r.get("title", ""), "snippet": r.get("snippet", "")}

            if any(kw in text for kw in _hiring_kw):
                signals["hiring"].append(entry)
            elif any(kw in text for kw in _funding_kw):
                signals["funding"].append(entry)
            elif any(kw in text for kw in _product_kw):
                signals["product"].append(entry)
            else:
                signals["product"].append(entry)  # default bucket

        signals["raw_results"] = results
        return signals

    async def scrape_company_pages(self, domain: str) -> dict:
        """
        Scrape company homepage + /about + /company pages.
        Returns raw text for AI summarization (no AI call here).
        """
        if not domain or domain.endswith(".unknown"):
            return {"text": "", "pages_scraped": 0}

        base_url = f"https://{domain}"
        pages_to_try = [
            base_url,
            f"{base_url}/about",
            f"{base_url}/about-us",
            f"{base_url}/company",
            f"{base_url}/customers",
            f"{base_url}/pricing",
            f"{base_url}/careers",
            f"{base_url}/products",
        ]

        raw_text = ""
        pages_scraped = 0
        urls_scraped: list[str] = []

        async with httpx.AsyncClient(
            timeout=12,
            follow_redirects=True,
            headers=_SCRAPE_HEADERS,
        ) as client:
            for url in pages_to_try:
                text = await _fetch_page_text(client, url)
                if text and len(text) > 100:
                    raw_text += f"\n\n[{url}]\n{text}"
                    pages_scraped += 1
                    urls_scraped.append(url)
                    if len(raw_text) > 6000:
                        break

        return {"text": raw_text[:8000], "pages_scraped": pages_scraped, "urls_scraped": urls_scraped}

    # ── Company website scraping + GPT-4o summary ─────────────────────────────

    async def company_website_summary(
        self, domain: str, company_name: str, ai_client
    ) -> Optional[dict]:
        """
        Scrape the company's own website (homepage + /about) and use GPT-4o
        to produce a clean 3-4 sentence company background summary.

        Returns {title, description, extract, url, founded} or None on failure.
        """
        if not domain or domain.endswith(".unknown"):
            return None

        base_url = f"https://{domain}"
        pages_to_try = [base_url, f"{base_url}/about", f"{base_url}/company"]

        raw_text = ""
        source_url = base_url

        async with httpx.AsyncClient(
            timeout=12,
            follow_redirects=True,
            headers=_SCRAPE_HEADERS,
        ) as client:
            for url in pages_to_try:
                text = await _fetch_page_text(client, url)
                if text and len(text) > 100:
                    raw_text += f"\n\n[{url}]\n{text}"
                    source_url = url
                    if len(raw_text) > 3000:
                        break

        if not raw_text.strip():
            logger.warning(f"Could not scrape any content from {domain}")
            return None

        # Trim to avoid huge GPT-4o context
        raw_text = raw_text[:4000]

        # Ask GPT-4o to extract structured company info
        system = (
            "You are a B2B sales researcher. Given raw website text, extract a concise "
            "company profile. Respond ONLY in this exact format:\n"
            "DESCRIPTION: <one sentence describing what the company does>\n"
            "EXTRACT: <2-3 sentences covering the company's core product, target customers, "
            "and key differentiator. Be specific, not generic.>\n"
            "FOUNDED: <year or blank>\n"
            "If you cannot determine a field, leave it blank after the colon."
        )
        user = (
            f"Company: {company_name}\nWebsite: {domain}\n\n"
            f"Raw page content:\n{raw_text}"
        )

        try:
            response = await ai_client.complete(system, user, max_tokens=300)
            if not response:
                return None
            return _parse_ai_company_profile(response, company_name, source_url)
        except Exception as e:
            logger.warning("GPT-4o company summary failed for %s: %s", domain, safe_error_message(e))
            return None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _fetch_page_text(client: httpx.AsyncClient, url: str) -> str:
    """Fetch a URL and return visible text content (no nav/footer noise)."""
    try:
        resp = await client.get(url)
        if resp.status_code not in (200, 201):
            return ""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove noise elements
        for tag in soup(["script", "style", "nav", "footer", "header",
                          "noscript", "iframe", "aside"]):
            tag.decompose()

        # Prefer <main> or <article> if available for denser signal
        main = soup.find("main") or soup.find("article") or soup
        text = main.get_text(separator=" ", strip=True)

        # Collapse whitespace
        text = re.sub(r"\s{2,}", " ", text)
        return text[:2000]
    except Exception as e:
        logger.debug(f"Page fetch failed for {url}: {e}")
        return ""


def _parse_ai_company_profile(response: str, company_name: str, url: str) -> dict:
    """Parse the structured GPT-4o response into a company background dict."""
    lines = {
        k.strip().upper(): v.strip()
        for line in response.strip().splitlines()
        if ":" in line
        for k, v in [line.split(":", 1)]
    }
    return {
        "title": company_name,
        "description": lines.get("DESCRIPTION", ""),
        "extract": lines.get("EXTRACT", ""),
        "url": url,
        "founded": lines.get("FOUNDED") or None,
    }


def _extract_founded(text: str) -> Optional[str]:
    """Pull founding year from raw text."""
    match = re.search(
        r"founded in (\d{4})|incorporated in (\d{4})|established in (\d{4})", text, re.I
    )
    if match:
        return match.group(1) or match.group(2) or match.group(3)
    match = re.search(r"\b(19[5-9]\d|20[0-2]\d)\b", text)
    return match.group(1) if match else None
