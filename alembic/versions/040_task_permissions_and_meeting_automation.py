"""Add role permissions and pre-meeting automation settings

Revision ID: 040_task_perms_meeting_auto
Revises: 039_tldv_meeting_external_ids
Create Date: 2026-04-05 23:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "040_task_perms_meeting_auto"
down_revision = "039_tldv_meeting_external_ids"
branch_labels = None
depends_on = None


ROLE_PERMISSIONS_DEFAULT = (
    '{"ae":{"crm_import":false,"prospect_migration":true,"manage_team":false,"run_pre_meeting_intel":true},'
    '"sdr":{"crm_import":false,"prospect_migration":true,"manage_team":false,"run_pre_meeting_intel":false}}'
)

PRE_MEETING_AUTOMATION_DEFAULT = '{"enabled":true,"send_hours_before":12,"auto_generate_if_missing":true}'


def upgrade() -> None:
    op.add_column(
        "workspace_settings",
        sa.Column(
            "role_permissions",
            sa.JSON(),
            nullable=False,
            server_default=ROLE_PERMISSIONS_DEFAULT,
        ),
    )
    op.add_column(
        "workspace_settings",
        sa.Column(
            "pre_meeting_automation_settings",
            sa.JSON(),
            nullable=False,
            server_default=PRE_MEETING_AUTOMATION_DEFAULT,
        ),
    )
    op.add_column("meetings", sa.Column("intel_email_sent_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("meetings", "intel_email_sent_at")
    op.drop_column("workspace_settings", "pre_meeting_automation_settings")
    op.drop_column("workspace_settings", "role_permissions")
