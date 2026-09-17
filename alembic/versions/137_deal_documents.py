"""Create deal_documents table.

Revision ID: 137
Revises: 136
Create Date: 2026-09-17

Files a rep attaches to a deal (proposals, contracts, decks) from the new
Documents tab on the deal drawer. Stored directly in Postgres as bytea —
there is no S3/GCS bucket configured anywhere in this app, so this avoids
needing new infrastructure/credentials. Not meant for very large files or
high volume; see MAX_DEAL_DOCUMENT_BYTES in the upload endpoint.
"""

import sqlalchemy as sa
from alembic import op


revision = "137"
down_revision = "136"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deal_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deal_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by_id", sa.Uuid(), nullable=True),
        sa.Column("uploaded_by_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_deal_documents_deal_id"), "deal_documents", ["deal_id"])
    op.create_index(op.f("ix_deal_documents_uploaded_by_id"), "deal_documents", ["uploaded_by_id"])
    op.create_index(op.f("ix_deal_documents_created_at"), "deal_documents", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_deal_documents_created_at"), table_name="deal_documents")
    op.drop_index(op.f("ix_deal_documents_uploaded_by_id"), table_name="deal_documents")
    op.drop_index(op.f("ix_deal_documents_deal_id"), table_name="deal_documents")
    op.drop_table("deal_documents")
