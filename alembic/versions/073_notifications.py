"""Add notifications table for in-app bell.

Revision ID: 073
Revises: 072
Create Date: 2026-05-27

Distinct from `tasks` on purpose: a Task is durable work a rep owes
(backlog, ownership, due dates). A Notification is a signal the system
noticed *for* the rep that decays once acknowledged. First consumer is
"meeting booked — create deal?" suggestions from the reply-sentiment
classifier; future consumers slot in by adding a new `type` value and a
per-type accept dispatch in app/api/v1/endpoints/notifications.py.

Dedup: each (user_id, dedup_key) pair is unique so a re-delivered webhook
or a re-classified reply can't spawn duplicate notifications.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "073"
down_revision = "072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("action_payload", postgresql.JSONB(), nullable=True),
        # Idempotency anchor. e.g. "meeting_booked:<contact_id>:<message_id>".
        # Re-delivery of the same webhook returns the existing row instead
        # of creating a duplicate.
        sa.Column("dedup_key", sa.String(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_type", "notifications", ["type"])
    # Cheap "what's unread for this user" query — drives the bell badge.
    op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "read_at"])
    op.create_unique_constraint(
        "uq_notifications_user_dedup",
        "notifications",
        ["user_id", "dedup_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_notifications_user_dedup", "notifications", type_="unique")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index("ix_notifications_type", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")
