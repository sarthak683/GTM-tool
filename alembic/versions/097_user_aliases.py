"""Add user_aliases table — alternate Google accounts that map to a primary CRM user.

Revision ID: 097
Revises: 096
Create Date: 2026-07-02

Allows team members who have multiple Google accounts (e.g. sipra@beacon.li
and sipra@beaconli.com) to log in with any of them and land in the same CRM
profile. Each row links an alternate google_id + email to the primary user.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "097"
down_revision = "096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("google_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_aliases_google_id", "user_aliases", ["google_id"], unique=True)
    op.create_index("ix_user_aliases_user_id", "user_aliases", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_aliases_google_id", table_name="user_aliases")
    op.drop_index("ix_user_aliases_user_id", table_name="user_aliases")
    op.drop_table("user_aliases")
