"""Add email_bcc column to activities table.

Revision ID: 099
Revises: 098
Create Date: 2026-07-14

Captures the BCC recipients of outbound Gmail emails so analytics can
correctly classify beacon.li emails as "Emails Out" when Zippy is BCC'd.
Historical rows will have NULL — only new emails synced after this migration
will have the field populated.
"""
from alembic import op
import sqlalchemy as sa


revision = "099"
down_revision = "098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("activities", sa.Column("email_bcc", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("activities", "email_bcc")
