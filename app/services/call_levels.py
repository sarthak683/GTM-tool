"""L1 / L2 / L3 call classification (Sales Lifecycle SOP, stage 04).

The SOP has the AE classify each upcoming client call at the prep call, from the
calendar invite's attendee list, and that classification sets how the call is
run:

    L1  Solo Director/VP of Implementation, Delivery or PS.
        Deep discovery, high-level demo only, output glimpses only.
        Next: book a Demo Deep Dive with a larger audience.
    L2  Larger group (2+ attendees).
        Moderate discovery + deep platform demo, output only.
        Next: book a technical deep dive with an exec looped in.
    L3  SVP+ executives.
        Light/embedded discovery, brand vision first, deep demo, output only.
        Next: book an L1-style discovery + demo, then loop in Operations.

Two rules, not one ladder: L2 is defined by *group size* and L3 by *seniority*.
A lone SVP is L3, not L1 — so seniority is evaluated first and wins regardless
of how many people are on the invite.

On honesty about what we can actually see: in production only 3.4% of external
attendees (139 of 4,125) carry a title, while attendee counts are complete. So
the group-size rule is nearly always decidable and the seniority rule almost
never is. A classifier that answered a confident "L2" from a headcount, when an
unseen SVP would have made it L3, would be wrong in exactly the direction that
costs a deal — the AE walks into an executive audience prepared for a working
session. Every suggestion therefore carries a confidence and a rationale, and
`low` confidence means "the count says this, but titles are unknown, so confirm
at the prep call". The classification is a *suggestion*; the AE's manual value
always wins and is never overwritten by a re-sync.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import func

CALL_LEVELS: tuple[str, ...] = ("L1", "L2", "L3")

# ── Seniority matching ───────────────────────────────────────────────────────
# Word-boundary regexes, not substring checks, because the real titles in this
# database break naive matching in both directions:
#
#   "AVP, Global Services"            must NOT read as VP  (\bvp\b won't match
#                                     inside "avp" — no word boundary there)
#   "Senior Vice President/ Delivery Director"  must read as SVP, not Director
#   "Chief Delivery Officer Biometrics Services"  must read as C-level
#   "VP, Implementation"              must read as VP, NOT as "president"
#
# That last one is why "vice president" is collapsed to a single token before
# the standalone `president` test runs — otherwise every VP would match
# `president` and be promoted to L3.
_SVP_PLUS = re.compile(
    r"\b("
    r"svp|evp|cvp"
    r"|senior\s+vice\s?president|executive\s+vice\s?president"
    r"|sr\.?\s+vice\s?president"
    r"|president"                       # standalone only — see _normalize_title
    r"|chief"                           # Chief X Officer, incl. Chief of Staff
    r"|ceo|cto|coo|cfo|cio|cpo|cro|cmo|cdo|ciso|cxo"
    r"|founder|co-?founder|owner"
    r"|managing\s+partner"
    r")\b",
    re.IGNORECASE,
)

# Director/VP band — the SOP's L1 audience. Deliberately includes AVP and
# "associate/assistant vice president", which are NOT SVP+.
_DIRECTOR_BAND = re.compile(
    r"\b("
    r"vp|avp|vice\s?president|associate\s+vice\s?president|assistant\s+vice\s?president"
    r"|director|head\s+of"
    r")\b",
    re.IGNORECASE,
)


def _normalize_title(title: Optional[str]) -> str:
    """Lowercase, collapse whitespace, and fuse "vice president" into one token.

    The fusing matters: `_SVP_PLUS` tests for a standalone `president`, and
    without this every "VP, Implementation" and "Vice President, Delivery"
    would match it and be misread as an executive.
    """
    text = re.sub(r"\s+", " ", str(title or "")).strip().lower()
    if not text:
        return ""
    # Order matters — the longer senior forms must survive, so fuse only the
    # bare "vice president", leaving "senior vice president" intact for the
    # SVP pattern by re-expanding it afterwards.
    text = re.sub(r"\bvice[\s-]?president\b", "viceQpresident", text)
    text = re.sub(r"\b(senior|sr\.?|executive|exec\.?)\s+viceQpresident\b", r"\1 vice president", text)
    return text


def is_svp_or_above(title: Optional[str]) -> bool:
    """True for SVP, EVP, President, C-level, Founder — the SOP's L3 trigger."""
    return bool(_SVP_PLUS.search(_normalize_title(title)))


