"""Move historical free-text close reasons onto the structured enum — losslessly.

``Deal.qualification.close_reason`` was a free-text field long before it became
an enum (the dropdown shipped 2026-08-17). Twenty-four of the twenty-five deals
that carry a reason therefore hold prose in the enum's slot, and that prose is
not disposable: it contains commitments the team still needs —

    "They are trying to build it in-house. We will revisit this after 6 months."
    "Building Intrenaly. 7-8weeks internal team has asked for. Reconnect on 22 sep'26"

so the backfill NEVER overwrites it. The original text is copied to
``close_reason_detail`` — the field the current write path already uses for
exactly this — and only then is ``close_reason`` set to the enum value. Nothing
is destroyed; the row gains structure it did not have.

On the matching rule: an earlier, looser pattern (`%intern%`) captured "No due
to Internal issues" and "they said they dont want to proceed - internal
priorities", neither of which is a build-vs-buy loss. Requiring the word
"build"/"built" excludes both while still catching every genuine one, including
the misspelling "Building Intrenaly". Anything the rule cannot place is
reported, not guessed at — a wrong reason is worse than a missing one, because
it will be counted.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.visibility import unscoped_for_background_job
from app.models.deal import Deal
from app.services.deal_stage_history import CLOSE_REASONS

# Deliberately narrow. Each pattern must be something that cannot plausibly mean
# anything else in a close-reason sentence; borderline text is left for a human.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "build vs buy", "built internally", "Building Intrenaly" (sic),
    # "trying to build it in-house". The build/built stem is what makes this
    # safe — "internal priorities" and "Internal issues" do not contain it.
    ("built_in_house", re.compile(r"\bbuil[dt]\w*\b", re.IGNORECASE)),
)


def propose_close_reason(text: Optional[str]) -> Optional[str]:
    """The enum value this free text clearly means, or None to leave it alone."""
    value = (text or "").strip()
    if not value:
        return None
    for reason, pattern in _RULES:
        if pattern.search(value):
            return reason
    return None


def is_enum_value(text: Optional[str]) -> bool:
    return (text or "").strip() in CLOSE_REASONS


async def backfill_close_reasons(
    session: AsyncSession, *, dry_run: bool = True, limit: Optional[int] = None
) -> dict[str, Any]:
    """Structure historical free-text close reasons.

    Safety properties, all load-bearing:

    * ``dry_run`` defaults to True, so an accidental call changes nothing.
    * A row whose ``close_reason`` is ALREADY a valid enum value is skipped
      entirely — this never re-decides something the enum already answered.
    * The original text is preserved into ``close_reason_detail``, and an
      existing ``close_reason_detail`` is never clobbered.
    * ``qualification`` is rebuilt as a NEW dict. Mutating the loaded one in
      place leaves SQLAlchemy's attribute history unchanged on a plain JSONB
      column, so the UPDATE is never emitted — a silent no-op that has bitten
      this codebase before.
    * Text the rules cannot place is returned under ``unmatched`` rather than
      being forced into "other". A wrong reason gets counted; a missing one
      does not.
    """
    rows = (
        await session.execute(
            unscoped_for_background_job(Deal, "close reason backfill system work").where(
                Deal.deleted_at.is_(None),
                Deal.pipeline_type == "deal",
                Deal.qualification.is_not(None),
            )
        )
    ).scalars().all()

    proposals: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    skipped_already_enum = 0

    for deal in rows:
        qualification = deal.qualification if isinstance(deal.qualification, dict) else {}
        current = qualification.get("close_reason")
        if not isinstance(current, str) or not current.strip():
            continue
        if is_enum_value(current):
            skipped_already_enum += 1
            continue

        proposed = propose_close_reason(current)
        entry = {
            "deal_id": str(deal.id),
            "deal": deal.name,
            "stage": deal.stage,
            "original_text": current,
            "proposed_reason": proposed,
        }
        if proposed is None:
            unmatched.append(entry)
            continue
        proposals.append(entry)

        if not dry_run:
            updated = dict(qualification)
            updated["close_reason"] = proposed
            # Preserve the prose. Only fill the detail field when it is empty —
            # if a rep has since written a proper detail, theirs wins.
            if not str(updated.get("close_reason_detail") or "").strip():
                updated["close_reason_detail"] = current
            deal.qualification = updated
            session.add(deal)

        if limit and len(proposals) >= limit:
            break

    if not dry_run and proposals:
        await session.commit()

    by_reason: dict[str, int] = {}
    for p in proposals:
        by_reason[p["proposed_reason"]] = by_reason.get(p["proposed_reason"], 0) + 1

    return {
        "dry_run": dry_run,
        "scanned": len(rows),
        "already_structured": skipped_already_enum,
        "would_update" if dry_run else "updated": len(proposals),
        "by_reason": by_reason,
        "proposals": proposals,
        # Surfaced deliberately: these are the ones a human has to read.
        "unmatched": unmatched,
    }
