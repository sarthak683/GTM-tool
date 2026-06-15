"""add sync_schedule_settings to workspace_settings"""

from alembic import op
import sqlalchemy as sa

revision = "042_sync_schedule_settings"
down_revision = "041_prospect_stage_settings"
branch_labels = None
depends_on = None

DEFAULT = '{"tldv_sync_hour":3,"tldv_sync_enabled":true,"tldv_page_size":20,"tldv_max_pages":3,"email_sync_interval_seconds":180,"deal_health_hour":2}'


def upgrade() -> None:
    op.add_column(
        "workspace_settings",
        sa.Column("sync_schedule_settings", sa.JSON(), nullable=False, server_default=DEFAULT),
    )


def downgrade() -> None:
    op.drop_column("workspace_settings", "sync_schedule_settings")
