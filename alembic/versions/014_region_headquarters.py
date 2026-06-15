"""add region and headquarters columns to companies

Revision ID: 014
Revises: 013
Create Date: 2026-03-23
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("region", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("headquarters", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "headquarters")
    op.drop_column("companies", "region")
