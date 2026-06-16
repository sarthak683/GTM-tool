"""
Demo AI client — calls Claude to generate self-contained interactive HTML demos.

Design decisions:
  - Uses Claude Sonnet 4 with extended thinking for structured planning before
    generating 15-25K token HTML output.
  - Extended thinking unlocks up to 64K output tokens (vs 16K without).
  - The thinking step helps Claude plan the HTML/CSS/JS structure before writing,
    producing more complete and coherent output.
  - Retries transient errors (overloaded, rate-limit, 5xx) with exponential backoff.
  - Every step is logged with timing so failures are easy to diagnose.
  - No fake fallback HTML — if generation fails, raises RuntimeError with context.
"""
import anthropic
import asyncio
import logging
import re
import time

from app.config import settings

logger = logging.getLogger(__name__)

# ── System prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert frontend developer specialising in self-contained
interactive HTML demo files that showcase enterprise SaaS platform integrations.

You generate a SINGLE, COMPLETE, self-contained HTML file with NO external dependencies.

## Visual layout — two-panel, full-screen (body background: #0D1117)

### LEFT panel — CLIENT SYSTEM (takes 100% width when Beacon is closed, 58% when open)
  • Mimics the CLIENT's actual system UI using their primary brand colour
  • Has a sidebar nav + topbar + content area (like a real SaaS app)
  • Content changes between scenes: starts empty/unconfigured → progressively fills
  • Each scene shows a different "screen" in the client's system (tables, records, forms, reports)
  • Use realistic-looking data (tables with proper headers, status badges, decision logs, etc.)

