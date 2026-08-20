"""Undo the multi-party meeting mis-links and the contacts they created.

Revision ID: 124
Revises: 123
Create Date: 2026-08-20

Four of Beacon's OWN board/MIS reviews were auto-attributed to a customer deal
(IQVIA 2, Bhavya's). Their attendees — the company's investors and lawyers —
were then created as CONTACTS of the customer account and linked to the deal, at
which point personal-inbox email sync did exactly what it is told: resolve
participants to contacts, walk deal_contacts, attach. 103 of the deal's 131
emails had nothing to do with IQVIA, and 52 of those were Series A
correspondence — share certificates, board MIS, cap-table threads — sitting on a
deal that `deal_visibility_filter` makes visible to EVERY user, because the
pipeline is workspace-wide by product decision.

Only 18 of the 131 emails were genuinely from @iqvia.com. The account carried no
@iqvia.com contact at all.

The live defect is fixed in code (tldv_sync.MULTI_PARTY_DOMAIN_THRESHOLD, with
the same guard in calendar_sync and meeting_relink — three of the four meetings
came in through Google Calendar). This migration repairs what it already did.

Written as predicates, not hard-coded ids, so it also heals any other meeting of
the same shape — but measured against prod it touches exactly the four.

DELIBERATELY NOT DONE — a naive "contact domain must equal company domain" sweep
looked attractive and would have been wrong. 61 production contacts fail that
test and 56 of them are correct: Ceridian renamed itself Dayforce (34 contacts),
Alight's payroll arm became Strada, eBaoTech ships InsureMO, Civica uses .co.uk.
Severing those would have broken email tracking on live accounts to fix a
cosmetic mismatch. Likewise the 27 legacy "<name> Attendee" contacts: 21 sit on
the right account at the right domain and are real prospects. Pollution is
identified by the multi-party MEETING that created it, never by the domain alone.

Reversible: every change is journaled in meeting_link_backup_124 /
contact_move_backup_124 / activity_detach_backup_124, and downgrade replays them.
"""

from __future__ import annotations

from alembic import op

revision = "124"
down_revision = "123"
branch_labels = None
depends_on = None

# Attendee domains that identify neither a customer nor us. Mirrors
# deal_linker.FREE_EMAIL_PROVIDERS; a personal Gmail address in the room must
# not count toward "how many companies are here".
_FREE = (
    "'gmail.com','googlemail.com','yahoo.com','yahoo.co.in','outlook.com',"
    "'hotmail.com','live.com','icloud.com','aol.com','proton.me',"
    "'protonmail.com','rediffmail.com','ymail.com','msn.com'"
)

# Meetings that are events, not account meetings: 3+ distinct external attendee
# domains. Internal domains come from workspace_settings so this stays true to
# the running config rather than hard-coding beacon.li.
_MULTI_PARTY = f"""
    SELECT m.id
      FROM meetings m
      LEFT JOIN LATERAL jsonb_array_elements(coalesce(m.attendees, '[]'::jsonb)) a ON true
     WHERE m.deal_id IS NOT NULL
       AND m.manually_linked IS NOT TRUE
     GROUP BY m.id
    HAVING count(DISTINCT split_part(lower(a->>'email'), '@', 2)) FILTER (
             WHERE a->>'email' LIKE '%@%'
               AND split_part(lower(a->>'email'), '@', 2) NOT IN ({_FREE})
               AND split_part(lower(a->>'email'), '@', 2) NOT IN (
                     SELECT jsonb_array_elements_text(coalesce(internal_domains::jsonb, '[]'::jsonb))
                       FROM workspace_settings WHERE id = 1)
           ) >= 3
"""


