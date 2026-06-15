"""Contacts: dedup by email + partial UNIQUE index on lower(email).

Revision ID: 082
Revises: 081
Create Date: 2026-06-05

Root cause of "my emails aren't tracked": import/sourcing re-runs minted duplicate
contact rows for the same address, fragmenting a person's activity across rows
(e.g. an empty duplicate showed 0 emails). This migration:

  1) defensively merges any remaining duplicate-email rows — repoints the six
     contact FK tables to the oldest row, then deletes the extras (so no activity
     is orphaned), and
  2) adds a partial UNIQUE index on lower(email) so duplicates can't recur.

All contact-creation paths funnel through
``app.repositories.contact.get_or_create_contact_by_email``, which catches this
constraint and returns the existing row instead of erroring.

Idempotent: the dedup is a no-op when there are no duplicates, and the index uses
IF NOT EXISTS.
"""
from alembic import op


revision = "082"
down_revision = "081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Map every duplicate row to the keeper (oldest per lower(email)).
    op.execute(
        """
        CREATE TEMP TABLE _dup_map ON COMMIT DROP AS
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (PARTITION BY lower(btrim(email)) ORDER BY created_at, id) AS rn,
                   first_value(id) OVER (PARTITION BY lower(btrim(email)) ORDER BY created_at, id) AS keeper
            FROM contacts
            WHERE email IS NOT NULL AND btrim(email) <> ''
        )
        SELECT id AS loser, keeper FROM ranked WHERE rn > 1
        """
    )
    # Per-key-unique tables: drop the loser's row if the keeper already has one, else repoint.
    op.execute(
        """
        DELETE FROM deal_contacts dc USING _dup_map m
        WHERE dc.contact_id = m.loser
          AND EXISTS (SELECT 1 FROM deal_contacts k WHERE k.contact_id = m.keeper AND k.deal_id = dc.deal_id)
        """
    )
    op.execute("UPDATE deal_contacts dc SET contact_id = m.keeper FROM _dup_map m WHERE dc.contact_id = m.loser")
    op.execute(
        """
        DELETE FROM outreach_sequences os USING _dup_map m
        WHERE os.contact_id = m.loser
          AND EXISTS (SELECT 1 FROM outreach_sequences k WHERE k.contact_id = m.keeper)
        """
    )
    op.execute("UPDATE outreach_sequences os SET contact_id = m.keeper FROM _dup_map m WHERE os.contact_id = m.loser")
    # Plain repoints (many rows per contact allowed).
    for tbl in ("activities", "call_recordings", "angel_mappings", "reminders"):
        op.execute(f"UPDATE {tbl} t SET contact_id = m.keeper FROM _dup_map m WHERE t.contact_id = m.loser")
    op.execute("DELETE FROM contacts c USING _dup_map m WHERE c.id = m.loser")

    # 2) Enforce uniqueness going forward.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_contacts_lower_email_unique
        ON contacts (lower(email))
        WHERE email IS NOT NULL AND btrim(email) <> ''
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_contacts_lower_email_unique")
