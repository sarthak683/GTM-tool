"""allow multiple Gmail connections per user

Replace the unique constraint on user_id alone with a composite unique on
(user_id, email_address). This lets each rep connect a second inbox
(e.g. their @beaconli.com Instantly account) without replacing the first.

The sync task already iterates all active rows -- no changes needed there.

Revision ID: 104_multi_gmail_connections
Revises: 103_opportunity_details
Create Date: 2026-07-21
"""

from alembic import op

revision = "104_multi_gmail_connections"
down_revision = "103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the single-column unique constraint that limits one row per user.
    op.drop_constraint(
        "uq_user_email_connections_user_id",
        "user_email_connections",
        type_="unique",
    )
    op.drop_index(
        "ix_user_email_connections_user_id",
        table_name="user_email_connections",
    )
    op.create_index(
        "ix_user_email_connections_user_id",
        "user_email_connections",
        ["user_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_user_email_connections_user_email",
        "user_email_connections",
        ["user_id", "email_address"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_user_email_connections_user_email",
        "user_email_connections",
        type_="unique",
    )
    op.drop_index(
        "ix_user_email_connections_user_id",
        table_name="user_email_connections",
    )
    op.create_index(
        "ix_user_email_connections_user_id",
        "user_email_connections",
        ["user_id"],
        unique=True,
    )
    op.create_unique_constraint(
        "uq_user_email_connections_user_id",
        "user_email_connections",
        ["user_id"],
    )
