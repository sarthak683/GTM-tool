"""Normalize email_from on existing activities: beaconli.co and beaconli.com → beacon.li.

Any activity where the sender was using an Instantly or secondary Beacon domain
gets its email_from rewritten to the canonical @beacon.li address so analytics
attribution works consistently.

Revision ID: 101
Revises: 100
Create Date: 2026-07-15
"""

from alembic import op


revision = "101"
down_revision = "100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rewrite beaconli.co → beacon.li
    op.execute(
        """
        UPDATE activities
        SET email_from = REPLACE(email_from, '@beaconli.co', '@beacon.li')
        WHERE email_from ILIKE '%@beaconli.co'
        """
    )
    # Rewrite beaconli.com → beacon.li
    op.execute(
        """
        UPDATE activities
        SET email_from = REPLACE(email_from, '@beaconli.com', '@beacon.li')
        WHERE email_from ILIKE '%@beaconli.com'
        """
    )


def downgrade() -> None:
    # Cannot safely reverse without knowing original domains.
    pass
