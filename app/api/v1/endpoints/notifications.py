"""
In-app notification endpoints. Drives the bell icon in the navbar.

  GET    /api/v1/notifications/           — list (optionally unread_only)
  GET    /api/v1/notifications/unread/count — fast badge count
  POST   /api/v1/notifications/{id}/read
  POST   /api/v1/notifications/{id}/dismiss
  POST   /api/v1/notifications/{id}/accept — per-type action dispatch

The accept dispatcher is the only type-aware piece. Adding a new
notification type requires extending the dispatcher with one more branch
(or — if it's pure UI / no server action — none at all).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select, update

from app.core.dependencies import CurrentUser, DBSession
from app.models.notification import Notification, NotificationRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/", response_model=list[NotificationRead])
async def list_notifications(
    session: DBSession,
    user: CurrentUser,
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, le=200),
):
    """List the current rep's notifications, newest first."""
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


@router.get("/unread/count")
async def unread_count(session: DBSession, user: CurrentUser) -> dict[str, int]:
    """Cheap polling endpoint for the badge."""
    n = (await session.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id,
            Notification.read_at.is_(None),
            Notification.dismissed_at.is_(None),
        )
    )).scalar_one()
    return {"unread": int(n or 0)}


@router.post("/dismiss-all")
async def dismiss_all(session: DBSession, user: CurrentUser) -> dict[str, int]:
    """Clear all of the caller's active notifications in one shot — dismiss them
    and mark any unread ones read. Powers the bell's 'Clear all' button."""
    now = datetime.utcnow()
    result = await session.execute(
        update(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.dismissed_at.is_(None),
        )
        .values(
            dismissed_at=now,
            read_at=func.coalesce(Notification.read_at, now),
            updated_at=now,
        )
    )
    await session.commit()
    return {"dismissed": int(result.rowcount or 0)}


async def _own_or_404(session, user, notification_id: UUID) -> Notification:
    """Fetch a notification and assert the caller owns it."""
    notification = (await session.execute(
        select(Notification).where(Notification.id == notification_id)
    )).scalar_one_or_none()
    if not notification:
        raise HTTPException(404, "Notification not found.")
    if notification.user_id != user.id:
        # 404 over 403 so we don't leak the existence of another rep's notification.
        raise HTTPException(404, "Notification not found.")
    return notification


@router.post("/{notification_id}/read", response_model=NotificationRead)
async def mark_read(notification_id: UUID, session: DBSession, user: CurrentUser):
    notification = await _own_or_404(session, user, notification_id)
    if notification.read_at is None:
        notification.read_at = datetime.utcnow()
        notification.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(notification)
    return notification


@router.post("/{notification_id}/dismiss", response_model=NotificationRead)
async def dismiss(notification_id: UUID, session: DBSession, user: CurrentUser):
    notification = await _own_or_404(session, user, notification_id)
    now = datetime.utcnow()
    if notification.dismissed_at is None:
        notification.dismissed_at = now
    if notification.read_at is None:
        notification.read_at = now
    notification.updated_at = now
    await session.commit()
    await session.refresh(notification)
    return notification


@router.post("/{notification_id}/accept")
async def accept(notification_id: UUID, session: DBSession, user: CurrentUser):
    """Per-type accept dispatcher.

    Returns whatever shape makes sense for the type:
      - meeting_booked_suggest_deal → {"deal_id": "...", "notification": {...}}
    """
    notification = await _own_or_404(session, user, notification_id)
    if notification.accepted_at is not None:
        raise HTTPException(409, "Notification already accepted.")
    if notification.dismissed_at is not None:
        raise HTTPException(409, "Notification was dismissed.")

    result: dict[str, Any]
    if notification.type == "meeting_booked_suggest_deal":
        result = await _accept_meeting_booked(session, user, notification)
    else:
        raise HTTPException(400, f"Notification type {notification.type!r} has no accept handler.")

    notification.accepted_at = datetime.utcnow()
    if notification.read_at is None:
        notification.read_at = notification.accepted_at
    notification.updated_at = notification.accepted_at
    await session.commit()
    await session.refresh(notification)

    result["notification"] = NotificationRead.model_validate(notification, from_attributes=True).model_dump(mode="json")
    return result


