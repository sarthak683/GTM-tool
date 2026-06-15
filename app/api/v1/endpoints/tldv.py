from __future__ import annotations

from fastapi import APIRouter, Query

from app.clients.tldv import TldvClient
from app.core.dependencies import AdminUser, DBSession
from app.services.tldv_sync import sync_tldv_history, sync_tldv_meeting

router = APIRouter(prefix="/tldv", tags=["tldv"])


@router.get("/health")
async def tldv_health(_admin: AdminUser):
    client = TldvClient()
    if client.mock:
        return {"configured": False}
    return {"configured": True, "health": await client.health()}


@router.post("/sync/history")
async def tldv_sync_history(
    session: DBSession,
    _admin: AdminUser,
    page_size: int = Query(default=50, ge=1, le=100),
    max_pages: int | None = Query(default=None, ge=1, le=200),
    lookback_days: int | None = Query(default=None, ge=1, le=3650),
):
    return await sync_tldv_history(
        session,
        page_size=page_size,
        max_pages=max_pages,
        lookback_days=lookback_days,
    )


@router.post("/sync/meeting/{meeting_id}")
async def tldv_sync_one_meeting(meeting_id: str, session: DBSession, _admin: AdminUser):
    return await sync_tldv_meeting(session, meeting_id=meeting_id)