def is_director_band(title: Optional[str]) -> bool:
    """True for Director / VP / AVP / Head of — the SOP's L1 audience."""
    normalized = _normalize_title(title)
    if not normalized:
        return False
    if _SVP_PLUS.search(normalized):
        return False
    return bool(_DIRECTOR_BAND.search(normalized.replace("viceQpresident", "vice president")))


# ── Classification ───────────────────────────────────────────────────────────


@dataclass
class CallLevelSuggestion:
    """What the attendee list implies, and how much of it we could actually see."""

    level: Optional[str]              # "L1" | "L2" | "L3" | None (not a client call)
    confidence: str                   # "high" | "low"
    rationale: str
    external_count: int = 0
    titles_known: int = 0
    senior_attendees: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "external_count": self.external_count,
            "titles_known": self.titles_known,
            "senior_attendees": self.senior_attendees,
        }


def _attendee_title(attendee: dict[str, Any]) -> str:
    """Title from the attendee blob. Sync writes `title` inline when it knows
    one and leaves it null otherwise; a matched contact's title is copied in by
    the caller before classification, so only this one key is read here."""
    return str(attendee.get("title") or "").strip()


def _attendee_label(attendee: dict[str, Any]) -> str:
    name = str(attendee.get("name") or "").strip()
    title = _attendee_title(attendee)
    email = str(attendee.get("email") or "").strip()
    who = name or email or "unknown attendee"
    return f"{who} ({title})" if title else who


def classify_call_level(external_attendees: Iterable[dict[str, Any]]) -> CallLevelSuggestion:
    """Suggest L1/L2/L3 from the EXTERNAL attendees of a client call.

    Callers must filter out internal attendees first (see
    ``app.services.internal_domains.external_attendees``) — the SOP counts the
    prospect side, and every call has at least one Beacon rep on it, so counting
    everyone would push every 1:1 into L2.
    """
    attendees = [a for a in external_attendees if isinstance(a, dict)]
    count = len(attendees)
    titles_known = sum(1 for a in attendees if _attendee_title(a))
    senior = [_attendee_label(a) for a in attendees if is_svp_or_above(_attendee_title(a))]

    if count == 0:
        return CallLevelSuggestion(
            level=None,
            confidence="high",
            rationale="No external attendees — this is an internal meeting, not a client call.",
        )

    # Seniority first: a lone SVP is L3, not L1.
    if senior:
        return CallLevelSuggestion(
            level="L3",
            confidence="high",
            rationale=f"SVP+ on the invite: {', '.join(senior)}. Lead with brand vision, demo output only.",
            external_count=count,
            titles_known=titles_known,
            senior_attendees=senior,
        )

    # Group size. Confidence turns on whether we could see every title: with an
    # unknown title in the room, an SVP could be present and this would be L3.
    all_titles_known = titles_known == count
    if count >= 2:
        if all_titles_known:
            rationale = (
                f"{count} external attendees, none SVP+ — moderate discovery, deep platform demo. "
                "Next step: book a technical deep dive with an exec looped in."
            )
            confidence = "high"
        else:
            rationale = (
                f"{count} external attendees, so L2 by group size — but "
                f"{count - titles_known} of them have no title on record, so an SVP+ cannot be "
                "ruled out. Confirm the audience at the prep call."
            )
            confidence = "low"
        return CallLevelSuggestion(
            level="L2", confidence=confidence, rationale=rationale,
            external_count=count, titles_known=titles_known,
        )

    # Exactly one external attendee.
    title = _attendee_title(attendees[0])
    if not title:
        return CallLevelSuggestion(
            level="L1",
            confidence="low",
            rationale=(
                f"One external attendee ({_attendee_label(attendees[0])}) with no title on record. "
                "L1 by group size — confirm seniority at the prep call, since a solo SVP+ is L3."
            ),
            external_count=1, titles_known=0,
        )
    if is_director_band(title):
        return CallLevelSuggestion(
            level="L1",
            confidence="high",
            rationale=(
                f"Solo {title} — deep discovery, high-level demo, output glimpses only. "
                "Next step: book a Demo Deep Dive with a larger audience."
            ),
            external_count=1, titles_known=1,
        )
    return CallLevelSuggestion(
        level="L1",
        confidence="low",
        rationale=(
            f"One external attendee ({_attendee_label(attendees[0])}). L1 by group size, but the "
            "title is outside the Director/VP band the SOP describes — confirm at the prep call."
        ),
        external_count=1, titles_known=1,
    )


