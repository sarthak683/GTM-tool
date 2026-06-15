"""
Claude client wrapper with adaptive model routing.

Single-responsibility wrapper around Anthropic's async SDK. Picks a model
tier (simple / standard / complex) based on the prompt shape so cheap tasks
(persona classification, domain resolution) don't burn the big model and
heavy tasks (account briefs, meeting intelligence) get it.

Mock mode: returns None when no Claude API key is configured — services
must handle None and skip cleanly rather than crashing.
"""
import asyncio
import logging
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)


class ClaudeClient:
    def __init__(self) -> None:
        self.api_key = settings.claude_api_key
        self.mock = not self.api_key

    def _get_client(self):
        import anthropic

        return anthropic.AsyncAnthropic(api_key=self.api_key)

    def _pick_model(self, system: str, user: str, max_tokens: int) -> str:
        text = f"{system}\n{user}".lower()
        text_len = len(text)

        complex_markers = [
            "executive",
            "comprehensive",
            "competitive",
            "strategy",
            "deep",
            "analysis",
            "battlecard",
            "briefing",
            "demo strategy",
            "meeting intelligence",
        ]
        simple_markers = [
            "classify",
            "one word",
            "exactly one",
            "return the domain",
            "short",
            "concise",
        ]

        if max_tokens >= 650 or text_len > 7000 or any(marker in text for marker in complex_markers):
            return settings.CLAUDE_MODEL_COMPLEX
        if max_tokens <= 120 and text_len < 1500 and any(marker in text for marker in simple_markers):
            return settings.CLAUDE_MODEL_SIMPLE
        return settings.CLAUDE_MODEL_STANDARD

    async def complete(self, system: str, user: str, max_tokens: int = 500) -> Optional[str]:
        """Single-turn completion. Returns None in mock mode."""
        if self.mock:
            return None

        try:
            model = self._pick_model(system=system, user=user, max_tokens=max_tokens)
            client = self._get_client()
            response = await asyncio.wait_for(
                client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=0.2,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                ),
                timeout=40,
            )
            text_blocks = [block.text for block in response.content if getattr(block, "type", None) == "text"]
            content = "\n".join(text_blocks).strip() if text_blocks else ""
            return content or None
        except Exception as e:
            logger.error(f"Claude call failed: {e}")
            return None

    async def complete_structured(
        self,
        *,
        system: str,
        user: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        max_tokens: int = 800,
    ) -> Optional[dict[str, Any]]:
        """Single-turn structured extraction via Claude tool use."""
        if self.mock:
            return None

        try:
            model = self._pick_model(system=system, user=user, max_tokens=max_tokens)
            client = self._get_client()
            response = await asyncio.wait_for(
                client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=0,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    tools=[
                        {
                            "name": tool_name,
                            "description": tool_description,
                            "input_schema": input_schema,
                        }
                    ],
                    tool_choice={"type": "tool", "name": tool_name},
                ),
                timeout=40,
            )
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                    payload = getattr(block, "input", None)
                    if isinstance(payload, dict):
                        return payload
            return None
        except Exception as e:
            logger.error(f"Claude structured call failed: {e}")
            return None

    async def classify_persona(self, title: str, company_context: str = "") -> Optional[str]:
        """AI-powered persona classification when title keywords are ambiguous."""
        if self.mock:
            return None

        system = (
            "You are a B2B sales expert. Classify the job title into exactly one of: "
            "economic_buyer, champion, technical_evaluator, unknown. "
            "Respond with only the classification word, nothing else."
        )
        user = f"Job title: {title}\nCompany context: {company_context}"
        return await self.complete(system, user, max_tokens=10)

    async def summarise_account(self, company_name: str, news_signals: list, tech_stack: dict) -> Optional[str]:
        """Generate a pre-meeting account intelligence summary."""
        if self.mock:
            return None

        news_text = "\n".join(f"- {a['title']}" for a in news_signals[:5]) or "No recent news."
        tech_text = ", ".join(f"{k}: {v}" for k, v in (tech_stack or {}).items()) or "Unknown"

        system = "You are a sales intelligence analyst. Write a concise 3-bullet account brief for a sales rep going into a first meeting."
        user = (
            f"Company: {company_name}\n"
            f"Recent news:\n{news_text}\n"
            f"Tech stack: {tech_text}\n"
            "Write 3 bullet points covering: key business context, buying signals, and suggested conversation angle."
        )
        return await self.complete(system, user, max_tokens=300)

    async def resolve_domain(
        self,
        company_name: str,
        industry: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[str]:
        """Infer the most likely website domain for a company."""
        if self.mock:
            return None

        context_parts = [f"Company name: {company_name}"]
        if industry:
            context_parts.append(f"Industry: {industry}")
        if description:
            context_parts.append(f"Description: {description[:300]}")

        system = (
            "You are a B2B sales researcher. Given a company name and optional metadata, "
            "return the most likely primary website domain (e.g. 'acmecorp.com'). "
            "Rules: respond with ONLY the bare domain — no https://, no www, no path, no punctuation. "
            "If you are not confident (>80%) in the answer, respond with exactly: null"
        )
        user = "\n".join(context_parts)
        result = await self.complete(system, user, max_tokens=30)

        if not result or result.strip().lower() in ("null", "none", "unknown", ""):
            return None

        domain = result.strip().lower()
        for prefix in ("https://", "http://", "www."):
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
        domain = domain.split("/")[0].strip()

        if "." not in domain or len(domain) > 100:
            return None

        return domain

    async def draft_followup_email(self, contact_name: str, company_name: str, meeting_summary: str) -> Optional[str]:
        """Draft a post-meeting follow-up email."""
        if self.mock:
            return None

        system = "You are a senior enterprise sales rep. Write professional, concise follow-up emails."
        user = (
            f"Write a follow-up email to {contact_name} at {company_name}.\n"
            f"Meeting summary: {meeting_summary}\n"
            "Include: thank you, key takeaways (2-3 bullets), agreed next steps, and a soft CTA."
        )
        return await self.complete(system, user, max_tokens=400)
