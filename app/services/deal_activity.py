"""One definition of "activity that counts for a deal".

Why this module exists
----------------------
Reps log calls against a *contact* (the call disposition drawer, Aircall, the
LinkedIn/WhatsApp buttons) — none of those write ``Activity.deal_id``. Only the
email pipeline and the system bookkeeping writers set it. Measured on production
on 2026-08-15, of 13,139 calls in the previous 60 days exactly **24** carried a
``deal_id``, while 3,849 of them were logged against a contact that
``deal_contacts`` links to a deal.

Everything that asked "when was this deal last touched?" filtered on
``Activity.deal_id`` alone, so it saw ~0.4% of the real call volume:

- ``recalculate_all_deal_health`` marked deals red for inactivity that a rep had
  called the day before (22 "red" deals had a stakeholder touch inside 7 days),
- ``Deal.last_activity_at`` stayed NULL on 76 deals that did have touches,
- ``build_deal_timeline`` rendered a deal timeline with the calls missing,
- the AI task generator reasoned from a near-empty activity list.

The second half of the problem is the opposite error: of the 8,324 activities
that *do* carry a ``deal_id``, 2,388 are system bookkeeping (``field_change``,
``stage_change``, ``deal_created``, ``contact_linked``, ``qualification_update``).
Counting those as engagement makes a deal look freshly worked when all that
happened was an automated stage recalculation.

So "counts as a touch" means both of:
  1. the activity reaches the deal directly *or* through a linked stakeholder, and
  2. it is a human touch type, not system bookkeeping.

``app.services.tasks`` already used the correct type list; this module makes it
the shared definition rather than one caller's local opinion.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select

from app.models.activity import Activity
from app.models.deal import DealContact

# Human touches. Deliberately excludes field_change / stage_change /
# deal_created / contact_linked / qualification_update — those are written by
# the system about the deal, not work done on it.
ENGAGEMENT_ACTIVITY_TYPES: tuple[str, ...] = (
    "call",
    "email",
    "linkedin",
    "whatsapp",
    "meeting",
    "transcript",
    "note",
    "comment",
)


def deal_activity_condition(deal_id: UUID) -> ColumnElement[bool]:
    """Match activities on this deal directly or via one of its stakeholders.

    Used as a WHERE clause against ``Activity``. The stakeholder arm is a
    subquery rather than a join so callers can drop it into an existing
    ``select(Activity)`` without changing their result shape or row count — a
    join against ``deal_contacts`` would duplicate an activity once per link.
    """
    return or_(
        Activity.deal_id == deal_id,
        Activity.contact_id.in_(
            select(DealContact.contact_id).where(DealContact.deal_id == deal_id)
        ),
    )


def engagement_only() -> ColumnElement[bool]:
    """Restrict to human touches (see ENGAGEMENT_ACTIVITY_TYPES)."""
    return Activity.type.in_(ENGAGEMENT_ACTIVITY_TYPES)


async def latest_touch_by_deal(
    session, deal_ids, *, engagement_types_only: bool = False
) -> dict[UUID, "object"]:
    """Map deal_id -> timestamp of its most recent activity.

    Answers for many deals in two grouped queries instead of per-deal lookups,
    because ``recalculate_all_deal_health`` runs this across every open deal.
    The two arms (direct + via stakeholder) are aggregated separately and then
    merged with ``max``; expressing it as one OR'd query would force a scan that
    cannot use either index.

    ``engagement_types_only`` defaults to **False**, which keeps the historical
    definition of "touched" (any activity row). That default is deliberate.
    Measured against production on 2026-08-15, adding the stakeholder arm alone
    moved 83 of 601 open deals to a fresher last-touch — 16.5 days fresher on
    average — and took nothing away from any deal. Adding the type filter on top
    of it took the *only* activity away from 385 of those 601 deals, whose sole
    history is field_change / stage_change / import_note rows. Since engagement
    recency is 40 of the 100 health points, flipping that on would turn about
    two-thirds of the open pipeline red overnight.

    That may well be the honest number — a stage recalculation is not a touch —
    but it is a change to what "health" means, not a bug fix, so it is opt-in
    until someone decides it. The two corrections are independent: the
    stakeholder arm is always on.
    """
    deal_ids = [d for d in deal_ids if d is not None]
    if not deal_ids:
        return {}

    type_filter = [engagement_only()] if engagement_types_only else []
    latest: dict[UUID, object] = {}

    direct = await session.execute(
        select(Activity.deal_id, func.max(Activity.created_at))
        .where(Activity.deal_id.in_(deal_ids), *type_filter)
        .group_by(Activity.deal_id)
    )
    for deal_id, ts in direct.all():
        latest[deal_id] = ts

    via_contact = await session.execute(
        select(DealContact.deal_id, func.max(Activity.created_at))
        .join(Activity, Activity.contact_id == DealContact.contact_id)
        .where(DealContact.deal_id.in_(deal_ids), *type_filter)
        .group_by(DealContact.deal_id)
    )
    for deal_id, ts in via_contact.all():
        current = latest.get(deal_id)
        if ts is not None and (current is None or ts > current):
            latest[deal_id] = ts

    return latest
