"""Add company-level SDR assignment fields."""

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("companies", sa.Column("sdr_id", sa.Uuid(), nullable=True))
    op.add_column("companies", sa.Column("sdr_email", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("sdr_name", sa.String(), nullable=True))
    op.create_index("ix_companies_sdr_id", "companies", ["sdr_id"])
    op.create_index("ix_companies_sdr_email", "companies", ["sdr_email"])
    op.create_foreign_key("fk_companies_sdr_id", "companies", "users", ["sdr_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_companies_sdr_id", "companies", type_="foreignkey")
    op.drop_index("ix_companies_sdr_email", table_name="companies")
    op.drop_index("ix_companies_sdr_id", table_name="companies")
    op.drop_column("companies", "sdr_name")
    op.drop_column("companies", "sdr_email")
    op.drop_column("companies", "sdr_id")
