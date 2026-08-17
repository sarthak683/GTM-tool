"""
Single write path for deal stage transitions.

Every endpoint that mutates Deal.stage MUST call `record_stage_transition`
before committing. This keeps the audit log in lock-step with the deal row.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal_stage_history import DealStageHistory

# Canonical win/loss close reasons stored in DealStageHistory.reason (and
# mirrored into Deal.qualification.close_reason). The frontend mirror lives in
# frontend/src/lib/closeReasons.ts and the win/loss analytics rollup matches
# on EXACTLY these values — do not rename/remove entries without coordinating
# both sides.
CLOSE_REASONS: tuple[str, ...] = (
    "budget",
    "timing",
    "lost_to_competitor",
    "no_response",
    "not_a_fit",
    "pricing",
    "champion_left",
    "other",
)


async def record_stage_transition(
    session: AsyncSession,
    *,
    deal_id: UUID,
    from_stage: Optional[str],
    to_stage: str,
    changed_by_id: Optional[UUID] = None,
    source: Optional[str] = None,
    reason: Optional[str] = None,
    changed_at: Optional[datetime] = None,
) -> DealStageHistory:
    """Add a history row to the session. Caller is responsible for commit."""
    row = DealStageHistory(
        deal_id=deal_id,
        from_stage=from_stage,
        to_stage=to_stage,
        changed_by_id=changed_by_id,
        changed_at=changed_at or datetime.utcnow(),
        source=source,
        reason=reason,
    )
    session.add(row)
    return row
