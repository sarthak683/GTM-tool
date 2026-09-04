"""Add outreach_sequences.instantly_sync_fingerprint.

Revision ID: 132
Revises: 131
Create Date: 2026-09-04

``sync_active_instantly_campaigns`` runs every 900 seconds and issues one
Instantly API call per linked sequence with no change detection, so it
re-fetched every lead on every pass. Production logs show the identical result
each run — ``synced: 731, campaigns_checked: 748, errors: 0`` — taking ~250
seconds, roughly a 28% duty cycle on a worker and ~70k API calls a day, to
discover that nothing had moved.

This column stores ``"<analytics fingerprint>:<UTC date>"`` from the last
completed lead sync. When a campaign's analytics payload is byte-identical to
the previous pass, no lead in it can have changed and the per-lead fetch is
skipped. The date suffix forces one full reconciliation per day regardless, so
a change the aggregate counters happen not to reflect is deferred by hours, not
indefinitely — and the Instantly webhook still delivers changes in real time.

Additive and nullable: existing rows read as "never fingerprinted" and take the
full path once, which is exactly the desired first-run behaviour.
"""

import sqlalchemy as sa
from alembic import op


revision = "132"
down_revision = "131"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("outreach_sequences") as batch:
        batch.add_column(sa.Column("instantly_sync_fingerprint", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("outreach_sequences") as batch:
        batch.drop_column("instantly_sync_fingerprint")
