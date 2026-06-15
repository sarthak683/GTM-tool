"""
AI disposition classifier for recorded calls.

Takes a Whisper transcript of a manual phone call (rep on speakerphone,
laptop mic captured both sides), classifies the disposition against the
existing CRM enum, and returns a short summary the rep can scan.

Mirrors the structure of `app/services/reply_sentiment.py` — same JSON
output shape, same Claude provider, same fault-tolerant degrade-to-None
behavior. If classification fails, the caller falls back to the manual
disposition dropdown the rep already uses today.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app.clients.claude import ClaudeClient

logger = logging.getLogger(__name__)


# Must stay in sync with frontend/src/lib/prospectWorkflow.ts's
# CALL_DISPOSITION_OPTIONS. The classifier will only return values from
# this set; anything else is coerced to None so the UI can prompt the
# rep to choose manually.
ALLOWED_DISPOSITIONS = {
    "demo_scheduled_booked",
    "interested_follow_up_required",
    "meeting_confirmed",
    "call_back_later_rescheduled",
    "gatekeeper_connected_to_admin",
    "connected_not_interested",
    "no_answer_busy_signal",
    "invalid_number_wrong_number",
    "do_not_contact_dnc",
    "contact_poor_fit",
    "redirected_other_icp",
}


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def classify_call_transcript(
    *,
    transcript: str,
    contact_name: Optional[str] = None,
    contact_title: Optional[str] = None,
    company_name: Optional[str] = None,
) -> dict[str, Any] | None:
    """Return {disposition, confidence, summary} or None if unavailable.

    confidence is a 0.0-1.0 self-reported value from the model. Treat it
    as a hint — the UI surfaces it next to the AI suggestion so the rep
    knows when to second-guess.
    """
    if not transcript or len(transcript.strip()) < 20:
        return None

    ai = ClaudeClient()
    if ai.mock:
        return None

    enum_csv = ", ".join(sorted(ALLOWED_DISPOSITIONS))
    system = (
        "You classify B2B outbound sales call transcripts. The transcript "
        "is a single mixed-audio recording of a phone call where the sales "
        "rep had the prospect on speakerphone, so both sides of the "
        "conversation are present without speaker labels — infer who is "
        "speaking from context.\n\n"
        "Output ONLY a JSON object — no prose, no code fences. Schema:\n"
        "{\n"
        "  \"disposition\": \"<one of: " + enum_csv + ">\",\n"
        "  \"confidence\": <float 0.0-1.0>,\n"
        "  \"summary\": \"<~20 word summary of what happened on the call>\"\n"
        "}\n\n"
        "Guidance:\n"
        "- \"demo_scheduled_booked\" / \"meeting_confirmed\" require explicit "
        "  agreement on a time, not just expressed interest.\n"
        "- \"interested_follow_up_required\" = prospect was warm but didn't "
        "  commit to a calendar slot.\n"
        "- \"call_back_later_rescheduled\" = prospect asked to be called back "
        "  later; assume the rep will note the time separately.\n"
        "- \"connected_not_interested\" = spoke to the right person, soft no.\n"
        "- \"do_not_contact_dnc\" = explicit request to stop contacting, or "
        "  hostile/legal-threat language.\n"
        "- \"no_answer_busy_signal\" = transcript is silent / one-sided "
        "  voicemail attempt where nobody picked up.\n"
        "- \"gatekeeper_connected_to_admin\" = spoke to an assistant/receptionist, "
        "  not the named prospect.\n"
        "- Lower confidence (<0.6) when the transcript is short, garbled, or "
        "  ambiguous so the rep knows to double-check."
    )

    user_lines: list[str] = []
    if contact_name:
        user_lines.append(f"Prospect: {contact_name}")
    if contact_title:
        user_lines.append(f"Title: {contact_title}")
    if company_name:
        user_lines.append(f"Company: {company_name}")
    if user_lines:
        user_lines.append("")
    user_lines.append("Transcript:")
    # Cap the transcript so the model focuses on the call, not the
    # repeated boilerplate of a very long voicemail loop.
    user_lines.append(transcript.strip()[:8000])

    text = await ai.complete(system=system, user="\n".join(user_lines), max_tokens=220)
    if not text:
        return None

    data = _extract_json(text)
    if not isinstance(data, dict):
        logger.warning("call_disposition_ai: could not parse Claude response: %s", text[:200])
        return None

    disposition = str(data.get("disposition") or "").strip().lower()
    confidence_raw = data.get("confidence")
    summary = str(data.get("summary") or "").strip()

    if disposition not in ALLOWED_DISPOSITIONS:
        # Don't return a coerced default — better to let the UI prompt
        # the rep to pick manually than to suggest the wrong thing.
        logger.info("call_disposition_ai: model returned out-of-enum value %r", disposition)
        return None

    try:
        confidence = float(confidence_raw) if confidence_raw is not None else 0.5
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    return {
        "disposition": disposition,
        "confidence": confidence,
        "summary": summary[:280],
    }
