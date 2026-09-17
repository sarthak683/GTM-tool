"""Convert deals.use_case from a single string to a JSONB list.

Revision ID: 134
Revises: 133
Create Date: 2026-09-17

The Qualified Lead stage-move gate's Use Case field became multi-select
(a deal can span more than one product line), so the column needs to hold
several values, not one. Existing single-value rows are wrapped into a
one-element array; nulls become an empty array — same "not set yet"
semantics tags/additional_phones already use elsewhere on this table.
"""

from alembic import op


revision = "134"
down_revision = "133"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE deals
        ALTER COLUMN use_case TYPE JSONB
        USING (
            CASE WHEN use_case IS NULL THEN '[]'::jsonb
                 ELSE jsonb_build_array(use_case)
            END
        )
        """
    )
    op.execute("ALTER TABLE deals ALTER COLUMN use_case SET DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE deals ALTER COLUMN use_case SET NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE deals ALTER COLUMN use_case DROP NOT NULL")
    op.execute("ALTER TABLE deals ALTER COLUMN use_case DROP DEFAULT")
    op.execute(
        """
        ALTER TABLE deals
        ALTER COLUMN use_case TYPE VARCHAR
        USING (use_case ->> 0)
        """
    )
