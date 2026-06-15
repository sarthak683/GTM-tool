"""Allow call recordings on a deal without a linked contact.

Revision ID: 076
Revises: 075
Create Date: 2026-05-30

AEs record calls from the deal detail page, but most deals have no linked
contact — and the recorder required one, so it was a dead end on 97% of deals.
This makes `contact_id` nullable and adds an optional `deal_id`, so a recording
can attach to the deal directly (with or without a contact).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "076"
down_revision = "075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("call_recordings", sa.Column("deal_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_call_recordings_deal_id", "call_recordings", ["deal_id"])
    op.alter_column("call_recordings", "contact_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    # Note: contact_id can't be made NOT NULL again if any rows have null — leave nullable on downgrade.
    op.drop_index("ix_call_recordings_deal_id", table_name="call_recordings")
    op.drop_column("call_recordings", "deal_id")
