"""Add deal.qualification_reason and per-deal deal.priority_tag.

Revision ID: 086
Revises: 085
Create Date: 2026-06-08

Two product changes land here:

1. ``deals.qualification_reason`` — a free-text note ("why is this deal
   qualified / what's the criteria") reps capture once a deal reaches
   demo_done. Mirrors ``next_step`` (plain Text column).

2. ``deals.priority_tag`` — the P0/P1/P2 pipeline badge moves from the company
   to the deal, since one company can have several deals at different
   priorities. We backfill each deal from its company's current ``priority_tag``
   so the switch-over is visually invisible (every deal keeps its old badge);
   reps can then differentiate per deal. The company column is left in place
   (dormant) and is not dropped here.

Idempotent backfill (only fills deals that don't already have a tag). No data
is destroyed on downgrade beyond the new columns themselves.
"""
from alembic import op
import sqlalchemy as sa


revision = "086"
down_revision = "085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deals", sa.Column("qualification_reason", sa.Text(), nullable=True))
    op.add_column("deals", sa.Column("priority_tag", sa.String(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE deals AS d
               SET priority_tag = c.priority_tag
              FROM companies AS c
             WHERE d.company_id = c.id
               AND c.priority_tag IS NOT NULL
               AND d.priority_tag IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("deals", "priority_tag")
    op.drop_column("deals", "qualification_reason")
