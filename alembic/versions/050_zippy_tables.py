"""add zippy conversations, messages, and indexed drive files

Revision ID: 050
Revises: 049
Create Date: 2026-04-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zippy_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=False, server_default="New conversation"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        op.f("ix_zippy_conversations_user_id"),
        "zippy_conversations",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_zippy_conversations_updated_at"),
        "zippy_conversations",
        ["updated_at"],
        unique=False,
    )

    op.create_table(
        "zippy_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("zippy_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=True),
        sa.Column("artifacts", postgresql.JSONB(), nullable=True),
        sa.Column("tool_trace", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        op.f("ix_zippy_messages_conversation_id"),
        "zippy_messages",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_zippy_messages_role"),
        "zippy_messages",
        ["role"],
        unique=False,
    )
    op.create_index(
        op.f("ix_zippy_messages_created_at"),
        "zippy_messages",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "indexed_drive_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("drive_file_id", sa.String(), nullable=False),
        sa.Column("drive_folder_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("web_view_link", sa.String(), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("drive_modified_at", sa.DateTime(), nullable=True),
        sa.Column("qdrant_chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_indexed_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("extra", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("owner_user_id", "drive_file_id", name="uq_indexed_drive_files_owner_file"),
    )
    op.create_index(
        op.f("ix_indexed_drive_files_owner_user_id"),
        "indexed_drive_files",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_indexed_drive_files_is_admin"),
        "indexed_drive_files",
        ["is_admin"],
        unique=False,
    )
    op.create_index(
        op.f("ix_indexed_drive_files_drive_file_id"),
        "indexed_drive_files",
        ["drive_file_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_indexed_drive_files_drive_folder_id"),
        "indexed_drive_files",
        ["drive_folder_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_indexed_drive_files_last_indexed_at"),
        "indexed_drive_files",
        ["last_indexed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_indexed_drive_files_last_indexed_at"), table_name="indexed_drive_files")
    op.drop_index(op.f("ix_indexed_drive_files_drive_folder_id"), table_name="indexed_drive_files")
    op.drop_index(op.f("ix_indexed_drive_files_drive_file_id"), table_name="indexed_drive_files")
    op.drop_index(op.f("ix_indexed_drive_files_is_admin"), table_name="indexed_drive_files")
    op.drop_index(op.f("ix_indexed_drive_files_owner_user_id"), table_name="indexed_drive_files")
    op.drop_table("indexed_drive_files")

    op.drop_index(op.f("ix_zippy_messages_created_at"), table_name="zippy_messages")
    op.drop_index(op.f("ix_zippy_messages_role"), table_name="zippy_messages")
    op.drop_index(op.f("ix_zippy_messages_conversation_id"), table_name="zippy_messages")
    op.drop_table("zippy_messages")

    op.drop_index(op.f("ix_zippy_conversations_updated_at"), table_name="zippy_conversations")
    op.drop_index(op.f("ix_zippy_conversations_user_id"), table_name="zippy_conversations")
    op.drop_table("zippy_conversations")
