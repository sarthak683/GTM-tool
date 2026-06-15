from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal


SignalPolarity = Literal["none", "negated", "present"]


@dataclass(frozen=True)
class ActivitySignal:
    normalized_text: str
    stage_cue: str | None = None
    blocker: SignalPolarity = "none"
    pricing_request: bool = False
    pricing_pushback: bool = False
    legal_review: bool = False
    workshop: bool = False
    reschedule: bool = False


def _normalize(text: str | None) -> str:
    return " ".join((text or "").strip().lower().split())


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _contains_all(text: str, phrases: Iterable[str]) -> bool:
    return all(phrase in text for phrase in phrases)


NEGATED_ISSUE_PHRASES = (
    "no issue",
    "no issues",
    "no problem",
    "no problems",
    "no blocker",
    "no blockers",
    "no bug",
    "no bugs",
    "no error",
    "no errors",
    "nothing blocking",
    "all good",
    "working fine",
    "works fine",
    "no issues on my end",
)

BLOCKER_PHRASES = (
    "blocker",
    "blocked",
    "bug",
    "error",
    "not working",
    "isn't working",
    "isnt working",
    "broken",
    "failing",
    "failure",
    "unable to",
    "cannot access",
    "can't access",
    "having issues",
    "have issues",
    "ran into an issue",
    "running into issues",
    "hit an issue",
    "hitting issues",
    "seeing an issue",
    "seeing issues",
    "facing an issue",
    "facing issues",
    "issue with",
    "issues with",
    "problem with",
    "problems with",
)

POC_PHRASES = ("poc", "proof of concept", "pilot")
POC_AGREEMENT_PHRASES = (
    "agreed to poc",
    "agree to poc",
    "let's do a poc",
    "lets do a poc",
    "move to poc",
    "happy to start poc",
    "interested in poc",
    "keen on poc",
    "proceed with poc",
    "green light for the poc",
    "approved the poc",
)
POC_PREP_PHRASES = (
    "hasn't started",
    "hasnt started",
    "not started",
    "poc video",
    "client requirement",
    "sample requirement",
    "requirements sample",
    "prepare on their end",
    "prepare on your end",
    "for them to prepare",
    "for you to prepare",
    "what data do you need",
    "data checklist",
    "setup checklist",
    "technical requirements",
    "kickoff the poc",
    "poc kickoff",
    "poc setup",
    "before the poc starts",
)
POC_WIP_PHRASES = (
    "poc update",
    "poc progress",
    "poc status",
    "how is the poc",
    "update on the poc",
    "poc is underway",
    "pilot is underway",
    "running the poc",
    "during the poc",
    "mid-poc",
    "midpoint review",
    "checkpoint",
)
POC_DONE_PHRASES = (
    "poc completed",
    "poc complete",
    "completed the poc",
    "finished the poc",
    "wrapped up the poc",
    "poc is complete",
    "poc was completed",
    "pilot complete",
    "pilot completed",
    "finished the pilot",
    "wrapped up the pilot",
    "successful poc",
    "poc was successful",
    "pilot was successful",
    "success criteria met",
    "criteria met in the poc",
)

COMMERCIAL_PHRASES = (
    "pricing",
    "proposal",
    "quote",
    "commercial terms",
    "commercials",
    "budget review",
    "negotiat",
)
PRICING_REQUEST_PHRASES = (
    "send pricing",
    "share pricing",
    "what does it cost",
    "pricing details",
    "can you send a quote",
    "send a proposal",
    "review the proposal",
    "share the quote",
)
PRICING_PUSHBACK_PHRASES = (
    "too expensive",
    "budget is tight",
    "budget constraint",
    "price is high",
    "discount",
    "cost concern",
)

LEGAL_REVIEW_PHRASES = (
    "security review",
    "security questionnaire",
    "procurement",
    "legal review",
    "msa",
    "master services agreement",
    "redline",
    "vendor onboarding",
    "vendor form",
    "company info",
    "w-9",
    "w9",
    "insurance certificate",
)

