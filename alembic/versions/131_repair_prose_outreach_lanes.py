"""Repair outreach lanes that hold LLM prose instead of a routing token.

Revision ID: 131
Revises: 130
Create Date: 2026-09-04

``companies.recommended_outreach_lane`` and ``contacts.outreach_lane`` are
short, indexed routing tokens. Every consumer matches them by equality:
playbook selection, the ``instantly_ready`` gate, the Account Sourcing lane
filter, and the prospect board's columns.

The ICP enrichment writer assigned ``recommended_outreach_strategy`` — free
prose describing "who to contact first, what to say, which channels" — straight
into that column, and ``account_sourcing`` then copied the company's value down
onto its contacts. A prose lane matches no branch, so routing silently stopped
working for those accounts while the lane filter grew sentence-long options.

Production carried 19 companies and 36 contacts in this state, against just two
legitimate values (``cold_strategic``, ``cold_operator``).

The code path is fixed in ``icp_intelligence`` (it now adopts a value only when
it really is a lane), but a rule change does not repair stored rows — hence
this backfill, in the same spirit as the ``engagement`` derivation fix.

Every cleared value is copied to ``outreach_lane_backup_131`` first, so this is
fully reversible AND the prose stays queryable. That matters: two of the
production rows read "Do not pursue - this is an exclude account", which is a
real exclusion decision someone should act on rather than lose. The full ICP
text also remains in ``companies.enrichment_cache -> 'icp_analysis'``.

Idempotent: re-running clears nothing extra and re-inserts no duplicates.
"""

from alembic import op


revision = "131"
down_revision = "130"
branch_labels = None
depends_on = None


# Canonical vocabulary — mirrors OUTREACH_LANES in app/services/account_sourcing.py.
# Anything outside this set is not a lane.
LANES = ("warm_intro", "event_follow_up", "cold_strategic", "cold_operator")
LANE_LIST = ", ".join(f"'{lane}'" for lane in LANES)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS outreach_lane_backup_131 (
            entity_type TEXT   NOT NULL,
            entity_id   UUID   NOT NULL,
            old_lane    TEXT   NOT NULL,
            PRIMARY KEY (entity_type, entity_id)
        )
        """
    )

    # Snapshot before clearing. ON CONFLICT keeps a re-run from erroring and
    # from overwriting the original value with a second, post-clear read.
    op.execute(
        f"""
        INSERT INTO outreach_lane_backup_131 (entity_type, entity_id, old_lane)
        SELECT 'company', id, recommended_outreach_lane
        FROM companies
        WHERE recommended_outreach_lane IS NOT NULL
          AND recommended_outreach_lane NOT IN ({LANE_LIST})
        ON CONFLICT (entity_type, entity_id) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO outreach_lane_backup_131 (entity_type, entity_id, old_lane)
        SELECT 'contact', id, outreach_lane
        FROM contacts
        WHERE outreach_lane IS NOT NULL
          AND outreach_lane NOT IN ({LANE_LIST})
        ON CONFLICT (entity_type, entity_id) DO NOTHING
        """
    )

    # Clear the non-lane values. NULL is the correct resting state: it means
    # "no lane decided yet", which is exactly true, and every consumer already
    # handles it (account_sourcing falls back to 'cold_operator' at send time).
    op.execute(
        f"""
        UPDATE companies
        SET recommended_outreach_lane = NULL
        WHERE recommended_outreach_lane IS NOT NULL
          AND recommended_outreach_lane NOT IN ({LANE_LIST})
        """
    )
    op.execute(
        f"""
        UPDATE contacts
        SET outreach_lane = NULL
        WHERE outreach_lane IS NOT NULL
          AND outreach_lane NOT IN ({LANE_LIST})
        """
    )


def downgrade() -> None:
    # Restore exactly the rows this migration cleared, and only those.
    op.execute(
        """
        UPDATE companies c
        SET recommended_outreach_lane = b.old_lane
        FROM outreach_lane_backup_131 b
        WHERE b.entity_type = 'company'
          AND b.entity_id = c.id
          AND c.recommended_outreach_lane IS NULL
        """
    )
    op.execute(
        """
        UPDATE contacts ct
        SET outreach_lane = b.old_lane
        FROM outreach_lane_backup_131 b
        WHERE b.entity_type = 'contact'
          AND b.entity_id = ct.id
          AND ct.outreach_lane IS NULL
        """
    )
    op.execute("DROP TABLE IF EXISTS outreach_lane_backup_131")
