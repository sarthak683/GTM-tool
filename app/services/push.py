"""
Web Push (VAPID) sender.

Thin wrapper around pywebpush so endpoint code stays free of crypto / HTTP
plumbing. The send_to_user() helper looks up every PushSubscription row for
the user and fans the same payload out, deleting any subscription the push
service reports as gone (HTTP 404 / 410 — the standard "this user uninstalled
the PWA / cleared site data" signals).

Why this is small
-----------------
Browsers do the hard part. We just POST an encrypted body to the URL the
browser handed us at subscribe time. pywebpush handles ECDH key agreement,
AES-GCM encryption, and the JWT-signed Authorization header.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.push_subscription import PushSubscription

logger = logging.getLogger(__name__)

# pywebpush is an optional runtime dependency — keep the import lazy so the
# rest of the app boots even if the wheel failed to install in some dev
# environment. The service's public functions explicitly check.
try:
    from pywebpush import WebPushException, webpush  # type: ignore
    _PYWEBPUSH_AVAILABLE = True
except Exception as exc:  # pragma: no cover - import-time guard
    _PYWEBPUSH_AVAILABLE = False
    logger.warning("pywebpush unavailable: %s — push notifications will be no-ops", exc)


def vapid_configured() -> bool:
    """True if the server has the VAPID keys it needs to actually send pushes."""
    return _PYWEBPUSH_AVAILABLE and bool(settings.VAPID_PRIVATE_KEY) and bool(settings.VAPID_PUBLIC_KEY)


def _send_one(subscription: PushSubscription, payload: dict[str, Any]) -> tuple[bool, Optional[int]]:
    """Push one notification. Returns (ok, http_status).

    A None status means we failed before getting any HTTP response (e.g.
    network / VAPID misconfig). Status 404 / 410 signals the subscription
    should be deleted by the caller — that's how browsers tell servers a
    user uninstalled the PWA or revoked permission.
    """
    if not vapid_configured():
        return False, None
    sub_info = {
        "endpoint": subscription.endpoint,
        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
    }
    try:
        webpush(
            subscription_info=sub_info,
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
            ttl=60,  # short TTL — a stale "ring this call" is worthless
        )
        return True, 201
    except WebPushException as exc:  # type: ignore[misc]
        status = getattr(exc.response, "status_code", None)
        logger.info("push send failed for %s: status=%s", subscription.endpoint, status)
        return False, status
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("push send crashed: %s", exc)
        return False, None


async def send_to_user(
    session: AsyncSession,
    user_id: UUID,
    payload: dict[str, Any],
) -> dict[str, int]:
    """Send `payload` to every device this user has registered.

    Returns a small summary so the calling endpoint can hand it back to the
    UI ("Rang 2 devices, 0 invalid"). Auto-deletes subscriptions the push
    service reports as gone (404/410) — keeps the table from growing stale.
    """
    if not vapid_configured():
        # No keys = no-op. The endpoint will return 0/0 and the UI can show
        # "configure VAPID keys" guidance. We deliberately don't raise so a
        # missing key doesn't break the desktop sidebar flow.
        return {"sent": 0, "removed": 0, "total": 0, "configured": 0}

    rows = (
        await session.execute(select(PushSubscription).where(PushSubscription.user_id == user_id))
    ).scalars().all()

    sent = 0
    removed = 0
    stale_ids: list[UUID] = []
    for sub in rows:
        ok, status = _send_one(sub, payload)
        if ok:
            sent += 1
            sub.last_used_at = datetime.utcnow()
            session.add(sub)
        elif status in (404, 410):
            stale_ids.append(sub.id)
            removed += 1

    if stale_ids:
        await session.execute(delete(PushSubscription).where(PushSubscription.id.in_(stale_ids)))

    await session.commit()
    return {"sent": sent, "removed": removed, "total": len(rows), "configured": 1}
