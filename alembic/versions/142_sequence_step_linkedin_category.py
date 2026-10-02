"""Add sequence_steps.linkedin_category.

A build-time label for a LinkedIn step (dm / follow_up / inmail) — purely
descriptive, pre-selects the reused "Log LinkedIn touch" dialog's outcome
dropdown; doesn't change completion behavior.

Revision ID: 142
Revises: 141
Create Date: 2026-09-24
"""
import sqlalchemy as sa
from alembic import op

revision = "142"
down_revision = "141"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sequence_steps", sa.Column("linkedin_category", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("sequence_steps", "linkedin_category")
