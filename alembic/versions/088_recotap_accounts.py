"""Create recotap_accounts (ABM / Recotap account intelligence).

Revision ID: 088
Revises: 087
Create Date: 2026-06-09

A standalone table for Recotap's account signals (journey stage, score,
engagement, intent sub-scores), keyed by domain so it joins to companies
without adding rtp_* columns to the sales schema. See
docs/RECOTAP_INTEGRATION.md §4.1.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "088"
down_revision = "087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recotap_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rtp_aid", sa.String(), nullable=True),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("journey_stage", sa.String(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("engagement", sa.String(), nullable=True),
        sa.Column("icp_fit", sa.String(), nullable=True),
        sa.Column("advertising_activity_score", sa.Integer(), nullable=True),
        sa.Column("website_intent_score", sa.Integer(), nullable=True),
        sa.Column("g2_intent_score", sa.Integer(), nullable=True),
        sa.Column("bombora_intent_score", sa.Integer(), nullable=True),
        sa.Column("hq_location", sa.String(), nullable=True),
        sa.Column("last_account_date", sa.DateTime(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="recotap"),
        sa.Column("pulled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name="fk_recotap_accounts_company_id"),
    )
    op.create_index("ix_recotap_accounts_domain", "recotap_accounts", ["domain"])
    op.create_index("ix_recotap_accounts_rtp_aid", "recotap_accounts", ["rtp_aid"])
    op.create_index("ix_recotap_accounts_company_id", "recotap_accounts", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_recotap_accounts_company_id", table_name="recotap_accounts")
    op.drop_index("ix_recotap_accounts_rtp_aid", table_name="recotap_accounts")
    op.drop_index("ix_recotap_accounts_domain", table_name="recotap_accounts")
    op.drop_table("recotap_accounts")
