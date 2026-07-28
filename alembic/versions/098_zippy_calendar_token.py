"""Add zippy_calendar OAuth token fields to workspace_settings.

Revision ID: 098
Revises: 097
Create Date: 2026-07-10

Stores the OAuth refresh token for zippy@beacon.li's Google Calendar so the
daily AE meeting reminder Celery task can fetch tomorrow's meetings without
requiring a service account.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "098"
down_revision = "097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace_settings", sa.Column("zippy_calendar_connected_email", sa.String(), nullable=True))
    op.add_column("workspace_settings", sa.Column("zippy_calendar_connected_at", sa.DateTime(), nullable=True))
    op.add_column("workspace_settings", sa.Column("zippy_calendar_token_data", JSONB(), nullable=True))
    op.add_column("workspace_settings", sa.Column("zippy_calendar_last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspace_settings", "zippy_calendar_last_error")
    op.drop_column("workspace_settings", "zippy_calendar_token_data")
    op.drop_column("workspace_settings", "zippy_calendar_connected_at")
    op.drop_column("workspace_settings", "zippy_calendar_connected_email")
