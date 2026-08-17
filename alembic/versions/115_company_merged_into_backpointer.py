"""Merge back-pointer on companies (companies.merged_into_id)

Revision ID: 115
Revises: 114
Create Date: 2026-08-17

``merge_companies`` (app/services/company_lifecycle.py) moved everything from
the loser to the winner and then stamped ``loser.deleted_at`` — and that was
the entire record of the merge. From the outside a merged account was
indistinguishable from a deleted one: every bookmark, Slack link, and stale
list row pointing at the loser 404'd, with nothing anywhere saying where the
data went. Reps re-created the account they had just been merged out of.

``merged_into_id`` is that missing edge: loser -> winner, set at merge time,
read by ``GET /api/v1/companies/{id}/tombstone`` so the detail page can say
"this account was merged into X" and link there.

Notes:
  - Nullable and NOT backfilled. Historical merges left no evidence to
    reconstruct from (the loser's rows already carry the winner's company_id,
    which is not the same claim), so old merged rows keep NULL and render as a
    plain delete. Guessing would be worse than the honest fallback.
  - Self-referential FK with ON DELETE SET NULL: a winner that is later
    HARD-deleted (the legacy cascade path still exists) must null the
    back-pointer, never cascade the loser away with it.
  - Indexed: the trash view resolves winner names by this column.
"""
from alembic import op
import sqlalchemy as sa

revision = "115"
down_revision = "114"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("merged_into_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_companies_merged_into_id",
        "companies",
        "companies",
        ["merged_into_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_companies_merged_into_id", "companies", ["merged_into_id"])


def downgrade() -> None:
    op.drop_index("ix_companies_merged_into_id", table_name="companies")
    op.drop_constraint("fk_companies_merged_into_id", "companies", type_="foreignkey")
    op.drop_column("companies", "merged_into_id")
