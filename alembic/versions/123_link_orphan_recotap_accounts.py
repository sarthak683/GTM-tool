"""Attach orphaned Recotap accounts to the Beacon company they belong to.

Revision ID: 123
Revises: 122
Create Date: 2026-08-20

``recotap_accounts`` joins to ``companies`` on the domain string, and the two
systems disagree about domains far more often than that design assumed. In prod
116 of 605 Recotap accounts carried ``company_id IS NULL`` — invisible on every
Account Sourcing surface — and 63 of them were accounts we already have:

    Recotap                  Beacon                     Recotap stage
    ironcladapp.com          ironclad.com               Opportunity
    manh.com                 manhattanassociates.com    Aware (score 52)
    coupa.com                coupasoftware.com          Aware
    thomson-reuters          thomson-reuters.unknown    Unaware

Three causes underneath: placeholder ``*.unknown`` domains that can never match
anything, CRM domains guessed from the company name, and a handful of genuinely
corrupted ones (``orkhuman.com`` for Workhuman, ``ebengage.com`` for WebEngage —
a leading ``w`` eaten by an ``lstrip("www.")`` in whatever produced the original
import; no live code does that any more).

Same three keys as ``link_recotap_accounts``, which now runs on every sync so
this cannot silt up again: externalId, then domain, then an exact normalized
name that exactly ONE live company answers to. The name pass is last and refuses
ambiguity — three prod rows are all named "Northstar Technologies".

Company domains are deliberately NOT rewritten here. Correcting a company's
domain re-keys email matching and contact association, which is a bigger blast
radius than this migration should carry; the eight known-wrong ones are reported
separately for a human to approve.

Reversible: every row this touches is recorded in ``recotap_link_backup_123``
with its prior (always NULL) company_id, and downgrade puts it back.
"""

from __future__ import annotations

from alembic import op

revision = "123"
down_revision = "122"
branch_labels = None
depends_on = None


# btrim(lower(collapsed whitespace)) — the SQL twin of
# app.services.recotap.normalize_company_name. Kept deliberately conservative:
# no Inc/Ltd stripping, no fuzzy match. A wrong link puts one account's buying
# intent on a different account, which is worse than leaving it orphaned.
_NORM_NAME = "btrim(lower(regexp_replace({col}, '\\s+', ' ', 'g')), ' .,')"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS recotap_link_backup_123 (
            recotap_account_id uuid PRIMARY KEY,
            prior_company_id   uuid,
            linked_company_id  uuid NOT NULL,
            linked_by          text NOT NULL,
            recotap_domain     text,
            created_at         timestamp NOT NULL DEFAULT now()
        )
    """)

    # 1. externalId — the Beacon company UUID we send on every push. Domain
    #    independent, so it is the one key that survives either side correcting
    #    a domain. Guarded by a regex because the column is free text.
    op.execute("""
        WITH matched AS (
            SELECT r.id AS rid, c.id AS cid
              FROM recotap_accounts r
              JOIN companies c
                ON c.deleted_at IS NULL
               AND c.id = r.external_id::uuid
             WHERE r.company_id IS NULL
               AND r.external_id ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        ), saved AS (
            INSERT INTO recotap_link_backup_123
                (recotap_account_id, prior_company_id, linked_company_id, linked_by, recotap_domain)
            SELECT m.rid, NULL, m.cid, 'external_id', r.domain
              FROM matched m JOIN recotap_accounts r ON r.id = m.rid
            ON CONFLICT (recotap_account_id) DO NOTHING
            RETURNING recotap_account_id
        )
        UPDATE recotap_accounts r
           SET company_id = m.cid, updated_at = now()
          FROM matched m
         WHERE r.id = m.rid
    """)

    # 2. Normalized domain. Mirrors normalize_domain(): scheme and www. stripped,
    #    lowercased. DISTINCT ON so two companies sharing a domain (a dedup
    #    problem, not a linking decision) cannot multiply the update.
    op.execute("""
        WITH norm AS (
            SELECT DISTINCT ON (d) d, id AS cid FROM (
                SELECT c.id,
                       regexp_replace(
                           regexp_replace(lower(btrim(coalesce(c.domain,''))), '^https?://', ''),
                           '^www\\.', '') AS d
                  FROM companies c WHERE c.deleted_at IS NULL
            ) x WHERE d <> '' ORDER BY d, id
        ), matched AS (
            SELECT r.id AS rid, n.cid
              FROM recotap_accounts r JOIN norm n ON n.d = r.domain
             WHERE r.company_id IS NULL
        ), saved AS (
            INSERT INTO recotap_link_backup_123
                (recotap_account_id, prior_company_id, linked_company_id, linked_by, recotap_domain)
            SELECT m.rid, NULL, m.cid, 'domain', r.domain
              FROM matched m JOIN recotap_accounts r ON r.id = m.rid
            ON CONFLICT (recotap_account_id) DO NOTHING
            RETURNING recotap_account_id
        )
        UPDATE recotap_accounts r
           SET company_id = m.cid, updated_at = now()
          FROM matched m
         WHERE r.id = m.rid
    """)

    # 3. Exact normalized name, and only where exactly one live company answers
    #    to it. HAVING count(*) = 1 is the whole safety of this pass.
    op.execute(f"""
        WITH unique_names AS (
            -- (array_agg(...))[1], not min(): Postgres has no min(uuid). Safe
            -- because HAVING below admits only groups of exactly one company.
            SELECT {_NORM_NAME.format(col='c.name')} AS n, (array_agg(c.id))[1] AS cid
              FROM companies c
             WHERE c.deleted_at IS NULL AND coalesce(btrim(c.name),'') <> ''
             GROUP BY 1
            HAVING count(*) = 1
        ), matched AS (
            SELECT r.id AS rid, u.cid
              FROM recotap_accounts r
              JOIN unique_names u ON u.n = {_NORM_NAME.format(col='r.name')}
             WHERE r.company_id IS NULL AND coalesce(btrim(r.name),'') <> ''
        ), saved AS (
            INSERT INTO recotap_link_backup_123
                (recotap_account_id, prior_company_id, linked_company_id, linked_by, recotap_domain)
            SELECT m.rid, NULL, m.cid, 'name', r.domain
              FROM matched m JOIN recotap_accounts r ON r.id = m.rid
            ON CONFLICT (recotap_account_id) DO NOTHING
            RETURNING recotap_account_id
        )
        UPDATE recotap_accounts r
           SET company_id = m.cid, updated_at = now()
          FROM matched m
         WHERE r.id = m.rid
    """)

    # A company that now owns several rows must carry its CRM-derived stage on
    # exactly one of them, or it counts itself twice in the funnel's stages_crm.
    # Keep it on the row Recotap knows best; sync_crm_journey maintains this
    # invariant from here on.
    op.execute("""
        WITH ranked AS (
            SELECT id, company_id,
                   row_number() OVER (
                       PARTITION BY company_id
                       ORDER BY (rtp_aid IS NULL), score DESC NULLS LAST, domain
                   ) AS rn
              FROM recotap_accounts
             WHERE company_id IS NOT NULL AND crm_journey_stage IS NOT NULL
        )
        UPDATE recotap_accounts r
           SET crm_journey_stage = NULL, updated_at = now()
          FROM ranked k
         WHERE r.id = k.id AND k.rn > 1
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE recotap_accounts r
           SET company_id = b.prior_company_id, updated_at = now()
          FROM recotap_link_backup_123 b
         WHERE r.id = b.recotap_account_id
           AND r.company_id = b.linked_company_id
    """)
    op.execute("DROP TABLE IF EXISTS recotap_link_backup_123")
