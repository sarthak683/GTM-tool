"""Add synced_by_user_id and synced_at to meetings

Revision ID: 048
Revises: 047
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "meetings",
        sa.Column("synced_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "meetings",
        sa.Column("synced_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_meetings_synced_by_user_id", "meetings", ["synced_by_user_id"])
    op.create_foreign_key(
        "fk_meetings_synced_by_user_id_users",
        "meetings",
        "users",
        ["synced_by_user_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint("fk_meetings_synced_by_user_id_users", "meetings", type_="foreignkey")
    op.drop_index("ix_meetings_synced_by_user_id", table_name="meetings")
    op.drop_column("meetings", "synced_at")
    op.drop_column("meetings", "synced_by_user_id")
