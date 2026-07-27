"""Per-contact SDR reassignment watermark + Instantly count baselines

Revision ID: 107
Revises: 106
Create Date: 2026-07-26

The reassignment reset originally hung the watermark off `companies`
(migration 106). That over-reached: the cascade deliberately skips contacts
pointed at a *different* SDR, but a company-level watermark still hid their
activity, so a deliberately split prospect lost its call history even though
its own owner never changed. The watermark belongs on the row it applies to.

`instantly_*_baseline` records the open/click totals wiped at reassignment so
the Instantly poller can report counts accrued since, instead of restoring the
previous SDR's lifetime totals (which are still held on Instantly's side).
"""

from alembic import op
import sqlalchemy as sa


revision = "107"
down_revision = "106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("sdr_assigned_at", sa.DateTime(), nullable=True))
    op.add_column(
        "contacts",
        sa.Column("instantly_open_baseline", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "contacts",
        sa.Column("instantly_click_baseline", sa.Integer(), nullable=False, server_default="0"),
    )
    # Backfill: migration 106 shipped with the company-level watermark, so any
    # reassignment already performed under it must keep behaving the same.
    # Only contacts that actually follow the company's current SDR inherit it —
    # split contacts are exactly the rows the company-level filter got wrong.
    op.execute(
        """
        UPDATE contacts c
           SET sdr_assigned_at = co.sdr_assigned_at
          FROM companies co
         WHERE c.company_id = co.id
           AND co.sdr_assigned_at IS NOT NULL
           AND c.sdr_id IS NOT DISTINCT FROM co.sdr_id
        """
    )


def downgrade() -> None:
    op.drop_column("contacts", "instantly_click_baseline")
    op.drop_column("contacts", "instantly_open_baseline")
    op.drop_column("contacts", "sdr_assigned_at")
