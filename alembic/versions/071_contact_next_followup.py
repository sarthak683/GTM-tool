"""Add next_followup_at column to contacts.

Revision ID: 071
Revises: 070
Create Date: 2026-05-27

Stores the rep-selected follow-up timestamp for call dispositions like
"interested_follow_up_required" / "call_back_later_rescheduled". Drives the
follow-up date label on the prospect-page progress dots.
"""

from alembic import op
import sqlalchemy as sa


revision = "071"
down_revision = "070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column(
            "next_followup_at",
            sa.DateTime(),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_contacts_next_followup_at"),
        "contacts",
        ["next_followup_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_contacts_next_followup_at"),
        table_name="contacts",
    )
    op.drop_column("contacts", "next_followup_at")
