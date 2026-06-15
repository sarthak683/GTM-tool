"""Add commit_to_deal to deals and priority_tag to companies

Revision ID: 046_commit_to_deal_and_priority_tag
Revises: 045_contact_channel_tracking
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa

revision = "046"
down_revision = "045_contact_channel_tracking"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("deals", sa.Column("commit_to_deal", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("companies", sa.Column("priority_tag", sa.String(), nullable=True))


def downgrade():
    op.drop_column("deals", "commit_to_deal")
    op.drop_column("companies", "priority_tag")
