"""Enable pg_trgm for typo-tolerant company search + GIN trigram index.

Lets the company selector find "Hailey HR" when a rep types "Haily HR".
pg_trgm is a trusted extension (PG13+), so the DB owner (beacon) can install
it without superuser. Idempotent so re-running the migration is safe.

Revision ID: 080
Revises: 079
"""
from alembic import op

revision = "080"
down_revision = "079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # GIN trigram index keeps similarity()/ILIKE fuzzy search fast as the
    # accounts table grows. IF NOT EXISTS so the migration stays idempotent.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_name_trgm "
        "ON companies USING gin (name gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_name_trgm")
    # Intentionally leave the extension installed — other queries may use it.
