"""Add contact indexes for scalable CRM list and lookup queries."""

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.create_index("ix_contacts_phone", "contacts", ["phone"], unique=False, if_not_exists=True)
    op.create_index("ix_contacts_outreach_lane", "contacts", ["outreach_lane"], unique=False, if_not_exists=True)
    op.create_index("ix_contacts_sequence_status", "contacts", ["sequence_status"], unique=False, if_not_exists=True)
    op.create_index("ix_contacts_created_at", "contacts", ["created_at"], unique=False, if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_contacts_created_at", table_name="contacts", if_exists=True)
    op.drop_index("ix_contacts_sequence_status", table_name="contacts", if_exists=True)
    op.drop_index("ix_contacts_outreach_lane", table_name="contacts", if_exists=True)
    op.drop_index("ix_contacts_phone", table_name="contacts", if_exists=True)