def upgrade() -> None:
    for ddl in (
        """CREATE TABLE IF NOT EXISTS meeting_link_backup_124 (
               meeting_id uuid PRIMARY KEY, prior_deal_id uuid, prior_company_id uuid,
               title text, created_at timestamp NOT NULL DEFAULT now())""",
        """CREATE TABLE IF NOT EXISTS contact_move_backup_124 (
               contact_id uuid PRIMARY KEY, prior_company_id uuid, new_company_id uuid,
               email text, created_at timestamp NOT NULL DEFAULT now())""",
        """CREATE TABLE IF NOT EXISTS activity_detach_backup_124 (
               activity_id uuid PRIMARY KEY, prior_deal_id uuid,
               created_at timestamp NOT NULL DEFAULT now())""",
        """CREATE TABLE IF NOT EXISTS deal_contact_detach_backup_124 (
               deal_id uuid, contact_id uuid, role text,
               created_at timestamp NOT NULL DEFAULT now(),
               PRIMARY KEY (deal_id, contact_id))""",
    ):
        op.execute(ddl)

    # ── 1. Remember which deals/companies the bad meetings pointed at, then
    #       unlink the meetings. The recorded set drives every later step, so a
    #       re-run after the links are cleared is a no-op rather than a wider
    #       sweep.
    op.execute(f"""
        INSERT INTO meeting_link_backup_124 (meeting_id, prior_deal_id, prior_company_id, title)
        SELECT m.id, m.deal_id, m.company_id, left(coalesce(m.title, ''), 200)
          FROM meetings m WHERE m.id IN ({_MULTI_PARTY})
        ON CONFLICT (meeting_id) DO NOTHING
    """)
    op.execute("""
        UPDATE meetings m SET deal_id = NULL, company_id = NULL, updated_at = now()
          FROM meeting_link_backup_124 b WHERE m.id = b.meeting_id
    """)

    # ── 2. Detach the emails that were only ever on the deal because of those
    #       meetings' attendees. An activity is KEPT when any of its own
    #       participants belongs to the account's real domain — that is what
    #       separates the 18 genuine IQVIA messages (and the Beacon-side replies
    #       in those same threads) from the 103 that do not belong.
    #
    #       Accounts whose domain is a ".unknown" placeholder cannot be tested
    #       this way, so the account NAME's leading token is used as a fallback
    #       needle ("IQVIA-HIS/EMR" -> "iqvia"), which is exactly the evidence a
    #       human would use.
    op.execute("""
        WITH affected AS (
            SELECT DISTINCT b.prior_deal_id AS deal_id FROM meeting_link_backup_124 b
             WHERE b.prior_deal_id IS NOT NULL
        ), needle AS (
            SELECT a.deal_id,
                   CASE WHEN c.domain IS NOT NULL AND c.domain <> ''
                             AND c.domain NOT LIKE '%.unknown'
                        THEN '@' || regexp_replace(lower(c.domain), '^www\\.', '')
                        ELSE '@' || lower((regexp_split_to_array(coalesce(c.name, ''), '[^A-Za-z0-9]+'))[1])
                   END AS needle
              FROM affected a JOIN deals d ON d.id = a.deal_id
              LEFT JOIN companies c ON c.id = d.company_id
        ), doomed AS (
            SELECT act.id, act.deal_id
              FROM activities act JOIN needle n ON n.deal_id = act.deal_id
             WHERE act.source = 'personal_email_sync'
               AND n.needle <> '@'
               AND lower(coalesce(act.email_from,'') || ' ' || coalesce(act.email_to,'')
                         || ' ' || coalesce(act.email_cc,'')) NOT LIKE '%' || n.needle || '%'
        ), saved AS (
            INSERT INTO activity_detach_backup_124 (activity_id, prior_deal_id)
            SELECT id, deal_id FROM doomed
            ON CONFLICT (activity_id) DO NOTHING
            RETURNING activity_id
        )
        UPDATE activities act SET deal_id = NULL
          FROM doomed WHERE act.id = doomed.id
    """)

    # ── 3. Unlink the event attendees from the deal. Without this the nightly
    #       stakeholder reconciler re-links them and email sync starts
    #       re-attaching within a day.
    op.execute("""
        WITH attendees AS (
            SELECT DISTINCT b.prior_deal_id AS deal_id,
                   lower(a->>'email') AS email
              FROM meeting_link_backup_124 b
              JOIN meetings m ON m.id = b.meeting_id
              LEFT JOIN LATERAL jsonb_array_elements(coalesce(m.attendees,'[]'::jsonb)) a ON true
             WHERE b.prior_deal_id IS NOT NULL AND a->>'email' LIKE '%@%'
        ), doomed AS (
            SELECT dc.deal_id, dc.contact_id, dc.role
              FROM deal_contacts dc
              JOIN contacts ct ON ct.id = dc.contact_id
              JOIN attendees at ON at.deal_id = dc.deal_id AND at.email = lower(ct.email)
        ), saved AS (
            INSERT INTO deal_contact_detach_backup_124 (deal_id, contact_id, role)
            SELECT deal_id, contact_id, role FROM doomed
            ON CONFLICT (deal_id, contact_id) DO NOTHING
            RETURNING contact_id
        )
        DELETE FROM deal_contacts dc
         USING doomed d WHERE dc.deal_id = d.deal_id AND dc.contact_id = d.contact_id
    """)

    # ── 4. Move those contacts off the customer account. Step 1 of
    #       reconcile_deal_stakeholders links EVERY contact on an account to its
    #       deals unconditionally, so leaving them filed there would re-create
    #       step 3's links on the next nightly run.
    #
    #       They go to the company that actually owns their email domain when one
    #       exists (Sorin Investments and Athera are already accounts), otherwise
    #       to no company. Assignment is left alone on purpose: an unassigned,
    #       company-less contact is visible to every AE under the prospect
    #       visibility rule, which would be a wider exposure than the one being
    #       closed.
    op.execute("""
        WITH dom AS (
            SELECT id, regexp_replace(lower(btrim(coalesce(domain,''))), '^www\\.', '') AS d
              FROM companies WHERE deleted_at IS NULL
        ), moving AS (
            SELECT ct.id AS contact_id, ct.company_id AS prior_company_id, ct.email,
                   (SELECT dm.id FROM dom dm
                     WHERE dm.d = split_part(lower(ct.email), '@', 2)
                       AND dm.d <> '' AND dm.id <> ct.company_id LIMIT 1) AS new_company_id
              FROM deal_contact_detach_backup_124 b
              JOIN contacts ct ON ct.id = b.contact_id
             WHERE ct.company_id IS NOT NULL
               AND split_part(lower(ct.email), '@', 2) <> (
                     SELECT dm.d FROM dom dm WHERE dm.id = ct.company_id)
        ), saved AS (
            INSERT INTO contact_move_backup_124 (contact_id, prior_company_id, new_company_id, email)
            SELECT contact_id, prior_company_id, new_company_id, email FROM moving
            ON CONFLICT (contact_id) DO NOTHING
            RETURNING contact_id
        )
        UPDATE contacts ct SET company_id = m.new_company_id, updated_at = now()
          FROM moving m WHERE ct.id = m.contact_id
    """)

    # ── 5. stakeholder_count is denormalized and three writers have drifted it
    #       before; step 3 just changed the truth underneath it.
    op.execute("""
        UPDATE deals d SET stakeholder_count = sub.n
          FROM (SELECT dl.id, (SELECT count(*) FROM deal_contacts dc WHERE dc.deal_id = dl.id) AS n
                  FROM deals dl
                 WHERE dl.id IN (SELECT DISTINCT deal_id FROM deal_contact_detach_backup_124)) sub
         WHERE d.id = sub.id AND d.stakeholder_count IS DISTINCT FROM sub.n
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE contacts ct SET company_id = b.prior_company_id, updated_at = now()
          FROM contact_move_backup_124 b WHERE ct.id = b.contact_id
    """)
    op.execute("""
        INSERT INTO deal_contacts (deal_id, contact_id, role)
        SELECT deal_id, contact_id, role FROM deal_contact_detach_backup_124
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        UPDATE activities act SET deal_id = b.prior_deal_id
          FROM activity_detach_backup_124 b WHERE act.id = b.activity_id
    """)
    op.execute("""
        UPDATE meetings m SET deal_id = b.prior_deal_id, company_id = b.prior_company_id,
                              updated_at = now()
          FROM meeting_link_backup_124 b WHERE m.id = b.meeting_id
    """)
    for t in ("deal_contact_detach_backup_124", "activity_detach_backup_124",
              "contact_move_backup_124", "meeting_link_backup_124"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
