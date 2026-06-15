"""add company stage milestones table

Revision ID: 043_company_stage_milestones
Revises: 042_sync_schedule_settings
Create Date: 2026-04-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "043_company_stage_milestones"
down_revision = "042_sync_schedule_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_stage_milestones",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_activity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("milestone_key", sa.String(), nullable=False),
        sa.Column("first_reached_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"]),
        sa.ForeignKeyConstraint(["source_activity_id"], ["activities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "milestone_key", name="uq_company_stage_milestone_company_key"),
    )
    op.create_index(op.f("ix_company_stage_milestones_company_id"), "company_stage_milestones", ["company_id"], unique=False)
    op.create_index(op.f("ix_company_stage_milestones_deal_id"), "company_stage_milestones", ["deal_id"], unique=False)
    op.create_index(op.f("ix_company_stage_milestones_source_activity_id"), "company_stage_milestones", ["source_activity_id"], unique=False)
    op.create_index(op.f("ix_company_stage_milestones_milestone_key"), "company_stage_milestones", ["milestone_key"], unique=False)
    op.create_index(op.f("ix_company_stage_milestones_first_reached_at"), "company_stage_milestones", ["first_reached_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_company_stage_milestones_first_reached_at"), table_name="company_stage_milestones")
    op.drop_index(op.f("ix_company_stage_milestones_milestone_key"), table_name="company_stage_milestones")
    op.drop_index(op.f("ix_company_stage_milestones_source_activity_id"), table_name="company_stage_milestones")
    op.drop_index(op.f("ix_company_stage_milestones_deal_id"), table_name="company_stage_milestones")
    op.drop_index(op.f("ix_company_stage_milestones_company_id"), table_name="company_stage_milestones")
    op.drop_table("company_stage_milestones")
