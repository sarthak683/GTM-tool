"""Un-hide ClickUp-migrated companies that a rep is assigned to and working on

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
worked through the pipeline, with an owner and logged calls/emails — stays
permanently invisible to the Account Sourcing list and its "Search companies..."
box, even though ``GET /companies/{id}`` (the direct detail-page route) never
applies this filter and happily renders it. That mismatch is what made the
account reachable by direct link/Quick Search but not by the page's own search.

WHAT COUNTS AS "ACTIVE"
-----------------------
This is a TARGETED un-hide. Of the 271 flagged companies in production, the
large majority are legacy rows nobody has touched since the migration — the
single biggest deal stage among them is ``not_a_fit`` (127 companies), followed
by ``cold`` (57). Surfacing those would bury the accounts reps actually work.

A flagged company is un-hidden only if BOTH halves of "assigned to someone AND
being worked" hold, or if there is direct evidence of recent human work:

  (a) it is ASSIGNED — an AE or SDR on the company row itself, or on any of its
      non-deleted deals (in practice assignment lives on the deal: only 47 of
      the 271 carry a company-level owner, but 265 have an assigned deal) —
      AND it has at least one deal in a stage the workspace groups as
      ``active`` (read live from workspace_settings.deal_stage_settings, so
      each environment uses its own configured funnel);
  OR
  (b) a HUMAN logged an activity on it in the last 90 days
      (``activities.created_by_id IS NOT NULL``). The created_by_id test is
      what makes this mean "a rep did something": of the last-90d activities on
      these accounts, ``personal_email_sync`` (2,225, all human-attributed) and
      ``manual`` (17) carry an author, while ``instantly``, ``gmail_sync``,
      ``beacon_ai`` and ``pre_meeting_automation`` do not. Without that test,
      automated touches would qualify a dormant account.

Deliberately NOT used: the ``enrichment_cache IS NOT NULL AND enrichment_cache
<> '{}'`` test an earlier draft of this migration relied on. It looks like a
"real enrichment ran" signal but matches everything: the column holds the JSON
scalar ``null`` on 202 of the 271 rows, and JSON ``null`` is not SQL NULL —
``'null'::jsonb IS NOT NULL`` is true and ``'null'::jsonb <> '{}'::jsonb`` is
true, so both guards pass on an empty value. Any such check needs
``jsonb_typeof(...) = 'object'``.

REVERSIBILITY
-------------
Rows flipped here are stamped ``clickup_import.unhidden_by_migration = "119"``
so downgrade() can re-hide exactly this set and nothing else — neither
companies that were never hidden, nor ones a human un-hid some other way.
"""
from alembic import op

revision = "119"
down_revision = "118"
branch_labels = None
depends_on = None


# Used only if workspace_settings has no usable deal_stage_settings (fresh DB).
_FALLBACK_ACTIVE_STAGES = (
    "reprospect", "demo_scheduled", "demo_done", "qualified_lead",
    "poc_agreed", "poc_wip", "poc_done", "commercial_negotiation", "msa_review",
)


def upgrade() -> None:
    fallback = ",".join(f"'{s}'" for s in _FALLBACK_ACTIVE_STAGES)
    op.execute(
        f"""
        WITH active_stages AS (
            SELECT COALESCE(
                (SELECT array_agg(s->>'id')
                   FROM workspace_settings ws,
                        LATERAL jsonb_array_elements(ws.deal_stage_settings::jsonb) s
                  WHERE ws.id = 1 AND s->>'group' = 'active'),
                ARRAY[{fallback}]
            ) AS stages
        )
        UPDATE companies c
           SET enrichment_sources = jsonb_set(
                   jsonb_set(
                       c.enrichment_sources,
                       '{{clickup_import,hidden_from_account_sourcing}}',
                       'false'::jsonb
                   ),
                   '{{clickup_import,unhidden_by_migration}}',
                   '"119"'::jsonb
               )
          FROM active_stages a
         WHERE c.enrichment_sources @> '{{"clickup_import": {{"hidden_from_account_sourcing": true}}}}'::jsonb
           AND (
                (
                    -- (a) assigned to a rep AND sitting in an active stage
                    (
                        c.assigned_to_id IS NOT NULL
                     OR c.sdr_id IS NOT NULL
                     OR EXISTS (
                            SELECT 1 FROM deals d
                             WHERE d.company_id = c.id
                               AND d.deleted_at IS NULL
                               AND (d.assigned_to_id IS NOT NULL OR d.sdr_id IS NOT NULL)
                        )
                    )
                    AND EXISTS (
                            SELECT 1 FROM deals d
                             WHERE d.company_id = c.id
                               AND d.deleted_at IS NULL
                               AND d.stage = ANY(a.stages)
                        )
                )
             OR (
                    -- (b) a human logged activity on it in the last 90 days
                    EXISTS (
                        SELECT 1
                          FROM deals d
                          JOIN activities act ON act.deal_id = d.id
                         WHERE d.company_id = c.id
                           AND d.deleted_at IS NULL
                           AND act.created_by_id IS NOT NULL
                           AND act.created_at > (now() AT TIME ZONE 'utc') - interval '90 days'
                    )
                )
           )
        """
    )


def downgrade() -> None:
    # Precise inverse: re-hide only the rows upgrade() stamped, and drop the
    # stamp. Companies that were never hidden, and ones un-hidden by some other
    # path, carry no stamp and are left alone.
    op.execute(
        """
        UPDATE companies
           SET enrichment_sources = jsonb_set(
                   enrichment_sources #- '{clickup_import,unhidden_by_migration}',
                   '{clickup_import,hidden_from_account_sourcing}',
                   'true'::jsonb
               )
         WHERE enrichment_sources @> '{"clickup_import": {"unhidden_by_migration": "119"}}'::jsonb
        """
    )