WORKSHOP_PHRASES = (
    "workshop",
    "working session",
    "technical deep dive",
    "implementation session",
    "discovery workshop",
)
WORKSHOP_ACTION_PHRASES = ("schedule", "scheduled", "book", "set up", "calendar invite", "working session")

CLOSED_WON_PHRASES = (
    "going with you",
    "selected beacon",
    "chosen beacon",
    "moving forward with beacon",
)
NOT_FIT_PHRASES = (
    "not interested",
    "not a fit",
    "no longer pursuing",
    "decided against",
    "going with another vendor",
    "not moving forward",
)


def classify_activity_text(text: str | None) -> ActivitySignal:
    normalized = _normalize(text)
    if not normalized:
        return ActivitySignal(normalized_text="")

    blocker: SignalPolarity = "none"
    if _contains_any(normalized, NEGATED_ISSUE_PHRASES):
        blocker = "negated"
    elif _contains_any(normalized, BLOCKER_PHRASES):
        blocker = "present"

    mentions_poc = _contains_any(normalized, POC_PHRASES)
    poc_prep = mentions_poc and _contains_any(normalized, POC_PREP_PHRASES)
    poc_done = mentions_poc and _contains_any(normalized, POC_DONE_PHRASES) and not poc_prep
    poc_wip = mentions_poc and _contains_any(normalized, POC_WIP_PHRASES) and not poc_done and not poc_prep
    poc_agreed = _contains_any(normalized, POC_AGREEMENT_PHRASES) or (
        mentions_poc and _contains_any(normalized, ("agree", "agreed", "approved", "move forward", "green light", "aligned"))
    )

    if _contains_any(normalized, CLOSED_WON_PHRASES):
        stage_cue = "closed_won"
    elif _contains_any(normalized, NOT_FIT_PHRASES):
        stage_cue = "not_a_fit"
    elif poc_done:
        stage_cue = "poc_done"
    elif _contains_any(normalized, LEGAL_REVIEW_PHRASES):
        stage_cue = "msa_review"
    elif _contains_any(normalized, COMMERCIAL_PHRASES):
        stage_cue = "commercial_negotiation"
    elif poc_wip:
        stage_cue = "poc_wip"
    elif poc_agreed and not poc_prep:
        stage_cue = "poc_agreed"
    elif _contains_any(normalized, WORKSHOP_PHRASES) and _contains_any(normalized, WORKSHOP_ACTION_PHRASES):
        stage_cue = "workshop"
    else:
        stage_cue = None

    pricing_request = _contains_any(normalized, PRICING_REQUEST_PHRASES) or (
        _contains_any(normalized, ("pricing", "proposal", "quote")) and _contains_any(normalized, ("send", "share", "review", "please"))
    )

    return ActivitySignal(
        normalized_text=normalized,
        stage_cue=stage_cue,
        blocker=blocker,
        pricing_request=pricing_request,
        pricing_pushback=_contains_any(normalized, PRICING_PUSHBACK_PHRASES),
        legal_review=_contains_any(normalized, LEGAL_REVIEW_PHRASES),
        workshop=_contains_any(normalized, WORKSHOP_PHRASES),
        reschedule="reschedul" in normalized or _contains_any(normalized, ("move the demo", "push the demo", "can we move", "new time")),
    )


def signal_to_intent_key(signal: ActivitySignal) -> str | None:
    if signal.stage_cue:
        return f"move_deal_stage:{signal.stage_cue}"
    if signal.pricing_request:
        return "send_pricing_package"
    if signal.workshop:
        return "book_workshop_session"
    return None


def detect_latest_intent_from_segments(segments: list[str]) -> str | None:
    for segment in reversed(segments):
        intent = signal_to_intent_key(classify_activity_text(segment))
        if intent:
            return intent
    combined = "\n\n".join(segments[-4:])
    return signal_to_intent_key(classify_activity_text(combined))
