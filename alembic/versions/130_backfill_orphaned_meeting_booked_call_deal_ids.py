"""Backfill deal_id on orphaned meeting-booked call activities.

Revision ID: 130
Revises: 129
Create Date: 2026-09-04

When a rep logs a call with disposition demo_scheduled_booked or
meeting_confirmed, app.services.disposition_effects._maybe_suggest_deal_from_disposition
raises a bell alert (meeting_booked_suggest_deal). Accepting it either creates
a new deal or points at an existing one -- but until the fix that ships
alongside this migration, neither path wrote the resulting deal's id back
onto the call Activity that triggered the alert. That Activity's deal_id
stayed NULL forever, so app.services.us_pod_call_report._build_call_detail_rows
could never resolve a "Meeting date" for it (it only trusts activity.deal_id,
never re-matches by contact/company at report time) -- the report showed
"Pending" even when the deal it came from had a Date of Meeting set.

This repairs the historical orphans left behind, using the same
contact->deal linkage the disposition-effects code itself uses to detect
"does a deal already exist": DealContact first, then Deal.company_id ==
Contact.company_id. Matched only to a deal created within 7 days of the
call (the real-world gap is normally seconds), so a much later/earlier
unrelated deal on the same account is never picked. Written as a general
predicate rather than hard-coded ids so it heals any orphan regardless of
when it happened, and it is a no-op once every eligible call has a deal_id.
"""

from __future__ import annotations

from alembic import op


revision = "130"
down_revision = "129"
branch_labels = None
depends_on = None

_MATCH_WINDOW = "interval '7 days'"


def upgrade() -> None:
    # Pass 1: contact is directly linked to a deal via deal_contacts.
    op.execute(f"""
        UPDATE activities a
        SET deal_id = sub.deal_id
        FROM (
            SELECT DISTINCT ON (a2.id) a2.id AS activity_id, d.id AS deal_id
              FROM activities a2
              JOIN deal_contacts dc ON dc.contact_id = a2.contact_id
              JOIN deals d ON d.id = dc.deal_id
             WHERE a2.type = 'call'
               AND a2.deal_id IS NULL
               AND a2.contact_id IS NOT NULL
               AND a2.metadata->>'call_disposition' IN ('demo_scheduled_booked', 'meeting_confirmed')
               AND d.created_at >= a2.created_at
               AND d.created_at <= a2.created_at + {_MATCH_WINDOW}
             ORDER BY a2.id, d.created_at ASC
        ) sub
        WHERE a.id = sub.activity_id
    """)

    # Pass 2: no direct deal_contacts row (e.g. the deal was auto-created from
    # this exact call, so the contact link happened via DealContact at the
    # same moment -- covered above -- or via company match when it wasn't).
    # Fall back to the contact's company having a matching deal, same as the
    # disposition-effects "does a deal already exist" check.
    op.execute(f"""
        UPDATE activities a
        SET deal_id = sub.deal_id
        FROM (
            SELECT DISTINCT ON (a2.id) a2.id AS activity_id, d.id AS deal_id
              FROM activities a2
              JOIN contacts c ON c.id = a2.contact_id
              JOIN deals d ON d.company_id = c.company_id
             WHERE a2.type = 'call'
               AND a2.deal_id IS NULL
               AND a2.contact_id IS NOT NULL
               AND c.company_id IS NOT NULL
               AND a2.metadata->>'call_disposition' IN ('demo_scheduled_booked', 'meeting_confirmed')
               AND d.created_at >= a2.created_at
               AND d.created_at <= a2.created_at + {_MATCH_WINDOW}
             ORDER BY a2.id, d.created_at ASC
        ) sub
        WHERE a.id = sub.activity_id
    """)


def downgrade() -> None:
    # Cannot safely reverse -- we don't know which activities were NULL vs
    # intentionally set before this ran. Downgrade is a no-op.
    pass
