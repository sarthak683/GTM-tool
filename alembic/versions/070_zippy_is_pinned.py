"""Add is_pinned column to zippy_conversations.

Revision ID: 070
Revises: 069
Create Date: 2026-05-26

Lets users pin frequently-used conversations to the top of the Zippy
session list. Defaults to FALSE so existing rows behave as before.
"""

from alembic import op
import sqlalchemy as sa


revision = "070"
down_revision = "069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "zippy_conversations",
        sa.Column(
            "is_pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        op.f("ix_zippy_conversations_is_pinned"),
        "zippy_conversations",
        ["is_pinned"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_zippy_conversations_is_pinned"),
        table_name="zippy_conversations",
    )
    op.drop_column("zippy_conversations", "is_pinned")
