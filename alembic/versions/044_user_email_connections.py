"""add user_email_connections table for personal Gmail sync

Revision ID: 044_user_email_connections
Revises: 043_company_stage_milestones
Create Date: 2026-04-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "044_user_email_connections"
down_revision = "043_company_stage_milestones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_email_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email_address", sa.String(), nullable=False),
        sa.Column("token_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_sync_epoch", sa.Integer(), nullable=True),
        sa.Column("backfill_completed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("backfill_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("connected_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_email_connections_user_id"),
    )
    op.create_index(
        op.f("ix_user_email_connections_user_id"),
        "user_email_connections", ["user_id"], unique=True,
    )
    op.create_index(
        op.f("ix_user_email_connections_email_address"),
        "user_email_connections", ["email_address"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_email_connections_email_address"), table_name="user_email_connections")
    op.drop_index(op.f("ix_user_email_connections_user_id"), table_name="user_email_connections")
    op.drop_table("user_email_connections")
