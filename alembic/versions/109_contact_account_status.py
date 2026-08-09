"""Add per-prospect manual sourcing status to contacts

Revision ID: 109
Revises: 108
Create Date: 2026-08-07

Adds an `account_status` column to contacts, mirroring the account-level
status on companies but scoped to individual prospects. Values are the
CONTACT_STATUS_VALUES set in app/models/contact.py; the frontend control lives
in frontend/src/lib/contactStatus.ts. Distinct from sequence_status, which
call/LinkedIn automation drives.
"""

import sqlalchemy as sa
from alembic import op

revision = "109"
down_revision = "108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("account_status", sa.String(), nullable=True))
    op.create_index("ix_contacts_account_status", "contacts", ["account_status"])


def downgrade() -> None:
    op.drop_index("ix_contacts_account_status", table_name="contacts")
    op.drop_column("contacts", "account_status")
