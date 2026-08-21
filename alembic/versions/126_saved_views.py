"""Add saved_views table for per-user filter presets.

Revision ID: 126
Revises: 125
Create Date: 2026-08-21

Stores per-user named filter sets for the pipeline / contacts / companies
pages. The `filters` and `sort` columns are JSONB blobs mirroring the page's
URL search params, so save-view is a one-line snapshot and apply-view is a
URL push — no client-side translation needed.

`is_default` is partial-unique per (user_id, object_type, view_type) so a
user can have at most one default view per object type per layout.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "126"
down_revision = "125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_views",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("object_type", sa.String(length=32), nullable=False, index=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("view_type", sa.String(length=16), nullable=False, server_default="kanban"),
        sa.Column("filters", JSONB, nullable=True),
        sa.Column("sort", JSONB, nullable=True),
        sa.Column("columns", JSONB, nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_used_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_saved_views_user_object_type_view",
        "saved_views",
        ["user_id", "object_type", "view_type"],
        unique=False,
    )
    # Partial unique — at most one default per (user, object_type, view_type).
    op.create_index(
        "uq_saved_views_one_default_per_slot",
        "saved_views",
        ["user_id", "object_type", "view_type"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_saved_views_one_default_per_slot", table_name="saved_views")
    op.drop_index("ix_saved_views_user_object_type_view", table_name="saved_views")
    op.drop_table("saved_views")
