"""Resource chunk embeddings for semantic RAG

Revision ID: 094
Revises: 093
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "094"
down_revision = "093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Each element: {"text": str, "embedding": list[float]}.
    # Pure JSONB keeps the migration trivial (no pgvector dep); scales fine to
    # ~10k chunks because ranking is done in-process after a cheap module filter.
    op.add_column(
        "sales_resources",
        sa.Column("chunks", JSONB, server_default="[]", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sales_resources", "chunks")
