"""Store company money fields as exact numeric values.

Revision ID: 126
Revises: 125
Create Date: 2026-08-24
"""

from alembic import op


revision = "126"
down_revision = "125"
branch_labels = None
depends_on = None


MONEY_COLS = (
    "arr_estimate",
    "opp_amount",
    "opp_arr",
    "opp_multiyear_license_fee",
    "opp_service_fee",
)


def upgrade() -> None:
    for col in MONEY_COLS:
        op.execute(
            f'ALTER TABLE companies ALTER COLUMN "{col}" '
            f'TYPE numeric(15,2) USING "{col}"::numeric(15,2)'
        )


def downgrade() -> None:
    for col in MONEY_COLS:
        op.execute(
            f'ALTER TABLE companies ALTER COLUMN "{col}" '
            f'TYPE double precision USING "{col}"::double precision'
        )
