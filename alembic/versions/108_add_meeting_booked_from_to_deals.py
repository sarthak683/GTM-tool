"""Add meeting_booked_from column to deals table

Revision ID: 108
Revises: 107
Create Date: 2026-07-27

Authored by Maithili and deployed to staging + prod inside image v0.56 before it
was committed, so both databases already sat at 108 while no repo contained the
file — every later deploy crashlooped on "Can't locate revision identified by
'108'". Recovered verbatim from that image. The original was named `107_...`
while declaring revision "108"; renamed so the filename matches the revision.
"""

import sqlalchemy as sa
from alembic import op

revision = "108"
down_revision = "107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deals",
        sa.Column("meeting_booked_from", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deals", "meeting_booked_from")
