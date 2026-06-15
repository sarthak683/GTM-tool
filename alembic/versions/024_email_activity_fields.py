"""Add email-specific fields to activities table for Gmail inbox sync."""

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("activities", sa.Column("email_message_id", sa.String(), nullable=True))
    op.add_column("activities", sa.Column("email_subject", sa.String(), nullable=True))
    op.add_column("activities", sa.Column("email_from", sa.String(), nullable=True))
    op.add_column("activities", sa.Column("email_to", sa.Text(), nullable=True))
    op.add_column("activities", sa.Column("email_cc", sa.Text(), nullable=True))

    op.create_index("ix_activities_email_message_id", "activities", ["email_message_id"])


def downgrade() -> None:
    op.drop_index("ix_activities_email_message_id", table_name="activities")
    op.drop_column("activities", "email_cc")
    op.drop_column("activities", "email_to")
    op.drop_column("activities", "email_from")
    op.drop_column("activities", "email_subject")
    op.drop_column("activities", "email_message_id")
