"""Create account_status_history and contact_disposition_history tables.

Revision ID: 126
Revises: 125
Create Date: 2026-08-28

Mirrors deal_stage_history (see 065). Both account_status and
call_disposition previously had no audit trail -- these tables record every
transition going forward. No backfill: neither prior value nor timing of
past changes is recoverable from existing data.
"""

from alembic import op
import sqlalchemy as sa


revision = "126"
down_revision = "125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_status_history",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(), nullable=True),
        sa.Column("to_status", sa.String(), nullable=True),
        sa.Column("changed_by_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_account_status_history_company_id", "account_status_history", ["company_id"])
    op.create_index("ix_account_status_history_from_status", "account_status_history", ["from_status"])
    op.create_index("ix_account_status_history_to_status", "account_status_history", ["to_status"])
    op.create_index("ix_account_status_history_changed_at", "account_status_history", ["changed_at"])

    op.create_table(
        "contact_disposition_history",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("contact_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_disposition", sa.String(), nullable=True),
        sa.Column("to_disposition", sa.String(), nullable=True),
        sa.Column("changed_by_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_contact_disposition_history_contact_id", "contact_disposition_history", ["contact_id"])
    op.create_index("ix_contact_disposition_history_from_disposition", "contact_disposition_history", ["from_disposition"])
    op.create_index("ix_contact_disposition_history_to_disposition", "contact_disposition_history", ["to_disposition"])
    op.create_index("ix_contact_disposition_history_changed_at", "contact_disposition_history", ["changed_at"])


def downgrade() -> None:
    op.drop_index("ix_contact_disposition_history_changed_at", table_name="contact_disposition_history")
    op.drop_index("ix_contact_disposition_history_to_disposition", table_name="contact_disposition_history")
    op.drop_index("ix_contact_disposition_history_from_disposition", table_name="contact_disposition_history")
    op.drop_index("ix_contact_disposition_history_contact_id", table_name="contact_disposition_history")
    op.drop_table("contact_disposition_history")

    op.drop_index("ix_account_status_history_changed_at", table_name="account_status_history")
    op.drop_index("ix_account_status_history_to_status", table_name="account_status_history")
    op.drop_index("ix_account_status_history_from_status", table_name="account_status_history")
    op.drop_index("ix_account_status_history_company_id", table_name="account_status_history")
    op.drop_table("account_status_history")
