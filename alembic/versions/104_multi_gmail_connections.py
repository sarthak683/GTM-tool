"""allow multiple Gmail connections per user (reverted — kept for alembic history)

This migration ran and added a composite unique on (user_id, email_address).
The feature was subsequently reverted at the application layer; the DB schema
change is left in place since it is backwards-compatible with single-inbox use.

Revision ID: 104_multi_gmail_connections
Revises: 103
Create Date: 2026-07-21
"""
from alembic import op

revision = "104_multi_gmail_connections"
down_revision = "103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Already applied — no-op on re-run (idempotent guard via try/except).
    try:
        op.drop_constraint(
            "uq_user_email_connections_user_id",
            "user_email_connections",
            type_="unique",
        )
    except Exception:
        pass
    try:
        op.drop_index(
            "ix_user_email_connections_user_id",
            table_name="user_email_connections",
        )
    except Exception:
        pass
    try:
        op.create_index(
            "ix_user_email_connections_user_id",
            "user_email_connections",
            ["user_id"],
            unique=False,
        )
    except Exception:
        pass
    try:
        op.create_unique_constraint(
            "uq_user_email_connections_user_email",
            "user_email_connections",
            ["user_id", "email_address"],
        )
    except Exception:
        pass


def downgrade() -> None:
    try:
        op.drop_constraint(
            "uq_user_email_connections_user_email",
            "user_email_connections",
            type_="unique",
        )
    except Exception:
        pass
    try:
        op.drop_index(
            "ix_user_email_connections_user_id",
            table_name="user_email_connections",
        )
    except Exception:
        pass
    try:
        op.create_index(
            "ix_user_email_connections_user_id",
            "user_email_connections",
            ["user_id"],
            unique=True,
        )
    except Exception:
        pass
    try:
        op.create_unique_constraint(
            "uq_user_email_connections_user_id",
            "user_email_connections",
            ["user_id"],
        )
    except Exception:
        pass
