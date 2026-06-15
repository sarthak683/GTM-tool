"""
Centralized notification creation.

Detection code (webhooks, classifiers, schedulers) calls
`create_notification(...)` rather than INSERTing directly. This gives one
place to enforce idempotency (`dedup_key`) and to fan out to additional
delivery channels (browser push today, email / Slack later) without
touching every call site.

A Notification is *not* a Task. Tasks are durable work the rep owes;
Notifications are signals the system noticed that decay once
acknowledged. Surface a Notification when the rep needs to react in the
moment; create a Task when something needs to live on a backlog.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification

logger = logging.getLogger(__name__)


async def create_notification(
    session: AsyncSession,
    *,
    user_id: UUID,
    type: str,
    title: str,
    body: Optional[str] = None,
    action_payload: Optional[dict[str, Any]] = None,
    dedup_key: Optional[str] = None,
    push: bool = True,
) -> Notification:
    """Create (or return existing) notification, then optionally fire a push.

    - When `dedup_key` is provided and a row already exists for
      (user_id, dedup_key), return that row unchanged. This makes the
      function safe to call from re-delivered webhooks.
    - `push=True` (default) sends a browser push via
      `app.services.push.send_to_user` so reps on a closed tab still
      see the alert. Push failures are logged and swallowed — the
      in-app notification row is the source of truth.
    """
    if dedup_key:
        existing = (await session.execute(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.dedup_key == dedup_key,
            )
        )).scalar_one_or_none()
        if existing:
            return existing

    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        action_payload=action_payload,
        dedup_key=dedup_key,
    )
    session.add(notification)
    await session.commit()
    await session.refresh(notification)

    if push:
        try:
            from app.services.push import send_to_user

            await send_to_user(
                session,
                user_id,
                {
                    "title": title,
                    "body": body or "",
                    "data": {
                        "type": type,
                        "notification_id": str(notification.id),
                    },
                },
            )
        except Exception:
            # Push is best-effort — the bell is the source of truth.
            logger.exception("Push delivery failed for notification %s", notification.id)

    return notification


async def notify_records_added(
    session: AsyncSession,
    *,
    kind: str,
    count: int,
    actor_name: Optional[str] = None,
    owner_user_id: Optional[UUID] = None,
    detail: Optional[str] = None,
    dedup_key: Optional[str] = None,
) -> int:
    """Fan an informational 'records added' alert to admins + the assigned owner.

    `kind` is "prospects" or "accounts". Recipients (all admins plus the owner)
    are de-duplicated so overlap doesn't create double bell rows. When
    `dedup_key` is given it's namespaced per-recipient so a re-run is idempotent.
    Returns the number of bell rows created.
    """
    if count <= 0:
        return 0
    from app.models.user import User

    recipient_ids: set[UUID] = set(
        (await session.execute(select(User.id).where(User.role == "admin"))).scalars().all()
    )
    if owner_user_id:
        recipient_ids.add(owner_user_id)
    if not recipient_ids:
        return 0

    who = actor_name or "Someone"
    title = f"{count} {kind} added"
    body = detail or f"{who} added {count} {kind}."
    created = 0
    for rid in recipient_ids:
        await create_notification(
            session,
            user_id=rid,
            type="records_added",
            title=title,
            body=body,
            action_payload={"kind": kind, "count": count, "actor": who, "detail": detail},
            dedup_key=f"{dedup_key}:{rid}" if dedup_key else None,
            push=False,  # informational — don't push-spam; the bell is enough
        )
        created += 1
    return created
