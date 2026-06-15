"""Add external source IDs for deal/activity imports."""

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("deals", sa.Column("external_source", sa.String(), nullable=True))
    op.add_column("deals", sa.Column("external_source_id", sa.String(), nullable=True))
    op.create_index("ix_deals_external_source", "deals", ["external_source"])
    op.create_index("ix_deals_external_source_id", "deals", ["external_source_id"])
    op.create_unique_constraint(
        "uq_deals_external_source_id",
        "deals",
        ["external_source", "external_source_id"],
    )

    op.add_column("activities", sa.Column("external_source", sa.String(), nullable=True))
    op.add_column("activities", sa.Column("external_source_id", sa.String(), nullable=True))
    op.create_index("ix_activities_external_source", "activities", ["external_source"])
    op.create_index("ix_activities_external_source_id", "activities", ["external_source_id"])
    op.create_unique_constraint(
        "uq_activities_external_source_id",
        "activities",
        ["external_source", "external_source_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_activities_external_source_id", "activities", type_="unique")
    op.drop_index("ix_activities_external_source_id", table_name="activities")
    op.drop_index("ix_activities_external_source", table_name="activities")
    op.drop_column("activities", "external_source_id")
    op.drop_column("activities", "external_source")

    op.drop_constraint("uq_deals_external_source_id", "deals", type_="unique")
    op.drop_index("ix_deals_external_source_id", table_name="deals")
    op.drop_index("ix_deals_external_source", table_name="deals")
    op.drop_column("deals", "external_source_id")
    op.drop_column("deals", "external_source")
