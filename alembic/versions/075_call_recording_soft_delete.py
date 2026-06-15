"""Add soft-delete tracking to call_recordings.

Revision ID: 075
Revises: 074
Create Date: 2026-05-29

Deleting a recording is a soft-delete so it stays auditable: we record who
deleted it and when, and exclude deleted rows from the recording lists.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "075"
down_revision = "074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("call_recordings", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("call_recordings", sa.Column("deleted_by_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("call_recordings", "deleted_by_id")
    op.drop_column("call_recordings", "deleted_at")
