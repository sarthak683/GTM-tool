"""Add workspace_settings.deal_view_all_user_ids — per-user "see all deals" grants.

Revision ID: 096
Revises: 095
Create Date: 2026-06-30

Deals are visibility-scoped: a non-admin sees only deals they own (AE or SDR).
Admins can grant specific non-admins broader access ("see the entire team's
pipeline"); this nullable JSON column stores those granted user ids. Mirrors
prospect_view_all_user_ids (migration 081).

Additive and nullable — the existing workspace_settings row simply gets NULL,
no data is read or rewritten.
"""
from alembic import op
import sqlalchemy as sa


revision = "096"
down_revision = "095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_settings",
        sa.Column("deal_view_all_user_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace_settings", "deal_view_all_user_ids")