### RIGHT panel — "Beacon" AI chat panel (slides in from right, 42% width)
  • Slides in when the scene starts playing (transform: translateX from 100% to 0)
  • Header: dark indigo gradient (#1E1B4B → #3730A3), Beacon logo "✦", status dot
  • Chat area: user question bubble (dark blue) + AI response card (white bubble)
  • AI response card contains: intro paragraph + structured steps list
  • Step types: ✓ green (done), ⟳ amber (in-progress), ✗ red (error), ● info, ── section header
  • Steps stream in one-by-one using setTimeout chains at 300–600ms each
  • After all steps → left panel updates to reveal the final configured state

## Scene structure
1. TITLE SCENE (full-screen, dark gradient):
   - Both company logos + "×" separator
   - H1 headline, subtitle, time-comparison card (manual vs AI)
   - "▶ Start Demo" button

2. DEMO SCENES (numbered, accessed via scene-dots nav):
   - Top nav bar: scene dots (●), scene title, timecode, Prev/Next buttons
   - Content area: client system (left) + Beacon chat panel (right, slides in)
   - Action bar (bottom): Play / Skip / status text / "Next Scene →" button
   - Each scene has its own: user prompt, Beacon response, left-panel screen state

3. REVEAL: after a scene completes, the left panel shows the fully-populated state
   before the user moves to the next scene

## Interactivity rules
• "▶ Start Demo" → hides title scene, shows demo scene, scene 1 starts ready to play
• "▶ Play Scene" → starts the animation:
    1. User message appears in Beacon chat
    2. Thinking animation (bouncing dots, 1–2s)
    3. Steps stream in one-by-one
    4. Left panel "screen" switches to show populated state
    5. "Next Scene →" button appears
• "Skip to End" → instantly shows the final state of the current scene
• Scene dots + Prev/Next navigate between scenes instantly
• Beacon panel closes when user clicks "✕" in Beacon header (left panel expands back)
• The demo is fully self-contained: NO external CDN, NO fetch() calls — pure inline HTML + CSS + vanilla JS

## CRITICAL OUTPUT RULES
• Keep to 5-8 scenes maximum to ensure the output stays within token limits.
• Output ONLY the raw HTML. No markdown fences, no commentary, no explanations.
• Start with <!DOCTYPE html> and end with </html>.
• You MUST define these global JS functions: startDemo, prevScene, nextScene, playScene, skipScene.
• Ensure ALL HTML tags are properly closed and ALL JS functions are complete.
"""


# ── Validation ───────────────────────────────────────────────────────────────

def is_valid_demo_html(html: str) -> bool:
    """Validate minimum functional requirements for generated demo HTML."""
    if not html or len(html) < 500:
        return False
    if "<!DOCTYPE html" not in html:
        return False
    if "</html>" not in html.lower():
        return False

    required_handlers = ("startDemo", "prevScene", "nextScene")

    def has_handler(name: str) -> bool:
        patterns = (
            rf"function\s+{name}\s*\(",
            rf"(?:const|let|var)\s+{name}\s*=\s*(?:async\s*)?\(",
            rf"window\.{name}\s*=\s*function\s*\(",
            rf"window\.{name}\s*=\s*(?:async\s*)?\(",
        )
        return any(re.search(p, html) for p in patterns)

    for fn in required_handlers:
        if not has_handler(fn):
            return False
    return True


def validate_demo_html(html: str) -> dict:
    """
    Return a detailed validation report — useful for debugging.

    Returns: {"valid": bool, "checks": {"doctype": bool, "close_html": bool, ...}, "details": str}
    """
    checks = {
        "has_content": bool(html and len(html) > 500),
        "doctype": "<!DOCTYPE html" in (html or ""),
        "close_html": "</html>" in (html or "").lower(),
        "has_startDemo": False,
        "has_prevScene": False,
        "has_nextScene": False,
    }
    if html:
        for fn in ("startDemo", "prevScene", "nextScene"):
            key = f"has_{fn}"
            patterns = (
                rf"function\s+{fn}\s*\(",
                rf"(?:const|let|var)\s+{fn}\s*=",
                rf"window\.{fn}\s*=",
            )
            checks[key] = any(re.search(p, html) for p in patterns)

    valid = all(checks.values())
    failed = [k for k, v in checks.items() if not v]
    details = "all checks passed" if valid else f"failed: {', '.join(failed)}"

    return {"valid": valid, "checks": checks, "details": details}


def _strip_code_fences(html: str) -> str:
    """Strip markdown code fences if the model wrapped output."""
    cleaned = (html or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
    return cleaned.strip()


# ── Retry logic ──────────────────────────────────────────────────────────────

_TRANSIENT_KEYWORDS = (
    "overloaded", "rate_limit", "429", "temporar",
    "server_error", "500", "502", "503", "529",
)


def _is_transient(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(kw in text for kw in _TRANSIENT_KEYWORDS)


# Backoff delays: attempt 1 → 10s, attempt 2 → 30s, attempt 3 → 60s
_BACKOFF = [10, 30, 60]


def _extract_text(message) -> str:
    """Extract the text content from a Claude response, skipping thinking blocks."""
    for block in message.content:
        if block.type == "text":
            return _strip_code_fences(block.text)
    return ""


async def _call_model(
    client: anthropic.AsyncAnthropic,
    system: str,
    user_message: str,
    *,
    model: str,
    max_tokens: int,
    thinking_budget: int,
    timeout_seconds: int,
    label: str = "generation",
) -> str:
    """
    Call Claude with extended thinking + retry for transient errors.

    Extended thinking lets the model plan the HTML structure before writing,
    producing more complete output. It also unlocks higher max_tokens (64K
    for Sonnet 4 vs 16K without thinking).

    Returns:
        Raw text from the model (code fences already stripped).

    Raises:
        RuntimeError with detailed context on permanent failure.
    """
    max_attempts = len(_BACKOFF) + 1
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        t0 = time.monotonic()
        try:
            logger.info(
                "[demo_ai] %s attempt %d/%d — model=%s max_tokens=%d "
                "thinking_budget=%d timeout=%ds",
                label, attempt, max_attempts, model, max_tokens,
                thinking_budget, timeout_seconds,
            )

            async def _do_stream() -> str:
                async with client.messages.stream(
                    model=model,
                    max_tokens=max_tokens,
                    thinking={
                        "type": "enabled",
                        "budget_tokens": thinking_budget,
                    },
                    # Prompt caching: the system prompt (~3.3KB) is byte-stable
                    # across the initial attempt, the compact retry, the repair
                    # path, and all transient-error retries — only the user
                    # message varies. Cache it with an ephemeral breakpoint so
                    # repeated calls read the prefix instead of re-billing it.
                    # On Sonnet 4.6 the 2048-token minimum applies; this prompt
                    # is borderline, so caching is best-effort (no harm if the
                    # prefix is below the floor — it simply won't cache). The
                    # per-request `thinking`/`budget_tokens` are untouched.
                    system=[
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user_message}],
                ) as stream:
                    final = await stream.get_final_message()

                return _extract_text(final)

            html = await asyncio.wait_for(_do_stream(), timeout=timeout_seconds)
            elapsed = time.monotonic() - t0

            logger.info(
                "[demo_ai] %s attempt %d completed in %.1fs — output %d chars",
                label, attempt, elapsed, len(html),
            )
            return html

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            last_error = RuntimeError(
                f"Timed out after {elapsed:.0f}s (limit: {timeout_seconds}s)"
            )
            logger.warning("[demo_ai] %s attempt %d timed out after %.0fs", label, attempt, elapsed)

        except Exception as exc:
            elapsed = time.monotonic() - t0
            last_error = exc

            if not _is_transient(exc):
                logger.error(
                    "[demo_ai] %s attempt %d permanent error after %.1fs: %s",
                    label, attempt, elapsed, exc,
                )
                break  # Don't retry permanent errors

            logger.warning(
                "[demo_ai] %s attempt %d transient error after %.1fs: %s",
                label, attempt, elapsed, exc,
            )

        # Backoff before next retry
        if attempt < max_attempts:
            delay = _BACKOFF[attempt - 1]
            logger.info("[demo_ai] waiting %ds before retry...", delay)
            await asyncio.sleep(delay)

    raise RuntimeError(
        f"[demo_ai] {label} failed after {max_attempts} attempts. "
        f"Last error: {last_error}"
    )


# ── Public API ───────────────────────────────────────────────────────────────

def _build_user_message(
    production_guide: str,
    client_name: str,
    client_domain: str,
    brand_data: dict,
) -> str:
    primary_color = brand_data.get("primary_color", "#1a73e8")
    description = brand_data.get("description", "")
    logo_url = brand_data.get("logo_url", "")

    return f"""Generate a complete interactive HTML demo for this integration:

CLIENT: {client_name}
DOMAIN: {client_domain}
PRIMARY BRAND COLOR: {primary_color}
COMPANY DESCRIPTION: {description}
LOGO URL (use in <img> if provided, otherwise use text): {logo_url or "none — use text"}

---
PRODUCTION GUIDE / DEMO SCRIPT:
{production_guide}
---

Generate the complete self-contained HTML file now. No explanations — output only the HTML.
Start with <!DOCTYPE html> and end with </html>."""


async def generate_demo_html(
    production_guide: str,
    client_name: str,
    client_domain: str,
    brand_data: dict,
) -> str:
    """
    Generate a complete interactive HTML demo file using Claude.

    Uses Claude Sonnet 4 with extended thinking — the model plans the
    HTML/CSS/JS structure during the thinking phase, then writes the
    complete output. This produces more coherent demos and unlocks
    64K output tokens (vs 16K without thinking).

    Returns:
        Complete HTML string starting with <!DOCTYPE html>.

    Raises:
        RuntimeError with actionable error message on failure.
    """
    api_key = settings.claude_api_key
    if not api_key:
        raise RuntimeError(
            "Claude API key is not configured. "
            "Set ANTHROPIC_API_KEY or CLAUDE_API_KEY in your .env file."
        )

    model = settings.DEMO_MODEL
    max_tokens = settings.DEMO_MAX_TOKENS
    thinking_budget = settings.DEMO_THINKING_BUDGET
    timeout = settings.DEMO_TIMEOUT_SECONDS

    user_message = _build_user_message(
        production_guide, client_name, client_domain, brand_data,
    )

    # max_retries=0: _call_model already retries transient errors with its own
    # backoff/logging; the SDK's default max_retries=2 would multiply that into
    # up to 12 HTTP attempts of a 30K-max_tokens request. One retry layer total.
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)

    logger.info(
        "[demo_ai] starting generation — client=%s model=%s "
        "max_tokens=%d thinking_budget=%d guide_chars=%d",
        client_name, model, max_tokens, thinking_budget, len(production_guide),
    )

    # ── Attempt 1: full generation ──
    html = await _call_model(
        client, SYSTEM_PROMPT, user_message,
        model=model, max_tokens=max_tokens,
        thinking_budget=thinking_budget,
        timeout_seconds=timeout,
        label="initial",
    )

    report = validate_demo_html(html)
    if report["valid"]:
        logger.info("[demo_ai] initial generation VALID — %d chars", len(html))
        return html

    logger.warning(
        "[demo_ai] initial generation INVALID — %s — trying compact retry",
        report["details"],
    )

    # ── Attempt 2: compact retry with explicit structural reminders ──
    compact_message = (
        user_message
        + "\n\n"
        + "IMPORTANT — YOUR PREVIOUS OUTPUT WAS INCOMPLETE OR INVALID.\n"
        + "RETRY RULES:\n"
        + "- Keep to 5-6 scenes max to stay within output limits.\n"
        + "- You MUST define global JS functions: startDemo, prevScene, nextScene, playScene, skipScene.\n"
        + "- You MUST start with <!DOCTYPE html> and end with </html>.\n"
        + "- Output ONLY raw HTML — no markdown fences, no commentary.\n"
        + "- Make sure ALL script tags and functions are complete — do not truncate.\n"
    )

    html_retry = await _call_model(
        client, SYSTEM_PROMPT, compact_message,
        model=model, max_tokens=max_tokens,
        thinking_budget=thinking_budget,
        timeout_seconds=timeout,
        label="compact-retry",
    )

    report2 = validate_demo_html(html_retry)
    if report2["valid"]:
        logger.info("[demo_ai] compact retry VALID — %d chars", len(html_retry))
        return html_retry

    # Both attempts failed validation
    raise RuntimeError(
        f"Generated HTML failed validation after 2 generation attempts.\n"
        f"Attempt 1: {report['details']} ({len(html)} chars)\n"
        f"Attempt 2: {report2['details']} ({len(html_retry)} chars)\n"
        f"Try simplifying the production guide or reducing scene count."
    )


async def repair_demo_html(existing_html: str, client_name: str = "Client") -> str:
    """Repair previously generated incomplete HTML."""
    if is_valid_demo_html(existing_html):
        return existing_html

    api_key = settings.claude_api_key
    if not api_key:
        raise RuntimeError("Claude API key is not configured.")

    # max_retries=0 — _call_model owns retries (see generate_demo_html).
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)

    repair_prompt = f"""The following HTML demo is broken or truncated.
Fix it into a complete, valid, self-contained interactive HTML document.

Requirements:
- Output ONLY HTML starting with <!DOCTYPE html> and ending with </html>.
- Keep the same visual intent and scene narrative where possible.
- Ensure global functions exist: startDemo, prevScene, nextScene, playScene, skipScene.
- Keep inline onclick handlers working.
- No external JS/CSS dependencies.
- Limit to 5 scenes if the original was longer.

Client: {client_name}

BROKEN HTML INPUT:
{existing_html[:100000]}
"""

    repaired = await _call_model(
        client, SYSTEM_PROMPT, repair_prompt,
        model=settings.DEMO_MODEL,
        max_tokens=settings.DEMO_MAX_TOKENS,
        thinking_budget=settings.DEMO_THINKING_BUDGET,
        timeout_seconds=180,
        label="repair",
    )

    if not is_valid_demo_html(repaired):
        report = validate_demo_html(repaired)
        raise RuntimeError(
            f"HTML repair failed validation: {report['details']}. "
            "Please regenerate the demo."
        )
    return repaired
