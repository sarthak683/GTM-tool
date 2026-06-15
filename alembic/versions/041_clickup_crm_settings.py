"""Add ClickUp CRM settings to workspace settings

Revision ID: 041_clickup_crm_settings
Revises: 040_task_permissions_and_meeting_automation
Create Date: 2026-04-06
"""

from alembic import op
import sqlalchemy as sa


revision = "041_clickup_crm_settings"
down_revision = "040_task_perms_meeting_auto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace_settings", sa.Column("clickup_crm_settings", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspace_settings", "clickup_crm_settings")
