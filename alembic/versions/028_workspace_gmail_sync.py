"""Add Gmail sync fields to workspace settings."""

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    op.add_column("workspace_settings", sa.Column("gmail_shared_inbox", sa.String(), nullable=True))
    op.add_column("workspace_settings", sa.Column("gmail_connected_email", sa.String(), nullable=True))
    op.add_column("workspace_settings", sa.Column("gmail_connected_at", sa.DateTime(), nullable=True))
    op.add_column("workspace_settings", sa.Column("gmail_token_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("workspace_settings", sa.Column("gmail_last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspace_settings", "gmail_last_error")
    op.drop_column("workspace_settings", "gmail_token_data")
    op.drop_column("workspace_settings", "gmail_connected_at")
    op.drop_column("workspace_settings", "gmail_connected_email")
    op.drop_column("workspace_settings", "gmail_shared_inbox")
