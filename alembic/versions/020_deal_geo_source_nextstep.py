"""Add geography, source, next_step to deals."""

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("deals", sa.Column("geography", sa.String(), nullable=True))
    op.add_column("deals", sa.Column("source", sa.String(), nullable=True))
    op.add_column("deals", sa.Column("next_step", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("deals", "next_step")
    op.drop_column("deals", "source")
    op.drop_column("deals", "geography")
