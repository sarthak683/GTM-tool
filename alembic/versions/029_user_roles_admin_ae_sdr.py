"""Normalize user roles to admin/ae/sdr."""

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("UPDATE users SET role = 'sdr' WHERE role = 'sales_rep'")


def downgrade() -> None:
    op.execute("UPDATE users SET role = 'sales_rep' WHERE role = 'sdr'")
