"""Add workspace_settings.prospect_view_all_user_ids — per-user "see all prospects" grants.

Revision ID: 081
Revises: 080
Create Date: 2026-06-04

Prospects are now visibility-scoped: a non-admin sees only their own +
unassigned prospects. Admins can grant specific non-admins broader access
("see all prospects"); this nullable JSON column stores those granted user ids.

Additive and nullable — the existing workspace_settings row simply gets NULL,
no data is read or rewritten.
"""
from alembic import op
import sqlalchemy as sa


revision = "081"
down_revision = "080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_settings",
        sa.Column("prospect_view_all_user_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace_settings", "prospect_view_all_user_ids")
