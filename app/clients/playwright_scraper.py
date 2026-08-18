"""
Playwright-based web scraper for company and contact research.

Used in pre-meeting intelligence to fetch:
  - Company homepage (value prop, product description)
  - /about page if it exists
  - LinkedIn profile page for a contact

Returns clean text that gets fed to GPT-4o for brief generation.
Falls back gracefully if page load fails or times out.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Selectors to extract meaningful text from — ordered by priority
_CONTENT_SELECTORS = [
    "main",
    "article",
    '[class*="hero"]',
    '[class*="about"]',
    '[class*="mission"]',
    "section",
    "body",
]


async def scrape_linkedin_profile(linkedin_url: str) -> dict:
    """
    Scrape a LinkedIn public profile page for headline, summary, and recent experience.
    LinkedIn blocks headless browsers aggressively — this uses realistic browser fingerprints
    and falls back gracefully if blocked.

    Returns dict with keys: headline, summary, experience, error.
    """
    result: dict = {"url": linkedin_url, "headline": "", "summary": "", "experience": "", "error": None}

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                java_script_enabled=True,
                locale="en-US",
            )
            page = await context.new_page()
            await page.route(
                "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}",
                lambda route: route.abort(),
            )

            try:
                await page.goto(linkedin_url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)

                # Headline (job title / tagline shown under name)
                for sel in [".text-body-medium", ".pv-top-card--list .text-body-medium", "h2"]:
                    try:
                        el = page.locator(sel).first
                        text = await el.inner_text(timeout=2000)
                        if text and len(text.strip()) > 5:
                            result["headline"] = text.strip()[:300]
                            break
                    except Exception:
                        continue

                # About / summary section
                for sel in ['[data-section="summary"]', ".pv-about-section", ".summary"]:
                    try:
                        el = page.locator(sel).first
                        text = await el.inner_text(timeout=2000)
                        if text and len(text.strip()) > 20:
                            result["summary"] = text.strip()[:800]
                            break
                    except Exception:
                        continue

                # Experience section — most recent 2 entries
                for sel in ['[data-section="experience"]', ".experience-section", "#experience"]:
                    try:
                        el = page.locator(sel).first
                        text = await el.inner_text(timeout=2000)
                        if text and len(text.strip()) > 20:
                            result["experience"] = text.strip()[:600]
                            break
                    except Exception:
                        continue

            except Exception as e:
                result["error"] = f"Page load failed: {e}"

            await browser.close()

    except Exception as e:
        logger.warning(f"LinkedIn scrape failed for {linkedin_url}: {e}")
        result["error"] = str(e)

    return result
