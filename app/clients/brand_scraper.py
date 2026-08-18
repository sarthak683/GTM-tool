"""
Brand scraper — extracts primary colour, logo URL, and description from a
client's website using httpx + BeautifulSoup.

Kept intentionally lightweight: no Playwright, no JS rendering.
Falls back gracefully when scraping fails.
"""
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
TIMEOUT = 10.0


async def scrape_brand(domain: str) -> dict:
    """
    Scrape brand data from a domain.

    Returns a dict with:
      primary_color   – CSS hex (e.g. "#1a73e8") or empty string
      secondary_color – CSS hex or empty string
      logo_url        – absolute URL or empty string
      description     – meta description or empty string
      error           – non-empty string if scraping failed
    """
    url = _normalise_url(domain)
    result = {
        "primary_color": "",
        "secondary_color": "",
        "logo_url": "",
        "description": "",
        "error": "",
    }

    try:
        async with httpx.AsyncClient(
            headers=HEADERS,
            timeout=TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        result["error"] = str(exc)
        return result

    soup = BeautifulSoup(html, "lxml")

    # ── Description ──────────────────────────────────────────────────────────
    meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if meta_desc and meta_desc.get("content"):
        result["description"] = meta_desc["content"].strip()[:400]

    # ── Logo ─────────────────────────────────────────────────────────────────
    result["logo_url"] = _find_logo(soup, url)

    # ── Colours from inline CSS / style attributes ────────────────────────────
    colors = _extract_colors_from_css(html)
    if colors:
        result["primary_color"] = colors[0]
        if len(colors) > 1:
            result["secondary_color"] = colors[1]

    # Fallback: og:image dominant colour hint (skip — requires Pillow scraping)
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_url(domain: str) -> str:
    domain = domain.strip()
    if not domain.startswith(("http://", "https://")):
        domain = "https://" + domain
    return domain


def _find_logo(soup: BeautifulSoup, base_url: str) -> str:
    # 1. og:image
    og = soup.find("meta", attrs={"property": "og:image"})
    if og and og.get("content"):
        return urljoin(base_url, og["content"])

    # 2. <link rel="icon" ...> / apple-touch-icon
    for rel in ("apple-touch-icon", "icon", "shortcut icon"):
        # `rel=rel` binds this iteration's value. Without it the lambda closes
        # over the loop variable, which happens to work here only because
        # soup.find() calls it synchronously — it would silently match the last
        # rel if that ever became lazy.
        tag = soup.find("link", rel=lambda r, rel=rel: r and rel in r)  # type: ignore[arg-type]
        if tag and tag.get("href"):
            return urljoin(base_url, tag["href"])

    # 3. <img> whose src / alt contains "logo"
    for img in soup.find_all("img"):
        src = img.get("src", "")
        alt = img.get("alt", "").lower()
        if "logo" in src.lower() or "logo" in alt:
            return urljoin(base_url, src)

    return ""


# Regex for hex colours in CSS
_HEX_RE = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")

# Colours to skip (white, black, near-grey)
_BORING = {
    "ffffff", "000000", "f5f5f5", "eeeeee", "cccccc",
    "333333", "666666", "999999", "aaaaaa", "dddddd",
}


def _extract_colors_from_css(html: str) -> list[str]:
    """Return up to 3 dominant non-boring hex colours found in inline CSS."""
    seen: dict[str, int] = {}
    for match in _HEX_RE.finditer(html):
        raw = match.group(1).lower()
        if len(raw) == 3:
            raw = raw[0] * 2 + raw[1] * 2 + raw[2] * 2
        if raw not in _BORING:
            seen[raw] = seen.get(raw, 0) + 1

    # Return the top colours by frequency
    ranked = sorted(seen.items(), key=lambda x: x[1], reverse=True)
    return [f"#{h}" for h, _ in ranked[:3]]
