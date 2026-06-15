"""deal next_step_updated_at

Adds a dedicated timestamp that tracks when the deal's next_step note
itself was last rewritten — distinct from last_activity_at (which moves
on any activity) or updated_at (which moves on any column write). This
lets the pipeline card show "is this note fresh?" honestly.

Revision ID: 061
Revises: 060
Create Date: 2026-05-07
"""

from alembic import op
import sqlalchemy as sa


revision = "061"
down_revision = "060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deals",
        sa.Column("next_step_updated_at", sa.DateTime(), nullable=True),
    )
    # Backfill: for deals that already have a next_step, seed the new
    # column with last_activity_at (best available proxy) so existing
    # cards have a date on day one rather than "—".
    op.execute(
        """
        UPDATE deals
        SET next_step_updated_at = COALESCE(last_activity_at, updated_at)
        WHERE next_step IS NOT NULL AND btrim(next_step) <> ''
        """
    )


def downgrade() -> None:
    op.drop_column("deals", "next_step_updated_at")
