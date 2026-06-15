"""
Web Push subscription + "ring my mobile" endpoints.

Routes
------
GET    /push/vapid-public-key                  → returns the server's VAPID
                                                  public key (base64url) so
                                                  the browser can subscribe.
POST   /push/subscribe                         → store a PushSubscription for
                                                  the logged-in user.
DELETE /push/subscribe                         → remove by endpoint.
POST   /push/contacts/{contact_id}/ring-mobile → send a "tap to call X"
                                                  notification to every
                                                  device the calling user
                                                  has registered. Best-effort:
                                                  returns counts; never raises
                                                  so the desktop sidebar flow
                                                  is never blocked by a push
                                                  failure.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.config import settings
from app.core.dependencies import CurrentUser, DBSession
from app.models.contact import Contact
from app.models.push_subscription import PushSubscription, PushSubscriptionRead
from app.services.push import send_to_user, vapid_configured

router = APIRouter(prefix="/push", tags=["push"])


class SubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class SubscriptionPayload(BaseModel):
    """Mirrors the browser's PushSubscriptionJSON shape exactly."""
    endpoint: str
    keys: SubscriptionKeys
    user_agent: Optional[str] = None
    label: Optional[str] = None


class UnsubscribePayload(BaseModel):
    endpoint: str


class RingMobileResult(BaseModel):
    sent: int
    removed: int
    total: int
    configured: int  # 0 if the server has no VAPID keys yet


@router.get("/vapid-public-key")
async def get_vapid_public_key(_user: CurrentUser):
    """Return the public key the browser needs to call pushManager.subscribe.

    Exposing the public key is safe by design — it's the *public* half of
    the VAPID identity. The private key never leaves the server.
    """
    return {
        "publicKey": settings.VAPID_PUBLIC_KEY,
        "configured": vapid_configured(),
    }


@router.post("/subscribe", response_model=PushSubscriptionRead)
async def subscribe(
    payload: SubscriptionPayload,
    current_user: CurrentUser,
    session: DBSession,
):
    """Upsert the browser's subscription for the current user.

    Browsers can hand back the same `endpoint` on a re-subscribe (no-op for
    them), but if the user revoked permission and re-granted it the endpoint
    will change. Either way we key on `endpoint` so the table converges to
    one row per active subscription per device.
    """
    existing = (
        await session.execute(
            select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint)
        )
    ).scalar_one_or_none()

    if existing:
        existing.user_id = current_user.id
        existing.p256dh = payload.keys.p256dh
        existing.auth = payload.keys.auth
        existing.user_agent = payload.user_agent
        existing.label = payload.label
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        return PushSubscriptionRead.model_validate(existing)

    sub = PushSubscription(
        user_id=current_user.id,
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth=payload.keys.auth,
        user_agent=payload.user_agent,
        label=payload.label,
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return PushSubscriptionRead.model_validate(sub)


@router.delete("/subscribe")
async def unsubscribe(
    payload: UnsubscribePayload,
    current_user: CurrentUser,
    session: DBSession,
):
    sub = (
        await session.execute(
            select(PushSubscription).where(
                PushSubscription.endpoint == payload.endpoint,
                PushSubscription.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if not sub:
        return {"removed": 0}
    await session.delete(sub)
    await session.commit()
    return {"removed": 1}


@router.post("/contacts/{contact_id}/ring-mobile", response_model=RingMobileResult)
async def ring_mobile(
    contact_id: UUID,
    current_user: CurrentUser,
    session: DBSession,
):
    """Notify the *calling user's* mobile devices that they're about to call X.

    The notification payload includes the contact's phone number and a
    `tel:` deep link; the service worker hands that link to the OS dialer
    when the rep taps the notification.

    This endpoint is intentionally idempotent and best-effort — the desktop
    sidebar opens regardless of push delivery. We just count successes so
    the UI can show a small "Rang 1 device" toast.
    """
    contact = await session.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    contact_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip() or contact.email or "Prospect"
    phone = (contact.phone or "").strip()

    payload = {
        "type": "ring-mobile",
        "title": "Call ready",
        "body": f"Tap to call {contact_name}{' · ' + phone if phone else ''}",
        "tel": phone,
        "contact_id": str(contact.id),
        "contact_name": contact_name,
        "issued_at": datetime.utcnow().isoformat() + "Z",
    }

    result = await send_to_user(session, current_user.id, payload)
    return RingMobileResult(**result)
