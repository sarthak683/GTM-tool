"""add angel_investors and angel_mappings tables, company investor columns

Revision ID: 013
Revises: 012
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Angel Investors table ─────────────────────────────────────────────
    op.create_table(
        "angel_investors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("current_role", sa.String(), nullable=True),
        sa.Column("current_company", sa.String(), nullable=True),
        sa.Column("linkedin_url", sa.String(), nullable=True),
        sa.Column("career_history", sa.Text(), nullable=True),
        sa.Column("networks", sa.Text(), nullable=True),
        sa.Column("pe_vc_connections", sa.Text(), nullable=True),
        sa.Column("sectors", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_angel_investors_name", "angel_investors", ["name"])

    # ── Angel Mappings table (prospect ↔ angel connections) ───────────────
    op.create_table(
        "angel_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("angel_investor_id", sa.Uuid(), nullable=False),
        sa.Column("strength", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("connection_path", sa.Text(), nullable=True),
        sa.Column("why_it_works", sa.Text(), nullable=True),
        sa.Column("recommended_strategy", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["angel_investor_id"], ["angel_investors.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_angel_mappings_contact_id", "angel_mappings", ["contact_id"])
    op.create_index("ix_angel_mappings_company_id", "angel_mappings", ["company_id"])
    op.create_index("ix_angel_mappings_angel_investor_id", "angel_mappings", ["angel_investor_id"])

    # ── Add investor columns to companies ─────────────────────────────────
    op.add_column("companies", sa.Column("ownership_stage", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("pe_investors", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("vc_investors", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("strategic_investors", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "strategic_investors")
    op.drop_column("companies", "vc_investors")
    op.drop_column("companies", "pe_investors")
    op.drop_column("companies", "ownership_stage")
    op.drop_table("angel_mappings")
    op.drop_table("angel_investors")
