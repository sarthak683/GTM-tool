"""
Reply sentiment + intent classification for inbound Instantly emails.

A single Claude call turns a raw prospect reply into a structured verdict:
  - sentiment:  positive | neutral | negative
  - intent:     interested | asking_question | objection | not_interested |
                unsubscribe | out_of_office | wrong_person | other
  - one_line:   a ~12-word human summary the rep can read at a glance
  - suggested_response: optional next-step hint

Output is persisted onto the Activity row so the rep sees the tag in the
timeline and "hot reply" filters become possible. If the Claude call fails,
we just skip the enrichment — the reply is still logged in its raw form.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app.clients.claude import ClaudeClient

logger = logging.getLogger(__name__)


_SENTIMENTS = {"positive", "neutral", "negative"}
_INTENTS = {
    "interested",
    "asking_question",
    "objection",
    "not_interested",
    "unsubscribe",
    "out_of_office",
    "wrong_person",
    "other",
}


def _extract_json(text: str) -> dict[str, Any] | None:
    """Claude sometimes wraps JSON in ``` fences or preamble. Extract the
    first balanced {...} block."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def classify_reply(
    *,
    subject: str,
    body: str,
    sender: str,
    prospect_title: Optional[str] = None,
    company_name: Optional[str] = None,
) -> dict[str, Any] | None:
    """Return a sentiment/intent dict or None if classification unavailable."""
    if not body or len(body.strip()) < 5:
        return None

    ai = ClaudeClient()
    if ai.mock:
        return None

    system = (
        "You classify short B2B sales email replies. Respond with ONLY a JSON "
        "object — no prose, no code fences. "
        "Schema: {\"sentiment\": \"positive|neutral|negative\", "
        "\"intent\": \"interested|asking_question|objection|not_interested|"
        "unsubscribe|out_of_office|wrong_person|other\", "
        "\"one_line\": \"<~12 word summary>\", "
        "\"suggested_response\": \"<one short sentence or empty>\"}."
    )
    user_lines = [
        f"Subject: {subject[:200]}",
        f"From: {sender}",
    ]
    if prospect_title:
        user_lines.append(f"Prospect title: {prospect_title}")
    if company_name:
        user_lines.append(f"Company: {company_name}")
    user_lines.append("")
    user_lines.append("Reply body:")
    # Trim extremely long threads so we classify the new content, not the
    # quoted history below it. A simple truncation + signature strip gives
    # the model the signal it needs without wasting tokens.
    truncated = body.strip()[:2400]
    user_lines.append(truncated)

    text = await ai.complete(system=system, user="\n".join(user_lines), max_tokens=180)
    if not text:
        return None

    data = _extract_json(text)
    if not isinstance(data, dict):
        logger.warning("reply_sentiment: could not parse Claude response: %s", text[:200])
        return None

    sentiment = str(data.get("sentiment") or "").strip().lower()
    intent = str(data.get("intent") or "").strip().lower()
    one_line = str(data.get("one_line") or "").strip()
    suggested = str(data.get("suggested_response") or "").strip()

    if sentiment not in _SENTIMENTS:
        sentiment = "neutral"
    if intent not in _INTENTS:
        intent = "other"

    return {
        "sentiment": sentiment,
        "intent": intent,
        "one_line": one_line[:200],
        "suggested_response": suggested[:300],
    }
