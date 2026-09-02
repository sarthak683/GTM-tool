"""Store company money fields as exact numeric values.

Revision ID: 127
Revises: 126
Create Date: 2026-08-24

Revision 126 existed in two deployed histories. The canonical chain keeps the
production 126 account/contact history migration and moves this conversion to
127. Reapplying the same PostgreSQL type conversion is safe for databases that
already ran the former 126 version.
"""

from alembic import op


revision = "127"
down_revision = "126"
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
