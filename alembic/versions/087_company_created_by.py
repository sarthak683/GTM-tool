"""Add companies.created_by_id / created_by_name (who added the account).

Revision ID: 087
Revises: 086
Create Date: 2026-06-08

Leadership needs to see which account was added by whom (the "when" already
exists via created_at). We stamp the creating user on manual adds and CSV/Excel
uploads going forward, and backfill history from the sourcing batch that created
each company — sourcing_batches already store created_by_id / created_by_name.

created_by_name is denormalized (like companies.assigned_rep_name / sdr_name) so
the UI can render "Added by X" without a join. System-created rows (imports, AI
sourcing, seed) stay null.
"""
from alembic import op
import sqlalchemy as sa


revision = "087"
down_revision = "086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("created_by_id", sa.Uuid(), nullable=True))
    op.add_column("companies", sa.Column("created_by_name", sa.String(), nullable=True))
    op.create_index("ix_companies_created_by_id", "companies", ["created_by_id"])
    op.create_foreign_key(
        "fk_companies_created_by_id",
        "companies",
        "users",
        ["created_by_id"],
        ["id"],
    )
    # Backfill from the sourcing batch that created each company.
    op.execute(
        sa.text(
            """
            UPDATE companies AS c
               SET created_by_id = b.created_by_id,
                   created_by_name = b.created_by_name
              FROM sourcing_batches AS b
             WHERE c.sourcing_batch_id = b.id
               AND c.created_by_id IS NULL
               AND b.created_by_id IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint("fk_companies_created_by_id", "companies", type_="foreignkey")
    op.drop_index("ix_companies_created_by_id", table_name="companies")
    op.drop_column("companies", "created_by_name")
    op.drop_column("companies", "created_by_id")
