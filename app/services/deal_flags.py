"""Deal flag matrix — derive Green/Yellow/Red flags + forecast bucket from MEDDPICC.

Translates the existing MEDDPICC level (0-3) + captured evidence into the flag
matrix the team uses on forecast calls:

    GREEN  = level 3 (confirmed) AND fresh captured evidence
    YELLOW = level 1 or 2, OR a level-3 dimension whose evidence has gone stale
    RED    = level 0 (not started / missing)

Forecast bucket follows directly from the flag mix:

    commit     -> all 8 dimensions GREEN
    pipeline   -> any RED
    best_case  -> otherwise (all known, at least one not yet validated in writing)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models.deal import MEDDPICC_FIELDS
from app.services.meddpicc_updates import (
    detail_has_capture,
    detail_updated_at,
    get_meddpicc_detail,
    get_meddpicc_snapshot,
)

# A level-3 ("confirmed") field reverts to YELLOW after this many days without
# refreshed evidence. The forecast call discipline is "validated this week" —
# 30 days is a forgiving floor that still catches stale Greens.
GREEN_FRESHNESS_DAYS = 30

FLAG_GREEN = "green"
FLAG_YELLOW = "yellow"
FLAG_RED = "red"

FORECAST_COMMIT = "commit"
FORECAST_BEST_CASE = "best_case"
FORECAST_PIPELINE = "pipeline"

# Human labels used in blocker strings — keep in sync with MEDDPICC_DIMENSIONS
# on the frontend so the UI and API agree.
_FIELD_LABELS = {
    "metrics": "Metrics",
    "economic_buyer": "Economic Buyer",
    "decision_criteria": "Decision Criteria",
    "decision_process": "Decision Process",
    "paper_process": "Paper Process",
    "identify_pain": "Identified Pain",
    "champion": "Champion",
    "competition": "Competition",
}


def _flag_for_field(
    *,
    level: int,
    detail: dict[str, Any],
    now: datetime,
) -> str:
    if level <= 0:
        return FLAG_RED
    if level >= 3:
        # Green requires both confirmation AND captured evidence. A "3" with
        # no notes is a rep claim, not proof — downgrade to Yellow.
        if not detail_has_capture(detail):
            return FLAG_YELLOW
        updated = detail_updated_at(detail)
        if updated is not None and (now - updated).days > GREEN_FRESHNESS_DAYS:
            return FLAG_YELLOW
        return FLAG_GREEN
    return FLAG_YELLOW


def compute_deal_flags(
    qualification: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the flag matrix + forecast bucket for a deal's qualification JSON.

    Pure function — no DB, no I/O. Safe to call from board queries.
    """
    now = now or datetime.utcnow()
    snapshot = get_meddpicc_snapshot(qualification)

    flags: dict[str, str] = {}
    blockers: list[str] = []
    yellows: list[str] = []
    for field in MEDDPICC_FIELDS:
        level = snapshot.get(field, 0)
        detail = get_meddpicc_detail(qualification, field)
        flag = _flag_for_field(level=level, detail=detail, now=now)
        flags[field] = flag
        label = _FIELD_LABELS.get(field, field)
        if flag == FLAG_RED:
            blockers.append(label)
        elif flag == FLAG_YELLOW:
            yellows.append(label)

    green_count = sum(1 for f in flags.values() if f == FLAG_GREEN)
    yellow_count = sum(1 for f in flags.values() if f == FLAG_YELLOW)
    red_count = sum(1 for f in flags.values() if f == FLAG_RED)

    if red_count > 0:
        forecast_category = FORECAST_PIPELINE
    elif yellow_count == 0 and green_count == len(MEDDPICC_FIELDS):
        forecast_category = FORECAST_COMMIT
    else:
        forecast_category = FORECAST_BEST_CASE

    return {
        "flags": flags,
        "forecast_category": forecast_category,
        "green_count": green_count,
        "yellow_count": yellow_count,
        "red_count": red_count,
        "flag_blockers": blockers,
        "flag_yellows": yellows,
    }
