"""Make the account the source of truth for prospect ownership.

Revision ID: 121
Revises: 120
Create Date: 2026-08-20

Account Sourcing assignment is the source of truth: whoever holds an account
holds its prospects. Prod had drifted -- 66 accounts carried 328 prospects whose
AE/SDR disagreed with the account's, because the company->contact cascade
deliberately skips a third rep's prospect (CascadeResult.kept_divergent) and the
bulk-assign path only backfills an EMPTY account slot. With account-scoped
visibility now enforced (ed281ae), that drift stopped being a leak and became
the opposite problem: 273 prospects were assigned to people who could no longer
see them.

This realigns every prospect to its account's owner, and un-hides ten ClickUp
accounts that are assigned to a rep but were still flagged
hidden_from_account_sourcing (migration 119 only un-hid accounts with an
active-stage deal or recent human activity, so assigned-but-quiet ones stayed
invisible to their own owner).

DELIBERATELY EXCLUDES accounts owned by agency@beacon.li. That user has authored
ZERO activities ever, while its 100 accounts took ~4,990 activities in 120 days
from named reps (mahesh 2007, pulkit 1305, sipra 634, ...). Cascading those down
would move 165 prospects onto a dormant login and strip access from the people
actually working them -- the exact opposite of the intent. Those accounts need
their OWNER corrected upward first; that is a business decision, not a migration.
Accounts with no owner at all are skipped for the same reason.

On an owned account the prospect's (AE, SDR) pair is set to EQUAL the account's
pair -- including CLEARING a slot the account leaves empty. A half-owned account
otherwise strands the other slot: prod had prospects on Gainsight (account AE
set, no account SDR) whose contact SDR was a rep not on the account, so that rep
was assigned to a prospect they could not see. Unowned accounts are untouched, so
their prospects stay visible to whoever holds them under the unclaimed-account rule.

NO outreach-progress reset. reset_contact_outreach_progress() fires only when the
COMPANY's SDR changes (see sync_company_sdr_assignment_to_contacts); here the
company owner is unchanged and only the prospect is being corrected to match it.
This is the "propagation-gap fill, not a handoff" case that authorize_contact_edit
already documents -- resetting would destroy real call/sequence/open history.

Reversible: every changed row's previous owner is copied into
contact_ownership_backup_121 first, and downgrade() restores from it.
"""

from __future__ import annotations

from alembic import op


revision = "121"
down_revision = "120"
branch_labels = None
depends_on = None


# Accounts whose owner is real: not NULL, and not the dormant agency login.
_REAL_OWNER = """
    c.deleted_at IS NULL
    AND c.assigned_to_id IS DISTINCT FROM (SELECT id FROM users WHERE email = 'agency@beacon.li')
    AND c.sdr_id         IS DISTINCT FROM (SELECT id FROM users WHERE email = 'agency@beacon.li')
"""


def upgrade() -> None:
    # 1. Snapshot every row this migration is about to touch, so downgrade is exact.
    op.execute("""
        CREATE TABLE IF NOT EXISTS contact_ownership_backup_121 (
            contact_id uuid PRIMARY KEY,
            assigned_to_id uuid,
            assigned_rep_email varchar,
            sdr_id uuid,
            sdr_name varchar
        )
    """)
    op.execute(f"""
        INSERT INTO contact_ownership_backup_121
             (contact_id, assigned_to_id, assigned_rep_email, sdr_id, sdr_name)
        SELECT ct.id, ct.assigned_to_id, ct.assigned_rep_email, ct.sdr_id, ct.sdr_name
          FROM contacts ct
          JOIN companies c ON c.id = ct.company_id
         WHERE {_REAL_OWNER}
           AND (c.assigned_to_id IS NOT NULL OR c.sdr_id IS NOT NULL)
           AND (ct.assigned_to_id IS DISTINCT FROM c.assigned_to_id
             OR ct.sdr_id         IS DISTINCT FROM c.sdr_id)
        ON CONFLICT (contact_id) DO NOTHING
    """)

    # 2. AE slot follows the account's AE.
    op.execute(f"""
        UPDATE contacts ct
           SET assigned_to_id = c.assigned_to_id,
               assigned_rep_email = c.assigned_rep_email,
               updated_at = (now() AT TIME ZONE 'utc')
          FROM companies c
         WHERE c.id = ct.company_id
           AND {_REAL_OWNER}
           AND (c.assigned_to_id IS NOT NULL OR c.sdr_id IS NOT NULL)
           AND ct.assigned_to_id IS DISTINCT FROM c.assigned_to_id
    """)

    # 3. SDR slot follows the account's SDR. sdr_assigned_at is deliberately NOT
    #    stamped: it is the outreach watermark, and this is a correction, not a
    #    handoff -- moving it would hide the prospect's real history.
    op.execute(f"""
        UPDATE contacts ct
           SET sdr_id = c.sdr_id,
               sdr_name = c.sdr_name,
               updated_at = (now() AT TIME ZONE 'utc')
          FROM companies c
         WHERE c.id = ct.company_id
           AND {_REAL_OWNER}
           AND (c.assigned_to_id IS NOT NULL OR c.sdr_id IS NOT NULL)
           AND ct.sdr_id IS DISTINCT FROM c.sdr_id
    """)

    # 4. An assigned account must never be invisible to its own owner.
    op.execute("""
        UPDATE companies
           SET enrichment_sources = jsonb_set(
                   jsonb_set(enrichment_sources,
                       '{clickup_import,hidden_from_account_sourcing}', 'false'::jsonb),
                   '{clickup_import,unhidden_by_migration}', '"121"'::jsonb)
         WHERE deleted_at IS NULL
           AND (assigned_to_id IS NOT NULL OR sdr_id IS NOT NULL)
           AND enrichment_sources @> '{"clickup_import": {"hidden_from_account_sourcing": true}}'::jsonb
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE contacts ct
           SET assigned_to_id = b.assigned_to_id,
               assigned_rep_email = b.assigned_rep_email,
               sdr_id = b.sdr_id,
               sdr_name = b.sdr_name,
               updated_at = (now() AT TIME ZONE 'utc')
          FROM contact_ownership_backup_121 b
         WHERE b.contact_id = ct.id
    """)
    op.execute("DROP TABLE IF EXISTS contact_ownership_backup_121")
    op.execute("""
        UPDATE companies
           SET enrichment_sources = jsonb_set(
                   enrichment_sources #- '{clickup_import,unhidden_by_migration}',
                   '{clickup_import,hidden_from_account_sourcing}', 'true'::jsonb)
         WHERE enrichment_sources @> '{"clickup_import": {"unhidden_by_migration": "121"}}'::jsonb
    """)
