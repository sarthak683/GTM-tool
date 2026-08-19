"""Un-hide ClickUp-migrated companies that show real Account Sourcing engagement

Revision ID: 119
Revises: 118
Create Date: 2026-08-19

Every company created by the ClickUp migration (app/services/clickup_import.py)
is stamped with ``enrichment_sources = {"clickup_import": {"hidden_from_account_sourcing":
true}}`` at creation time, which _account_sourcing_visibility_filter()
(app/api/v1/endpoints/account_sourcing.py) checks first, ahead of anything
else — a company carrying that flag is excluded from every Account Sourcing
list/search regardless of sourcing_batch_id, a linked deal, or how much real
work has since been done on it.

Nothing in the codebase ever clears that flag. So a company like "Aurionpro" —
fully ICP-researched, Recotap-scored, moved through pipeline stages, with
logged activity — stays permanently invisible to the Account Sourcing list and
its "Search companies..." box, even though `GET /companies/{id}` (the direct
detail-page route) never applies this filter and happily renders it. That
mismatch is what made the account reachable by direct link/Quick Search but
not by the page's own search.

This is a TARGETED un-hide, not a blanket one: flipping the flag for every
ClickUp-migrated company would also surface the large set of legacy, never
actually worked accounts the flag was presumably meant to keep out of the
sourcing funnel in the first place. Instead, a hidden company is un-hidden
only if it shows a real, non-default signal that someone worked it through
Account Sourcing since the migration:

  - enrichment_cache is populated (real enrichment ran), or
  - icp_tier is set (an ICP verdict was assigned), or
  - account_status is set (Cold / In Progress / Meeting Booked / Meeting Done /
    In Pipeline / Not a Fit / DND / Reach Out Later — any of them, including
    the "disqualified" ones, since those still reflect a real review), or
  - disposition is set (a sourcing disposition was recorded), or
  - recommended_outreach_lane is set (ICP research produced a lane), or
  - it has a recotap_accounts row with source='recotap' and a non-null score
    (a real, API-pulled Recotap signal — not the 'seed'/mock fallback or an
    unpopulated 'pending' row).

A company matching none of these keeps the flag exactly as-is and stays
excluded, same as before this migration.
"""
from alembic import op

revision = "119"
down_revision = "118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE companies
           SET enrichment_sources = jsonb_set(
                   enrichment_sources,
                   '{clickup_import,hidden_from_account_sourcing}',
                   'false'::jsonb
               )
         WHERE enrichment_sources @> '{"clickup_import": {"hidden_from_account_sourcing": true}}'::jsonb
           AND (
                (enrichment_cache IS NOT NULL AND enrichment_cache <> '{}'::jsonb)
             OR (icp_tier IS NOT NULL AND icp_tier <> '')
             OR (account_status IS NOT NULL AND account_status <> '')
             OR (disposition IS NOT NULL AND disposition <> '')
             OR (recommended_outreach_lane IS NOT NULL AND recommended_outreach_lane <> '')
             OR EXISTS (
                    SELECT 1 FROM recotap_accounts ra
                     WHERE ra.company_id = companies.id
                       AND ra.source = 'recotap'
                       AND ra.score IS NOT NULL
                )
           )
        """
    )


def downgrade() -> None:
    # Not reversible: once un-hidden, there is no record of which rows this
    # migration flipped versus companies that were never hidden, or that a
    # human explicitly un-hid some other way afterward. Re-hiding everything
    # with clickup_import metadata on downgrade would incorrectly re-hide
    # those too. No-op by design — same tradeoff migration 117 makes for its
    # own irreversible cleanup.
    pass
