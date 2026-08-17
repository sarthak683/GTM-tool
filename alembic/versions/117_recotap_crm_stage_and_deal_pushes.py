"""Separate the CRM journey stage from Recotap's, and track deal pushes

Revision ID: 117
Revises: 116
Create Date: 2026-08-17

Three changes, all in service of the Account Sourcing numbers being true.

1. ``recotap_accounts.crm_journey_stage``

   ``sync_crm_journey`` derived a journey stage from the company's most advanced
   deal and wrote it over ``journey_stage`` — the column that holds Recotap's own
   intent-derived stage — then flipped ``source`` to 'crm'. Recotap's value was
   destroyed on every refresh, and the Account Sourcing funnel, which carries a
   "Powered by Recotap" badge, was showing Beacon's own deal stages for 94 of 338
   scored accounts. All 22 accounts in the "Customer" tile were CRM-derived;
   Recotap itself reported zero Customers. Two facts, one column, second writer
   wins silently.

   The CRM-derived stage now has its own home. ``journey_stage`` means Recotap
   and only Recotap. The backfill below recovers Recotap's real value from
   ``raw->>'rtp_journey_stage'``, which the pull has been storing all along, so
   no data is lost to the split.

2. ``recotap_deal_pushes``

   Beacon → Recotap previously pushed only account tags. Recotap's
   ``POST /api/v1/deals`` takes the whole deal — amount, stage, pipeline, owner,
   dates, associated accounts — which is what their revenue attribution actually
   needs. This table records what we last sent for each deal so the daily push
   can skip deals whose payload has not changed; 689 live deals re-sent in full
   every night would be pure waste, and Recotap's batch limit is 100.

   ``payload_hash`` is over the exact JSON body sent, so any field change
   (including a stage label edited in Settings) re-pushes.

3. UNIQUE index on ``recotap_accounts.domain``

   ``_get_or_create_row`` looks a row up by domain with ``scalar_one_or_none()``,
   which raises MultipleResultsFound on a duplicate. Nothing at the DB level
   prevented one: two concurrent refreshes could each miss and each insert, and
   from then on *every* refresh would crash. Prod has zero duplicates today, so
   the constraint is free to add — and this release adds a scheduled sync that
   can overlap a user clicking "Sync Recotap", which is exactly the race.

Junk cleanup: ``sync_crm_journey`` created rows for placeholder domains
(``vistex.unknown``, ``charles-river-development.unknown``, …) because, unlike
the push path, it never checked ``is_pushable_domain``. Those domains cannot
exist in Recotap. Five such rows in prod double-counted their company in the
funnel. They are deleted here — the predicate is narrow enough to prove they
were never real: a placeholder TLD, never assigned an rtp_aid, never pushed.
"""
from alembic import op
import sqlalchemy as sa

revision = "117"
down_revision = "116"
branch_labels = None
depends_on = None


# Kept in sync with app.services.recotap._PLACEHOLDER_TLDS.
_PLACEHOLDER_TLDS = (
    "unknown", "local", "invalid", "test", "example",
    "internal", "none", "null", "localhost",
)


def upgrade() -> None:
    op.add_column(
        "recotap_accounts",
        sa.Column("crm_journey_stage", sa.String(), nullable=True),
    )

    # Move the CRM-derived stage into its own column, then restore Recotap's real
    # stage from the raw payload the pull has always kept. Rows marked source='crm'
    # are exactly the ones sync_crm_journey overwrote.
    op.execute(
        """
        UPDATE recotap_accounts
           SET crm_journey_stage = journey_stage,
               journey_stage     = NULLIF(raw->>'rtp_journey_stage', '')
         WHERE source = 'crm'
        """
    )
    # 'crm' was never a provenance for the row — it was a marker that the stage
    # had been overwritten. Provenance is 'recotap' if we ever pulled it, else
    # 'pending'. Keeping 'crm' would leave the source column lying about where
    # the row came from now that the stage lives elsewhere.
    op.execute(
        """
        UPDATE recotap_accounts
           SET source = CASE WHEN pulled_at IS NOT NULL THEN 'recotap' ELSE 'pending' END
         WHERE source = 'crm'
        """
    )

    placeholders = ", ".join(f"'{tld}'" for tld in _PLACEHOLDER_TLDS)
    op.execute(
        f"""
        DELETE FROM recotap_accounts
         WHERE rtp_aid IS NULL
           AND pushed_at IS NULL
           AND lower(split_part(domain, '.', array_length(string_to_array(domain, '.'), 1)))
               IN ({placeholders})
        """
    )

    # Defensive: the UNIQUE index below fails outright if a duplicate slipped in
    # on an environment other than prod. Keep the most recently updated row.
    op.execute(
        """
        DELETE FROM recotap_accounts a
         USING recotap_accounts b
         WHERE a.domain = b.domain
           AND (a.updated_at, a.id) < (b.updated_at, b.id)
        """
    )
    op.drop_index("ix_recotap_accounts_domain", table_name="recotap_accounts")
    op.create_index(
        "ix_recotap_accounts_domain", "recotap_accounts", ["domain"], unique=True
    )

    op.create_table(
        "recotap_deal_pushes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deal_id", sa.Uuid(), nullable=False),
        # What we sent as externalDealId. Stored rather than derived so a future
        # change to the id scheme stays diagnosable against Recotap's side.
        sa.Column("external_deal_id", sa.String(), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("pushed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # ON DELETE CASCADE: push state is meaningless without its deal, and a
        # hard-deleted deal should not be able to block the delete. (Ordinary
        # deletes are soft — deleted_at — and keep their row so we can tell
        # Recotap the deal is gone.)
        sa.ForeignKeyConstraint(
            ["deal_id"], ["deals.id"], ondelete="CASCADE",
        ),
        # One push-state row per deal — this is the dedup backstop, not just an
        # index. The service upserts on it.
        sa.UniqueConstraint("deal_id", name="uq_recotap_deal_pushes_deal_id"),
    )
    op.create_index(
        "ix_recotap_deal_pushes_pushed_at", "recotap_deal_pushes", ["pushed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_recotap_deal_pushes_pushed_at", table_name="recotap_deal_pushes")
    op.drop_table("recotap_deal_pushes")

    op.drop_index("ix_recotap_accounts_domain", table_name="recotap_accounts")
    op.create_index(
        "ix_recotap_accounts_domain", "recotap_accounts", ["domain"], unique=False
    )

    # Fold the CRM stage back over Recotap's, restoring the old single-column
    # behaviour. The deleted placeholder rows are not recoverable.
    op.execute(
        """
        UPDATE recotap_accounts
           SET journey_stage = crm_journey_stage,
               source        = 'crm'
         WHERE crm_journey_stage IS NOT NULL
        """
    )
    op.drop_column("recotap_accounts", "crm_journey_stage")
