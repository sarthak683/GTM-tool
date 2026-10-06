"""Event tags on contacts and companies.

Revision ID: 144
Revises: 143
Create Date: 2026-10-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "144"
down_revision = "143"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("contacts", "companies"):
        op.add_column(
            table,
            sa.Column("events", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        )
        op.create_index(f"ix_{table}_events", table, ["events"], postgresql_using="gin")


def downgrade() -> None:
    for table in ("contacts", "companies"):
        op.drop_index(f"ix_{table}_events", table_name=table)
        op.drop_column(table, "events")
