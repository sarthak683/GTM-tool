"""
Claude AI enrichment client — summarization + persona classification.

Uses single-turn Claude calls for:
  1. Company intelligence reports from collected data
  2. Contact persona classification (champion/buyer/evaluator/blocker)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

def _get_client():
    """Lazy-init Anthropic client."""
    import anthropic
    return anthropic.AsyncAnthropic(api_key=settings.claude_api_key)


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction for model responses with optional prose/fences."""
    text = (raw_text or "").strip()
    if not text:
        return None

    # Fast path: exact JSON payload.
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    # Strip Markdown code fences when present.
    if "```" in text:
        lines = text.splitlines()
        fence_stripped: list[str] = []
        inside_fence = False
        for line in lines:
            if line.strip().startswith("```"):
                inside_fence = not inside_fence
                continue
            if inside_fence:
                fence_stripped.append(line)
        fenced_text = "\n".join(fence_stripped).strip()
        if fenced_text:
            try:
                parsed = json.loads(fenced_text)
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                pass

    # Last resort: scan for the first decodable JSON object in mixed content.
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[idx:])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    return None


async def summarize_company(
    scraped_data: dict,
    apollo_data: dict | None,
    search_results: dict,
    company_name: str,
    domain: str,
    upload_context: dict[str, Any] | None = None,
) -> dict | None:
    """
    Single-turn Claude call to synthesize all collected data into a comprehensive
    sales intelligence report for a company.
    """
    if not settings.claude_api_key:
        logger.warning("Claude API key not configured — skipping summarization for %s", company_name)
        return None

    client = _get_client()

    system = (
        "You are a B2B sales intelligence analyst for Beacon.li, an AI implementation "
        "orchestration platform that automates enterprise SaaS deployments. "
        "Analyze ALL provided data about a company and produce a COMPREHENSIVE assessment.\n\n"
        "Beacon's ICP (Ideal Customer Profile):\n"
        "- Companies with 200-5000 employees\n"
        "- Series B+ funding\n"
        "- SaaS/enterprise software, HR Tech, FinTech, HealthTech, EdTech\n"
        "- Companies deploying or implementing complex SaaS tools\n"
        "- Presence of digital adoption platforms (WalkMe, Pendo, etc.) is a strong signal\n\n"
        "Respond ONLY in valid JSON with these exact keys:\n"
        "{\n"
        '  "description": "3-4 sentence company overview — what they do, who they serve, what makes them notable",\n'
        '  "industry": "primary industry classification",\n'
        '  "icp_fit_reasoning": "2-3 sentence analysis of why this company is or is not a fit for Beacon",\n'
        '  "icp_fit_score": 0-100,\n'
        '  "key_products": ["list of main products or platform features"],\n'
        '  "target_customers": "who their customers are — segment, size, verticals",\n'
        '  "value_proposition": "their core value prop in one sentence",\n'
        '  "competitive_landscape": ["list of known competitors"],\n'
        '  "tech_stack_signals": ["detected technologies, frameworks, platforms"],\n'
        '  "recent_news": ["notable recent events — funding, launches, partnerships, acquisitions"],\n'
        '  "hiring_signals": ["active hiring areas that indicate growth or investment"],\n'
        '  "pain_points": ["likely pain points relevant to Beacon — SaaS deployment, onboarding, adoption challenges"],\n'
        '  "recommended_approach": "how a sales rep should approach this company — angle, messaging, entry point",\n'
        '  "talking_points": ["3-4 personalized talking points for outreach"],\n'
        '  "intent_signals_summary": "summary of detected buying intent signals",\n'
        '  "confidence": 0-100\n'
        "}\n\n"
        "Be specific and actionable. Avoid generic statements. Use the actual data provided."
    )

    user_data = f"Company: {company_name}\nDomain: {domain}\n\n"
    if upload_context:
        user_data += f"Imported Analyst Context:\n{json.dumps(upload_context, default=str)}\n\n"
    if apollo_data:
        user_data += f"Apollo Firmographic Data:\n{json.dumps(apollo_data, default=str)}\n\n"
    if scraped_data.get("text"):
        user_data += f"Website Content (scraped):\n{scraped_data['text'][:6000]}\n\n"
    if search_results.get("raw_results"):
        user_data += f"Web Search Results:\n{json.dumps(search_results['raw_results'][:8], default=str)}\n\n"
    # Include structured intent signal data
    for signal_type in ("hiring", "funding", "product", "tech"):
        items = search_results.get(signal_type, [])
        if items:
            user_data += f"\n{signal_type.title()} Signals:\n"
            for item in items[:5]:
                if isinstance(item, dict):
                    user_data += f"  - {item.get('title', '')}: {item.get('snippet', '')}\n"
                else:
                    user_data += f"  - {item}\n"

    try:
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2000,
            # Cache the static system prompt: account-sourcing processes companies
            # in batches, so the identical prefix is reused within the 5-min cache
            # window. Below the model's cache threshold it's a silent no-op.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_data}],
        )
        text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        text = "\n".join(text_blocks).strip() if text_blocks else ""
        payload = _extract_json_object(text)
        if payload is None:
            logger.warning(f"Claude summarization returned non-JSON payload for {company_name}")
            return None
        payload.setdefault("_source", "claude")
        return payload
    except Exception as e:
        logger.error(f"Claude summarization failed for {company_name}: {e}")
        return None


async def classify_contact_persona(
    title: str, seniority: str | None, company_context: str | None
) -> str:
    """
    Use Claude to classify a contact into: champion | buyer | evaluator | blocker.
    Falls back to rule-based if API unavailable.
    """
    if not settings.claude_api_key:
        return _rule_based_persona(title, seniority)

    client = _get_client()

    try:
        response = await client.messages.create(
            # One-word persona classification — Haiku is ~20x cheaper than Sonnet
            # and accurate enough here; _rule_based_persona backstops any miss.
            model=settings.CLAUDE_MODEL_SIMPLE,
            max_tokens=50,
            system=(
                "Classify this B2B contact into exactly one role for a SaaS implementation sale. "
                "Respond with ONLY one word: champion, buyer, evaluator, or blocker.\n"
                "- champion: HR/People leaders who own the problem Beacon solves\n"
                "- buyer: C-suite/VPs who control budget (CEO, CFO, COO)\n"
                "- evaluator: Technical leaders who assess feasibility (CTO, VP Eng)\n"
                "- blocker: Procurement, legal, or incumbents who may resist"
            ),
            messages=[{"role": "user", "content": f"Title: {title}\nSeniority: {seniority or 'unknown'}\nCompany context: {company_context or 'unknown'}"}],
        )
        result = response.content[0].text.strip().lower()
        if result in ("champion", "buyer", "evaluator", "blocker"):
            return result
        return _rule_based_persona(title, seniority)
    except Exception:
        return _rule_based_persona(title, seniority)



def _rule_based_persona(title: str, seniority: str | None) -> str:
    """Quick rule-based persona classification fallback."""
    title_lower = (title or "").lower()
    if any(k in title_lower for k in ["ceo", "cfo", "coo", "president", "founder", "vp finance"]):
        return "buyer"
    if any(k in title_lower for k in ["chro", "vp hr", "head of people", "hr director", "people"]):
        return "champion"
    if any(k in title_lower for k in ["cto", "vp eng", "architect", "it director", "technical"]):
        return "evaluator"
    if any(k in title_lower for k in ["procurement", "legal", "compliance", "vendor"]):
        return "blocker"
    return "evaluator"  # default for unknown
