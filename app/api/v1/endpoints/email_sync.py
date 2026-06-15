"""
Email sync endpoints — manual trigger + status.

The actual syncing runs as a Celery beat task every 3 minutes.
These endpoints let admins trigger an immediate sync or check status.
"""
from fastapi import APIRouter

from app.config import settings
from app.core.dependencies import AdminUser, CurrentUser, DBSession
from app.models.settings import WorkspaceSettings

router = APIRouter(prefix="/email-sync", tags=["email-sync"])


@router.post("/trigger")
async def trigger_email_sync(session: DBSession, _admin: AdminUser):
    """Manually trigger an immediate Gmail inbox sync (queues a Celery task)."""
    row = await session.get(WorkspaceSettings, 1)
    inbox = (row.gmail_shared_inbox if row and row.gmail_shared_inbox else settings.GMAIL_SHARED_INBOX).strip() if (row and row.gmail_shared_inbox) or settings.GMAIL_SHARED_INBOX else ""
    token_data = row.gmail_token_data if row else None
    if not inbox or not (token_data or settings.GMAIL_TOKEN_JSON):
        return {"status": "disabled", "message": "Gmail inbox is not connected"}

    from app.tasks.email_sync import sync_gmail_inbox
    task = sync_gmail_inbox.delay()
    return {"status": "queued", "task_id": task.id}


@router.get("/status")
async def email_sync_status(session: DBSession, _user: CurrentUser):
    """Check email sync configuration and last sync time."""
    import redis
    from app.tasks.email_sync import REDIS_KEY_LAST_SYNC

    row = await session.get(WorkspaceSettings, 1)
    inbox = (row.gmail_shared_inbox if row and row.gmail_shared_inbox else settings.GMAIL_SHARED_INBOX).strip() if (row and row.gmail_shared_inbox) or settings.GMAIL_SHARED_INBOX else ""
    token_data = row.gmail_token_data if row else None
    result = {
        "enabled": bool(inbox and (token_data or settings.GMAIL_TOKEN_JSON)),
        "inbox": inbox or None,
        "interval_seconds": settings.EMAIL_SYNC_INTERVAL_SECONDS,
        "last_sync_epoch": None,
        "connected_email": row.gmail_connected_email if row else None,
    }

    if inbox:
        try:
            r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            last = r.get(REDIS_KEY_LAST_SYNC)
            result["last_sync_epoch"] = int(last) if last else None
            r.close()
        except Exception:
            pass

    return result
