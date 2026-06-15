"""enforce account sourcing for new company inserts

Revision ID: 059
Revises: 058
Create Date: 2026-05-03
"""

from alembic import op


revision = "059"
down_revision = "068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_unbatched_company_insert()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.sourcing_batch_id IS NULL THEN
                RAISE EXCEPTION
                    'companies must be created through Account Sourcing with sourcing_batch_id';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_prevent_unbatched_company_insert ON companies;
        CREATE TRIGGER trg_prevent_unbatched_company_insert
        BEFORE INSERT ON companies
        FOR EACH ROW
        EXECUTE FUNCTION prevent_unbatched_company_insert();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_unbatched_company_insert ON companies;")
    op.execute("DROP FUNCTION IF EXISTS prevent_unbatched_company_insert();")
