"""Add sdr_id and sdr_name to contacts for dual AE/SDR assignment."""

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("contacts", sa.Column("sdr_id", sa.Uuid(), nullable=True))
    op.add_column("contacts", sa.Column("sdr_name", sa.String(), nullable=True))
    op.create_index("ix_contacts_sdr_id", "contacts", ["sdr_id"])
    op.create_foreign_key("fk_contacts_sdr_id", "contacts", "users", ["sdr_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_contacts_sdr_id", "contacts", type_="foreignkey")
    op.drop_index("ix_contacts_sdr_id", table_name="contacts")
    op.drop_column("contacts", "sdr_name")
    op.drop_column("contacts", "sdr_id")
