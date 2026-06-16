"""Add ON DELETE SET NULL to recotap_accounts.company_id FK.

Revision ID: 091
Revises: 090
Create Date: 2026-06-11

Migration 088 created `fk_recotap_accounts_company_id` with no ON DELETE. Once any
recotap row links to a company, deleting that company — via DELETE /companies/{id}
or the account-sourcing / workspace reset cascade (CompanyRepository.delete_with_cascade,
which does not clean up recotap rows) — raised a FK IntegrityError -> HTTP 500.

`company_id` is nullable, so SET NULL is the correct behavior: the company delete
succeeds, and the ABM signal row survives with a null company link (it re-links by
domain on the next Recotap pull). Mirrored on the model with a pointer comment.
"""
from alembic import op


revision = "091"
down_revision = "090"
branch_labels = None
depends_on = None

_FK = "fk_recotap_accounts_company_id"


def upgrade() -> None:
    op.drop_constraint(_FK, "recotap_accounts", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "recotap_accounts",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(_FK, "recotap_accounts", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "recotap_accounts",
        "companies",
        ["company_id"],
        ["id"],
    )
