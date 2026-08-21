"""Add currency_code to deals + arr_estimate_currency to companies.

Revision ID: 125
Revises: 124
Create Date: 2026-08-21

Multi-currency support. Existing rows default to 'USD' (server_default fills
the column for current data; new writes accept any ISO 4217 code but the
frontend picker is curated to a short list — see
frontend/src/lib/currencies.ts).

Why a per-row currency and not a single workspace currency: Beacon sells to
US + EMEA + APAC and most deals are USD but not all. A workspace-level setting
forces every non-USD deal to be done off-system. Per-row currency keeps the
pipeline forecasting by stage honest — each deal knows its own currency.

The column is nullable so legacy deals from before this migration that were
written without a currency keep working; the server_default + frontend
default both fill USD on the way in.
"""

from alembic import op
import sqlalchemy as sa


revision = "125"
down_revision = "124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deals",
        sa.Column(
            "currency_code",
            sa.String(length=3),
            nullable=True,
            server_default="USD",
        ),
    )
    op.add_column(
        "companies",
        sa.Column(
            "arr_estimate_currency",
            sa.String(length=3),
            nullable=True,
            server_default="USD",
        ),
    )


def downgrade() -> None:
    op.drop_column("companies", "arr_estimate_currency")
    op.drop_column("deals", "currency_code")
