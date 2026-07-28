"""Track company SDR reassignment timestamp

Revision ID: 106
Revises: 105
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa


revision = "106"
down_revision = "105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("sdr_assigned_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "sdr_assigned_at")
