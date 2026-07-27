"""Selecting among a user's mailboxes.

A rep may have several connected mailboxes: their primary ``@beacon.li`` login
plus the alternate sending domains they also send manual outreach from
(``@beaconli.com`` / ``@beaconli.co``). The schema has always allowed this —
``uq_user_email_connections_user_email`` is unique on ``(user_id,
email_address)`` — but the read paths were written when one inbox per user was
the only possibility and used ``scalar_one_or_none()``, which raises
``MultipleResultsFound`` the moment a second mailbox exists.

Ingestion wants EVERY active mailbox (``sync_all_personal_inboxes`` already
fans out per connection). Everything else — the status header, outbound
sending, Drive folder selection — wants exactly ONE, and must pick the same one
every time. That one is the PRIMARY: the earliest-connected mailbox, which is
the rep's original login address, since alternates are always added later.
"""
from uuid import UUID

from sqlmodel import select

from app.models.user_email_connection import UserEmailConnection


def primary_connection_stmt(user_id: UUID, *, active_only: bool = False):
    """Select a user's PRIMARY mailbox — deterministic, at most one row.

    Ordered by ``connected_at`` ascending (nulls last) with an ``id`` tie-break
    so the result never depends on row order. Pair with ``.scalars().first()``,
    never ``scalar_one_or_none()``.
    """
    stmt = select(UserEmailConnection).where(UserEmailConnection.user_id == user_id)
    if active_only:
        stmt = stmt.where(UserEmailConnection.is_active == True)  # noqa: E712
    return stmt.order_by(
        UserEmailConnection.connected_at.asc().nullslast(),
        UserEmailConnection.id.asc(),
    ).limit(1)


def all_connections_stmt(user_id: UUID, *, active_only: bool = True):
    """Select ALL of a user's mailboxes, primary first."""
    stmt = select(UserEmailConnection).where(UserEmailConnection.user_id == user_id)
    if active_only:
        stmt = stmt.where(UserEmailConnection.is_active == True)  # noqa: E712
    return stmt.order_by(
        UserEmailConnection.connected_at.asc().nullslast(),
        UserEmailConnection.id.asc(),
    )


def connection_for_mailbox_stmt(user_id: UUID, email_address: str):
    """Select the row for ONE specific mailbox — the upsert key.

    Callbacks must match on (user_id, email_address), not user_id alone.
    Matching on user_id alone overwrote the rep's existing mailbox with the one
    they just authorised, silently disconnecting their primary inbox instead of
    adding a second.
    """
    return select(UserEmailConnection).where(
        UserEmailConnection.user_id == user_id,
        UserEmailConnection.email_address == (email_address or "").strip().lower(),
    )
