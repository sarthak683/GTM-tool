"""Add company indexes for scalable account sourcing list queries."""

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.create_index("ix_companies_assigned_rep_email", "companies", ["assigned_rep_email"], unique=False, if_not_exists=True)
    op.create_index("ix_companies_disposition", "companies", ["disposition"], unique=False, if_not_exists=True)
    op.create_index("ix_companies_recommended_outreach_lane", "companies", ["recommended_outreach_lane"], unique=False, if_not_exists=True)
    op.create_index("ix_companies_created_at", "companies", ["created_at"], unique=False, if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_companies_created_at", table_name="companies", if_exists=True)
    op.drop_index("ix_companies_recommended_outreach_lane", table_name="companies", if_exists=True)
    op.drop_index("ix_companies_disposition", table_name="companies", if_exists=True)
    op.drop_index("ix_companies_assigned_rep_email", table_name="companies", if_exists=True)
