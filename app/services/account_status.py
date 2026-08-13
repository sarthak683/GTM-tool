"""Account-level status automation.

Company ``account_status`` is normally set manually by reps on the detail page,
but the source of truth should also reflect strong engagement signals without
the rep having to touch the dropdown twice. This module owns the forward-only
advance rules and the single helper the various activity / deal funnels call.

Rules (see ``should_advance_account_status``):
  * Lifecycle only moves FORWARD: cold -> in_progress -> meeting_booked ->
    meeting_done -> in_pipeline. A later signal never downgrades an account
    that has already progressed.
  * Manual parked states (``not_a_fit``, ``dnd``, ``reach_out_later``) are
    respected by weak signals: an ``in_progress`` touch does NOT revive a
    parked account. Only strong signals (meeting_booked / meeting_done /
    in_pipeline) override them, because a booked meeting or a live deal is
    observable reality regardless of an earlier manual label.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company

# Strong lifecycle signals, ordered weakest -> strongest. Everything in this
# map is a value we automate TOWARD. Anything absent (e.g. the manual parked
# states) can never be a target of automation.
ACCOUNT_STATUS_LIFECYCLE_RANK: dict[str, int] = {
    "cold": 0,
    "in_progress": 1,
    "meeting_booked": 2,
    "meeting_done": 3,
    "in_pipeline": 4,
}

# Manual states reps deliberately park accounts in. Weak automation signals
# must not override these; strong lifecycle signals may.
NEGATIVE_ACCOUNT_STATUSES: frozenset[str] = frozenset(
    {"not_a_fit", "dnd", "reach_out_later"}
)


def should_advance_account_status(
    current: Optional[str],
    target: str,
) -> bool:
    """Whether an automated bump from ``current`` to ``target`` is allowed.

    Forward-only lifecycle semantics + manual parked-state protection. Pure
    function so it is trivially unit-testable.
    """
    target_rank = ACCOUNT_STATUS_LIFECYCLE_RANK.get(target)
    if target_rank is None:
        return False  # never automate toward a negative/manual status
    if current == target:
        return False
    if current in NEGATIVE_ACCOUNT_STATUSES:
        return target_rank > 1  # only strong signals revive a parked account
    current_rank = ACCOUNT_STATUS_LIFECYCLE_RANK.get(current or "cold", 0)
    return target_rank > current_rank


async def bump_company_account_status(
    session: AsyncSession,
    company_id,
    target: str,
) -> Optional[str]:
    """Advance ``Company.account_status`` to ``target`` if the lifecycle allows.

    No-op when the company is missing or the move would be a downgrade or an
    override of a parked state. Returns the new status when it changed, else
    None. Caller is responsible for committing.
    """
    company = await session.get(Company, company_id)
    if company is None:
        return None
    if not should_advance_account_status(company.account_status, target):
        return None
    company.account_status = target
    session.add(company)
    return target
