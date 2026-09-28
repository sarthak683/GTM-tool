"""Rename sequence_steps.day_offset -> delay_minutes (minute granularity).

The builder now offers an amount + unit (Days / Hours / Min) picker instead
of a calendar date, so the stored value needs minute precision, not whole
days. Existing values (whole days) are converted to minutes on the way in.

Revision ID: 143
Revises: 142
Create Date: 2026-09-24
"""
import sqlalchemy as sa
from alembic import op

revision = "143"
down_revision = "142"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sequence_steps", sa.Column("delay_minutes", sa.Integer(), nullable=True))
    op.execute("UPDATE sequence_steps SET delay_minutes = day_offset * 1440")
    op.alter_column("sequence_steps", "delay_minutes", nullable=False, server_default="0")
    op.drop_column("sequence_steps", "day_offset")


def downgrade() -> None:
    op.add_column("sequence_steps", sa.Column("day_offset", sa.Integer(), nullable=True))
    op.execute("UPDATE sequence_steps SET day_offset = delay_minutes / 1440")
    op.alter_column("sequence_steps", "day_offset", nullable=False, server_default="0")
    op.drop_column("sequence_steps", "delay_minutes")
