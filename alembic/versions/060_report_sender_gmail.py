"""report sender gmail settings

Revision ID: 060
Revises: 059
Create Date: 2026-05-06
"""

from alembic import op
import sqlalchemy as sa


revision = "060"
down_revision = "059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace_settings", sa.Column("report_sender_email", sa.String(), nullable=True))
    op.add_column("workspace_settings", sa.Column("report_sender_connected_email", sa.String(), nullable=True))
    op.add_column("workspace_settings", sa.Column("report_sender_connected_at", sa.DateTime(), nullable=True))
    op.add_column("workspace_settings", sa.Column("report_sender_token_data", sa.JSON(), nullable=True))
    op.add_column("workspace_settings", sa.Column("report_sender_last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspace_settings", "report_sender_last_error")
    op.drop_column("workspace_settings", "report_sender_token_data")
    op.drop_column("workspace_settings", "report_sender_connected_at")
    op.drop_column("workspace_settings", "report_sender_connected_email")
    op.drop_column("workspace_settings", "report_sender_email")
