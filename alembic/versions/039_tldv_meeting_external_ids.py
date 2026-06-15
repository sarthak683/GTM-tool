"""Add tl;dv external identifiers to meetings

Revision ID: 039_tldv_meeting_external_ids
Revises: 038_merge_open_into_reprospect
Create Date: 2026-04-05 17:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "039_tldv_meeting_external_ids"
down_revision = "038_merge_open_into_reprospect"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("external_source", sa.String(), nullable=True))
    op.add_column("meetings", sa.Column("external_source_id", sa.String(), nullable=True))
    op.add_column("meetings", sa.Column("meeting_url", sa.Text(), nullable=True))
    op.add_column("meetings", sa.Column("recording_url", sa.Text(), nullable=True))
    op.create_index("ix_meetings_external_source", "meetings", ["external_source"], unique=False)
    op.create_index("ix_meetings_external_source_id", "meetings", ["external_source_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_meetings_external_source_id", table_name="meetings")
    op.drop_index("ix_meetings_external_source", table_name="meetings")
    op.drop_column("meetings", "recording_url")
    op.drop_column("meetings", "meeting_url")
    op.drop_column("meetings", "external_source_id")
    op.drop_column("meetings", "external_source")
