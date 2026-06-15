"""add drive folder selection columns to user_email_connections

Revision ID: 049
Revises: 048
Create Date: 2026-04-19
"""

from alembic import op
import sqlalchemy as sa


revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_email_connections",
        sa.Column("selected_drive_folder_id", sa.String(), nullable=True),
    )
    op.add_column(
        "user_email_connections",
        sa.Column("selected_drive_folder_name", sa.String(), nullable=True),
    )
    op.add_column(
        "user_email_connections",
        sa.Column(
            "is_admin_folder",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.create_index(
        op.f("ix_user_email_connections_selected_drive_folder_id"),
        "user_email_connections",
        ["selected_drive_folder_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_user_email_connections_selected_drive_folder_id"),
        table_name="user_email_connections",
    )
    op.drop_column("user_email_connections", "is_admin_folder")
    op.drop_column("user_email_connections", "selected_drive_folder_name")
    op.drop_column("user_email_connections", "selected_drive_folder_id")
