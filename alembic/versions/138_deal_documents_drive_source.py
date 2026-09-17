"""Support Drive-linked deal documents (not just local uploads).

Revision ID: 138
Revises: 137
Create Date: 2026-09-17

A rep can now attach a deal document by linking an existing Google Drive
file instead of only uploading local bytes. Drive-linked rows carry no
bytes at all — `data`/`size_bytes` must become nullable to allow that, and
existing rows (all local uploads so far) are backfilled to `source='upload'`.
"""

import sqlalchemy as sa
from alembic import op


revision = "138"
down_revision = "137"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("deal_documents") as batch:
        batch.alter_column("data", existing_type=sa.LargeBinary(), nullable=True)
        batch.alter_column("size_bytes", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("source", sa.String(), nullable=False, server_default="upload"))
        batch.add_column(sa.Column("drive_file_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("drive_web_view_link", sa.Text(), nullable=True))
    op.create_index(op.f("ix_deal_documents_source"), "deal_documents", ["source"])


def downgrade() -> None:
    op.drop_index(op.f("ix_deal_documents_source"), table_name="deal_documents")
    with op.batch_alter_table("deal_documents") as batch:
        batch.drop_column("drive_web_view_link")
        batch.drop_column("drive_file_id")
        batch.drop_column("source")
        batch.alter_column("size_bytes", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("data", existing_type=sa.LargeBinary(), nullable=False)
