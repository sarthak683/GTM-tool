"""Add uploader and metadata fields to sourcing batches."""

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    op.add_column("sourcing_batches", sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("sourcing_batches", sa.Column("created_by_name", sa.String(), nullable=True))
    op.add_column("sourcing_batches", sa.Column("created_by_email", sa.String(), nullable=True))
    op.add_column("sourcing_batches", sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_foreign_key(
        "fk_sourcing_batches_created_by_id_users",
        "sourcing_batches",
        "users",
        ["created_by_id"],
        ["id"],
    )
    op.create_index("ix_sourcing_batches_created_by_id", "sourcing_batches", ["created_by_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sourcing_batches_created_by_id", table_name="sourcing_batches")
    op.drop_constraint("fk_sourcing_batches_created_by_id_users", "sourcing_batches", type_="foreignkey")
    op.drop_column("sourcing_batches", "meta")
    op.drop_column("sourcing_batches", "created_by_email")
    op.drop_column("sourcing_batches", "created_by_name")
    op.drop_column("sourcing_batches", "created_by_id")
