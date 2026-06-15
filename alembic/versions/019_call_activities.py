"""Add call-specific fields to activities table

Revision ID: 019
Revises: 018
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("activities", sa.Column("call_id", sa.String(), nullable=True))
    op.add_column("activities", sa.Column("call_duration", sa.Integer(), nullable=True))   # seconds
    op.add_column("activities", sa.Column("call_outcome", sa.String(), nullable=True))     # answered, missed, voicemail, failed
    op.add_column("activities", sa.Column("recording_url", sa.Text(), nullable=True))      # permanent internal URL after download
    op.add_column("activities", sa.Column("aircall_user_name", sa.String(), nullable=True))  # agent who made the call
    op.create_index("ix_activities_call_id", "activities", ["call_id"])


def downgrade():
    op.drop_index("ix_activities_call_id", "activities")
    op.drop_column("activities", "aircall_user_name")
    op.drop_column("activities", "recording_url")
    op.drop_column("activities", "call_outcome")
    op.drop_column("activities", "call_duration")
    op.drop_column("activities", "call_id")
