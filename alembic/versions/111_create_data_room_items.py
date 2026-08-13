"""Create data_room_items

Revision ID: 111
Revises: 110
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op

revision = "111"
down_revision = "110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_room_items",
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("embed_url", sa.Text(), nullable=False),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_by_id",
            sa.Uuid(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_data_room_items_category", "data_room_items", ["category"])
    op.create_index("ix_data_room_items_created_by_id", "data_room_items", ["created_by_id"])


def downgrade() -> None:
    op.drop_index("ix_data_room_items_created_by_id", table_name="data_room_items")
    op.drop_index("ix_data_room_items_category", table_name="data_room_items")
    op.drop_table("data_room_items")
