"""Add deals.use_case.

Revision ID: 133
Revises: 132
Create Date: 2026-09-17

Product-line categorization for a deal (Implementation Automation, Support
and Hypercare Automation, Workflow Automation, Product Agent Studio, Cross
System Orchestration, Presales) — captured as a mandatory field on the
Qualified Lead stage-move gate in Pipeline.tsx, alongside the (optional)
MEDDPICC fields for that transition.

Additive and nullable: existing deals read as "not set" until a rep moves
them through the gate or an admin backfills it.
"""

import sqlalchemy as sa
from alembic import op


revision = "133"
down_revision = "132"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("deals") as batch:
        batch.add_column(sa.Column("use_case", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("deals") as batch:
        batch.drop_column("use_case")
