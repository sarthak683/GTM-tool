"""Backfill deal_contacts.role="primary" for historically meeting-booked contacts.

Revision ID: 136
Revises: 135
Create Date: 2026-09-17

The deal drawer's Meeting Contact list only trusts role "primary"/"champion"
— never "auto_linked" (deal_linker.py's bulk backfill links any meeting/email
attendee as a general stakeholder, not specifically who booked the meeting).
Two rep-facing signals mean "a meeting was booked with this person" —
Contact.call_disposition in ('demo_scheduled_booked', 'meeting_confirmed'),
or Contact.account_status = 'meeting_booked' — but until now neither one
reliably created that link (call_disposition only linked via a bell
notification gated on an assigned SDR/AE most prospects don't have;
account_status never linked at all). app.services.disposition_effects now
links directly and unconditionally going forward; this is the one-time catch
-up for every contact who was already marked meeting-booked before that.

(deal_id, contact_id) is deal_contacts' primary key, so an existing row
(most commonly "auto_linked") is upgraded in place via ON CONFLICT, never
inserted alongside. Idempotent and safe to re-run.
"""

from alembic import op


revision = "136"
down_revision = "135"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO deal_contacts (deal_id, contact_id, role, added_at)
        SELECT d.id, c.id, 'primary', now()
        FROM contacts c
        JOIN deals d
            ON d.company_id = c.company_id
            AND d.pipeline_type = 'deal'
            AND d.deleted_at IS NULL
        WHERE c.company_id IS NOT NULL
          AND (
              c.call_disposition IN ('demo_scheduled_booked', 'meeting_confirmed')
              OR c.account_status = 'meeting_booked'
          )
        ON CONFLICT (deal_id, contact_id) DO UPDATE
        SET role = 'primary'
        WHERE deal_contacts.role NOT IN ('primary', 'champion')
        """
    )


def downgrade() -> None:
    # Non-destructive data enrichment — no reliable way to tell which rows
    # this backfill created/upgraded apart from rows a rep intentionally set
    # to "primary" by hand, so there is nothing safe to revert.
    pass
