"""
Deal health scoring engine.

Score 0–100 across three dimensions:
  - Engagement recency   (40 pts) — how recently was there activity?
  - Stakeholder coverage (30 pts) — how many stakeholders are engaged?
  - Stage velocity       (30 pts) — is the deal moving or stalling?

Maps to: green (70+) | yellow (40–69) | red (0–39)
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from app.models.activity import Activity
    from app.models.deal import Deal


def compute_health(
    deal: "Deal",
    activities: List["Activity"],
    *,
    last_activity_at: Optional[datetime] = None,
) -> tuple[int, str]:
    """Return (health_score 0-100, health str) for a deal.

    Only the LATEST activity timestamp feeds the score. Batch callers can
    pass it via `last_activity_at` (one grouped max() query for all deals)
    instead of materializing every Activity row per deal.
    """
    score = 0

    # 1. Engagement recency (40 pts)
    if last_activity_at is None and activities:
        last_activity_at = max(activities, key=lambda a: a.created_at).created_at
    if last_activity_at is not None:
        days_since = (datetime.utcnow() - last_activity_at).days
        if days_since <= 3:
            score += 40
        elif days_since <= 7:
            score += 33
        elif days_since <= 14:
            score += 22
        elif days_since <= 30:
            score += 10
        # >30 days = 0 engagement points

    # 2. Stakeholder coverage (30 pts)
    stakeholders = deal.stakeholder_count or 0
    if stakeholders >= 3:
        score += 30
    elif stakeholders == 2:
        score += 20
    elif stakeholders == 1:
        score += 10

    # 3. Stage velocity (30 pts) — penalise stale deals
    days_in_stage = deal.days_in_stage or 0
    if days_in_stage <= 7:
        score += 30
    elif days_in_stage <= 14:
        score += 24
    elif days_in_stage <= 30:
        score += 14
    elif days_in_stage <= 60:
        score += 5
    # >60 days = 0 velocity points

    score = min(score, 100)

    if score >= 70:
        health = "green"
    elif score >= 40:
        health = "yellow"
    else:
        health = "red"

    return score, health
