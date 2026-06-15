"""Add per-channel tracking fields to contacts

Revision ID: 045_contact_channel_tracking
Revises: 044_user_email_connections
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa

revision = "045_contact_channel_tracking"
down_revision = "044_user_email_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("email_open_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("contacts", sa.Column("email_click_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("contacts", sa.Column("email_last_opened_at", sa.DateTime(), nullable=True))
    op.add_column("contacts", sa.Column("call_status", sa.String(), nullable=True))
    op.add_column("contacts", sa.Column("call_disposition", sa.String(), nullable=True))
    op.add_column("contacts", sa.Column("call_notes", sa.String(), nullable=True))
    op.add_column("contacts", sa.Column("call_last_at", sa.DateTime(), nullable=True))
    op.add_column("contacts", sa.Column("linkedin_status", sa.String(), nullable=True))
    op.add_column("contacts", sa.Column("linkedin_last_at", sa.DateTime(), nullable=True))
    op.add_column("contacts", sa.Column("timezone", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("contacts", "timezone")
    op.drop_column("contacts", "linkedin_last_at")
    op.drop_column("contacts", "linkedin_status")
    op.drop_column("contacts", "call_last_at")
    op.drop_column("contacts", "call_notes")
    op.drop_column("contacts", "call_disposition")
    op.drop_column("contacts", "call_status")
    op.drop_column("contacts", "email_last_opened_at")
    op.drop_column("contacts", "email_click_count")
    op.drop_column("contacts", "email_open_count")
