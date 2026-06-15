"""Create deal_contacts junction table."""

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.create_table(
        "deal_contacts",
        sa.Column("deal_id", sa.Uuid(), sa.ForeignKey("deals.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("contact_id", sa.Uuid(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_deal_contacts_contact_id", "deal_contacts", ["contact_id"])


def downgrade() -> None:
    op.drop_index("ix_deal_contacts_contact_id", table_name="deal_contacts")
    op.drop_table("deal_contacts")
