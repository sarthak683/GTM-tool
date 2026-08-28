"""
Single write path for Contact.call_disposition transitions.

Every endpoint that mutates Contact.call_disposition MUST call
`record_disposition_change` before committing. Mirrors
app.services.deal_stage_history.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact_disposition_history import ContactDispositionHistory


async def record_disposition_change(
    session: AsyncSession,
    *,
    contact_id: UUID,
    from_disposition: Optional[str],
    to_disposition: Optional[str],
    changed_by_id: Optional[UUID] = None,
    changed_at: Optional[datetime] = None,
) -> ContactDispositionHistory:
    """Add a history row to the session. Caller is responsible for commit."""
    row = ContactDispositionHistory(
        contact_id=contact_id,
        from_disposition=from_disposition,
        to_disposition=to_disposition,
        changed_by_id=changed_by_id,
        changed_at=changed_at or datetime.utcnow(),
    )
    session.add(row)
    return row
