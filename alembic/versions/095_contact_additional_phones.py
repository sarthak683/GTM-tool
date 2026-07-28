"""Add additional_phones to contacts (extra phone numbers beyond primary).

Revision ID: 095
Revises: 094
Create Date: 2026-06-26

The existing single `phone` column stays the PRIMARY number (used for Aircall
dialing, push-to-call, search, and enrichment). `additional_phones` is a nullable
JSONB list of extra numbers, shape: [{"number": str, "label": str?}].
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "095"
down_revision = "094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("additional_phones", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contacts", "additional_phones")
