"""
Single write path for Company.account_status transitions.

Every endpoint that mutates Company.account_status MUST call
`record_account_status_change` before committing. Mirrors
app.services.deal_stage_history.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_status_history import AccountStatusHistory


async def record_account_status_change(
    session: AsyncSession,
    *,
    company_id: UUID,
    from_status: Optional[str],
    to_status: Optional[str],
    changed_by_id: Optional[UUID] = None,
    changed_at: Optional[datetime] = None,
) -> AccountStatusHistory:
    """Add a history row to the session. Caller is responsible for commit."""
    row = AccountStatusHistory(
        company_id=company_id,
        from_status=from_status,
        to_status=to_status,
        changed_by_id=changed_by_id,
        changed_at=changed_at or datetime.utcnow(),
    )
    session.add(row)
    return row
