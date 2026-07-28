"""Backfill deal.sdr_id from Company.sdr_id for all deals with no sdr_id set.

For every deal that:
  - has sdr_id IS NULL
  - is linked to a company that has sdr_id set

we copy Company.sdr_id → Deal.sdr_id. This surfaces historical demo funnel
data (demos_done / demos_scheduled) in Sales Analytics SDR attribution, which
now prefers deal.sdr_id over company.sdr_id.

Revision ID: 100
Revises: 099
Create Date: 2026-07-14
"""

from alembic import op


revision = "100"
down_revision = "099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE deals d
        SET sdr_id = c.sdr_id
        FROM companies c
        WHERE d.company_id = c.id
          AND d.sdr_id IS NULL
          AND c.sdr_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # Cannot safely reverse — we don't know which deals were NULL vs intentionally set.
    # Downgrade is a no-op.
    pass