async def _accept_meeting_booked(session, user, notification: Notification) -> dict[str, Any]:
    """Materialize a Deal from the action_payload."""
    from app.models.activity import Activity
    from app.models.contact import Contact
    from app.models.deal import Deal, DealContact
    from app.repositories.deal import DealRepository
    from app.services.company_stage_milestones import record_deal_stage_milestone
    from app.services.deal_stages import get_configured_deal_stage_ids, get_configured_default_deal_stage

    payload = notification.action_payload or {}
    contact_id_raw = payload.get("contact_id")
    if not contact_id_raw:
        raise HTTPException(400, "Notification payload missing contact_id.")
    try:
        contact_id = UUID(str(contact_id_raw))
    except (ValueError, TypeError):
        raise HTTPException(400, "Notification payload has invalid contact_id.")

    contact: Optional[Contact] = (await session.execute(
        select(Contact).where(Contact.id == contact_id)
    )).scalar_one_or_none()
    if not contact:
        raise HTTPException(404, "Contact has been deleted; can't create deal.")

    # Refuse to create a duplicate deal if this contact is already a
    # primary stakeholder on an active deal. The rep should manage the
    # existing deal, not spin a new one.
    existing = (await session.execute(
        select(Deal.id)
        .join(DealContact, DealContact.deal_id == Deal.id)
        .where(
            DealContact.contact_id == contact_id,
            Deal.stage.notin_(["won", "lost", "closed_won", "closed_lost"]),
        )
        .limit(1)
    )).first()
    if existing:
        raise HTTPException(
            409,
            "An active deal already exists for this contact. Dismiss this notification.",
        )

    # Compose the deal name from contact + company so the pipeline card
    # is readable at a glance: "Acme · Jane Doe".
    contact_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip() or contact.email or "New deal"
    company_name = payload.get("company_name")
    deal_name = (f"{company_name} · {contact_name}" if company_name else contact_name)[:200]

    # Meeting-booked notifications imply the prospect has agreed to meet,
    # so the deal belongs in `demo_scheduled` (or the closest configured
    # equivalent) — NOT the prospecting/lead bucket. We probe the
    # workspace's enabled stages and pick the first match from a
    # preference list; if none match, fall back to the workspace default
    # so we never fail on an unfamiliar pipeline.
    enabled_stages = await get_configured_deal_stage_ids(session)
    preferred_stage_order = ("demo_scheduled", "meeting_scheduled", "demo_done", "qualified_lead")
    stage = next((s for s in preferred_stage_order if s in enabled_stages), None)
    if stage is None:
        stage = await get_configured_default_deal_stage(session)
    now = datetime.utcnow()

    # Route through DealRepository.create() rather than constructing the
    # Deal model directly — the repository auto-generates required
    # downstream fields (email_cc_alias being the NOT NULL one), runs
    # slug normalization, and matches the path the regular POST /deals/
    # endpoint takes so the two creation paths produce identical rows.
    repo = DealRepository(session)
    deal = await repo.create({
        "name": deal_name,
        "pipeline_type": "deal",
        "stage": stage,
        "priority": "normal",
        "company_id": contact.company_id,
        "assigned_to_id": contact.assigned_to_id or user.id,
        # Preserve SDR-sourced pipeline credit through conversion.
        "sdr_id": contact.sdr_id,
        "source": "meeting_booked_notification",
        "description": payload.get("reply_summary") or None,
        "next_step": payload.get("next_step") or "Confirm meeting and prepare agenda",
        "next_step_updated_at": now,
        "stage_entered_at": now,
        "tags": [],
        "health": "green",
    })

    # Link the contact as the primary stakeholder.
    session.add(DealContact(deal_id=deal.id, contact_id=contact.id, role="primary"))

    session.add(Activity(
        deal_id=deal.id,
        contact_id=contact.id,
        type="deal_created",
        source="system",
        content=f"Deal created from meeting-booked notification — {payload.get('reply_summary') or 'reply parsed by AI'}",
        event_metadata={"notification_id": str(notification.id), **(payload or {})},
    ))

    try:
        await record_deal_stage_milestone(
            session,
            deal=deal,
            stage=deal.stage,
            reached_at=deal.stage_entered_at or now,
            source="notification_accept",
        )
    except Exception:
        # Non-fatal — milestone tracking shouldn't block deal creation.
        logger.exception("record_deal_stage_milestone failed for new deal %s", deal.id)

    await session.commit()
    full = await repo.get_with_joins(deal.id) or deal
    return {"deal_id": str(full.id), "deal_name": full.name, "stage": full.stage}
