"""Add owner_user_id to meetings

Revision ID: 047
Revises: 046_commit_to_deal_and_priority_tag
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "meetings",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_meetings_owner_user_id", "meetings", ["owner_user_id"])
    op.create_foreign_key(
        "fk_meetings_owner_user_id_users",
        "meetings",
        "users",
        ["owner_user_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint("fk_meetings_owner_user_id_users", "meetings", type_="foreignkey")
    op.drop_index("ix_meetings_owner_user_id", table_name="meetings")
    op.drop_column("meetings", "owner_user_id")
