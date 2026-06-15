"""Add meetings.is_internal column and backfill from attendee domains.

Revision ID: 068
Revises: 054
Create Date: 2026-04-24

Marks a meeting as "internal" when every attendee with a usable email
belongs to a workspace internal domain (seeded with beacon.li).  The
tldv sync path used to silently drop these; going forward we keep them
but hide them from default views via a filter toggle.

Backfill reads workspace_settings.internal_domains (added in 054) and
flags historical rows so the UI can immediately segment them.
"""

from alembic import op
import sqlalchemy as sa


revision = "068"
down_revision = "067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_meetings_is_internal", "meetings", ["is_internal"])

    # Backfill using the default workspace internal_domains (['beacon.li']).
    # We hardcode the domain here so the migration is deterministic and does
    # not depend on whether workspace_settings has been populated. Admins can
    # update the list later; this just sets a defensible initial state.
    op.execute(
        """
        UPDATE meetings m
        SET is_internal = TRUE
        WHERE jsonb_typeof(m.attendees) = 'array'
          AND (
            SELECT count(*) FROM jsonb_array_elements(m.attendees) a
            WHERE a->>'email' IS NOT NULL
              AND a->>'email' <> ''
              AND lower(a->>'email') !~ '@beacon\\.li$'
          ) = 0
          AND (
            SELECT count(*) FROM jsonb_array_elements(m.attendees) a
            WHERE a->>'email' IS NOT NULL AND a->>'email' LIKE '%@%'
          ) > 0
        """
    )


def downgrade() -> None:
    op.drop_index("ix_meetings_is_internal", table_name="meetings")
    op.drop_column("meetings", "is_internal")
