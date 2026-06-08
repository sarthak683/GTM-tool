"""Companies: dedup by domain + partial UNIQUE index on lower(domain).

Revision ID: 083
Revises: 082
Create Date: 2026-06-08

Mirrors migration 082 (which did the same for contacts.email), now for
companies.domain. Re-running account sourcing / prospect import could mint more
than one company row for the same domain, fragmenting an account's contacts,
deals, signals, meetings, etc. across rows. This migration:

  1) adds a plain index on lower(domain) for lookup performance (the get_by /
     get_or_create_company_by_domain paths filter on it),
  2) defensively merges any duplicate-domain rows — repoints EVERY child FK that
     references companies.id onto the oldest row, then deletes the extras (so no
     child is orphaned), and
  3) adds a partial UNIQUE index on lower(domain) so duplicates can't recur.

Company-creation paths funnel through
``app.repositories.company.get_or_create_company_by_domain``, which catches this
constraint and returns the existing row instead of erroring.

FK child tables of companies.id that are repointed (enumerated from the models
and the 001/002/003/006/013/026/043 schema migrations — the authoritative list):

    contacts                  (company_id, ON DELETE SET NULL, many per company)
    deals                     (company_id, ON DELETE SET NULL, many per company)
    meetings                  (company_id, ON DELETE SET NULL, many per company)
    outreach_sequences        (company_id, ON DELETE CASCADE,  many per company)
    signals                   (company_id, ON DELETE CASCADE,  many per company)
    reminders                 (company_id, NO ACTION,          many per company)
    angel_mappings            (company_id, ON DELETE SET NULL, many per company)
    custom_demos              (company_id, ON DELETE SET NULL, many per company)
    company_stage_milestones  (company_id, NO ACTION, UNIQUE(company_id,
                               milestone_key) -> delete-on-conflict, then repoint)

company_stage_milestones is the only child carrying a uniqueness constraint that
involves company_id, so it gets the same drop-the-loser-if-the-keeper-already-
has-that-key treatment 082 used for deal_contacts/outreach_sequences. Every other
child allows multiple rows per company, so a plain repoint is safe.

Idempotent: the dedup is a no-op when there are no duplicates, and both indexes
use IF NOT EXISTS. The merge is INSERT-trigger-safe (the companies
prevent_unbatched_company_insert trigger fires on INSERT only; we only
UPDATE/DELETE here).
"""
from alembic import op


revision = "083"
down_revision = "082"
branch_labels = None
depends_on = None


# Child FK tables where many rows may reference one company: a blind repoint of
# loser -> keeper can never violate a per-company uniqueness rule.
_PLAIN_REPOINT_TABLES = (
    "contacts",
    "deals",
    "meetings",
    "outreach_sequences",
    "signals",
    "reminders",
    "angel_mappings",
    "custom_demos",
)


def upgrade() -> None:
    # 1) Lookup index first (cheap, also helps the merge's correlated lookups).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_companies_lower_domain
        ON companies (lower(domain))
        WHERE domain IS NOT NULL AND btrim(domain) <> ''
        """
    )

    # 2) Map every duplicate company to the keeper (oldest per lower(domain)).
    op.execute(
        """
        CREATE TEMP TABLE _company_dup_map ON COMMIT DROP AS
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (PARTITION BY lower(btrim(domain)) ORDER BY created_at, id) AS rn,
                   first_value(id) OVER (PARTITION BY lower(btrim(domain)) ORDER BY created_at, id) AS keeper
            FROM companies
            WHERE domain IS NOT NULL AND btrim(domain) <> ''
        )
        SELECT id AS loser, keeper FROM ranked WHERE rn > 1
        """
    )

    # company_stage_milestones is UNIQUE(company_id, milestone_key): drop the
    # loser's row when the keeper already has that milestone_key, else repoint.
    op.execute(
        """
        DELETE FROM company_stage_milestones csm USING _company_dup_map m
        WHERE csm.company_id = m.loser
          AND EXISTS (
              SELECT 1 FROM company_stage_milestones k
              WHERE k.company_id = m.keeper AND k.milestone_key = csm.milestone_key
          )
        """
    )
    op.execute(
        "UPDATE company_stage_milestones csm SET company_id = m.keeper "
        "FROM _company_dup_map m WHERE csm.company_id = m.loser"
    )

    # Plain repoints (many rows per company allowed).
    for tbl in _PLAIN_REPOINT_TABLES:
        op.execute(
            f"UPDATE {tbl} t SET company_id = m.keeper "
            f"FROM _company_dup_map m WHERE t.company_id = m.loser"
        )

    # Losers now have no children referencing them — safe to delete.
    op.execute("DELETE FROM companies c USING _company_dup_map m WHERE c.id = m.loser")

    # 3) Enforce uniqueness going forward.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_companies_lower_domain_unique
        ON companies (lower(domain))
        WHERE domain IS NOT NULL AND btrim(domain) <> ''
        """
    )


def downgrade() -> None:
    # The merge is not reversible (loser rows are gone); only the indexes are.
    op.execute("DROP INDEX IF EXISTS ix_companies_lower_domain_unique")
    op.execute("DROP INDEX IF EXISTS ix_companies_lower_domain")
