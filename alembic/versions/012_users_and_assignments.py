"""add users table and assignment FKs on companies/contacts

Revision ID: 012
Revises: 011
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Users table ──────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("avatar_url", sa.String(), nullable=True),
        sa.Column("google_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="sales_rep"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_google_id", "users", ["google_id"], unique=True)

    # ── Assignment FK on companies ───────────────────────────────────────
    op.add_column("companies", sa.Column("assigned_to_id", sa.Uuid(), nullable=True))
    op.create_index("ix_companies_assigned_to_id", "companies", ["assigned_to_id"])
    op.create_foreign_key(
        "fk_companies_assigned_to_id",
        "companies",
        "users",
        ["assigned_to_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── Assignment FK on contacts ────────────────────────────────────────
    op.add_column("contacts", sa.Column("assigned_to_id", sa.Uuid(), nullable=True))
    op.create_index("ix_contacts_assigned_to_id", "contacts", ["assigned_to_id"])
    op.create_foreign_key(
        "fk_contacts_assigned_to_id",
        "contacts",
        "users",
        ["assigned_to_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_contacts_assigned_to_id", "contacts", type_="foreignkey")
    op.drop_index("ix_contacts_assigned_to_id", table_name="contacts")
    op.drop_column("contacts", "assigned_to_id")

    op.drop_constraint("fk_companies_assigned_to_id", "companies", type_="foreignkey")
    op.drop_index("ix_companies_assigned_to_id", table_name="companies")
    op.drop_column("companies", "assigned_to_id")

    op.drop_index("ix_users_google_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
