"""Add deals.sdr_id so SDR-sourced pipeline credit survives conversion.

Revision ID: 077
Revises: 076
Create Date: 2026-05-31

When a prospect converts to a deal, the originating SDR was dropped — only the
AE (`assigned_to_id`) carried over. That erased SDR-sourced-pipeline credit at
the exact moment it matters. This adds a nullable `sdr_id` on deals, mirroring
`assigned_to_id`, stamped from the contact's `sdr_id` at conversion.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "077"
down_revision = "076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deals", sa.Column("sdr_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_deals_sdr_id", "deals", ["sdr_id"])


def downgrade() -> None:
    op.drop_index("ix_deals_sdr_id", table_name="deals")
    op.drop_column("deals", "sdr_id")
