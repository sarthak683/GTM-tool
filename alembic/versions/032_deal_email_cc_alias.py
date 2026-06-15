"""Add stable email CC aliases to deals.

Revision ID: 032
Revises: 031
Create Date: 2026-04-02
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def _slugify(value: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug or "deal"


def upgrade() -> None:
    op.add_column("deals", sa.Column("email_cc_alias", sa.String(), nullable=True))

    bind = op.get_bind()
    deals = sa.table(
        "deals",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("email_cc_alias", sa.String),
    )

    rows = bind.execute(sa.select(deals.c.id, deals.c.name)).fetchall()
    used_aliases: set[str] = set()

    for row in rows:
        base = _slugify(row.name)
        alias = base
        suffix = 2
        while alias in used_aliases:
            alias = f"{base}-{suffix}"
            suffix += 1
        used_aliases.add(alias)
        bind.execute(
            deals.update().where(deals.c.id == row.id).values(email_cc_alias=alias)
        )

    op.alter_column("deals", "email_cc_alias", nullable=False)
    op.create_index("ix_deals_email_cc_alias", "deals", ["email_cc_alias"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_deals_email_cc_alias", table_name="deals")
    op.drop_column("deals", "email_cc_alias")