def normalize_call_level(value: Any) -> Optional[str]:
    """Accept 'l2', ' L2 ', 'L2' -> 'L2'; anything else -> None (caller 422s)."""
    candidate = str(value or "").strip().upper()
    return candidate if candidate in CALL_LEVELS else None


async def apply_auto_call_level(session, meeting, *, commit: bool = False) -> Optional[str]:
    """Write the classifier's suggestion onto a meeting, unless a human decided.

    Returns the level now on the meeting (possibly unchanged). The manual guard
    is the whole point: attendee lists churn right up to the call as people
    accept and decline, so this runs on every sync, and without the guard the
    AE's prep-call judgement would silently revert the next time someone added a
    note-taker to the invite.
    """
    if (meeting.call_level_source or "") == "manual":
        return meeting.call_level

    suggestion = await suggest_for_meeting(session, meeting)
    if suggestion.level == meeting.call_level and meeting.call_level_source == "auto":
        return meeting.call_level          # no write, no needless updated_at bump

    meeting.call_level = suggestion.level
    meeting.call_level_source = "auto" if suggestion.level else None
    meeting.updated_at = datetime.utcnow()
    session.add(meeting)
    if commit:
        await session.commit()
        await session.refresh(meeting)
    return meeting.call_level


async def suggest_for_meeting(session, meeting) -> CallLevelSuggestion:
    """Classify one meeting: external attendees only, titles enriched from CRM.

    Attendee blobs carry a `title` only when the calendar/tl;dv payload happened
    to include one (126 of 4,649 in production). Where an attendee was matched
    to a Contact, that contact's title is a second source — worth reading even
    though it currently adds only a few, because it is the source that improves
    as reps enrich contacts, and it costs one indexed query.
    """
    from sqlalchemy import select

    from app.models.contact import Contact
    from app.services.internal_domains import external_attendees, get_internal_domains

    domains = await get_internal_domains(session)
    externals = external_attendees(meeting.attendees, domains)
    if not externals:
        return classify_call_level([])

    # Only look up the ones we have no title for — an inline title is the more
    # specific record of who actually attended.
    missing = [a for a in externals if not _attendee_title(a)]
    titles_by_email: dict[str, str] = {}
    emails = {str(a.get("email") or "").strip().lower() for a in missing}
    emails.discard("")
    if emails:
        rows = (
            await session.execute(
                select(Contact.email, Contact.title).where(
                    func.lower(Contact.email).in_(emails), Contact.title.is_not(None)
                )
            )
        ).all()
        titles_by_email = {str(e).strip().lower(): t for e, t in rows if t}

    enriched = []
    for attendee in externals:
        if _attendee_title(attendee):
            enriched.append(attendee)
            continue
        email = str(attendee.get("email") or "").strip().lower()
        found = titles_by_email.get(email)
        # Copy rather than mutate: `meeting.attendees` is a plain JSONB column
        # with no MutableDict tracking, and writing through it here would either
        # be a silent no-op or an unintended persisted change on the next flush.
        enriched.append({**attendee, "title": found} if found else attendee)
    return classify_call_level(enriched)
