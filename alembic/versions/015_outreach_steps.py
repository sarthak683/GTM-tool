"""Add outreach_steps table and instantly tracking fields to outreach_sequences

Revision ID: 015
Revises: 014
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── New outreach_steps table ───────────────────────────────────────────────
    op.create_table(
        "outreach_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "sequence_id",
            UUID(as_uuid=True),
            sa.ForeignKey("outreach_sequences.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("step_number", sa.Integer, nullable=False),
        sa.Column("subject", sa.Text, nullable=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("delay_value", sa.Integer, nullable=False, server_default="0"),
        sa.Column("delay_unit", sa.String(20), nullable=False, server_default="Days"),
        sa.Column("variants", JSONB, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # ── New columns on outreach_sequences ──────────────────────────────────────
    op.add_column(
        "outreach_sequences",
        sa.Column("instantly_campaign_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "outreach_sequences",
        sa.Column("instantly_campaign_status", sa.String(50), nullable=True),
    )
    op.add_column(
        "outreach_sequences",
        sa.Column("launched_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("outreach_sequences", "launched_at")
    op.drop_column("outreach_sequences", "instantly_campaign_status")
    op.drop_column("outreach_sequences", "instantly_campaign_id")
    op.drop_table("outreach_steps")
