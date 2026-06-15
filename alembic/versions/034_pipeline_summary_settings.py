"""Add shared pipeline summary settings to workspace settings."""

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


DEAL_FUNNEL_DEFAULT = """'{"tofu":["qualified_lead","poc_agreed"],"mofu":["poc_wip","poc_done","commercial_negotiation","msa_review","workshop"],"bofu":["closed_won"]}'::jsonb"""
PROSPECT_FUNNEL_DEFAULT = """'{"tofu":["outreach"],"mofu":["in_progress"],"bofu":["meeting_booked"]}'::jsonb"""


def upgrade() -> None:
    op.add_column(
        "workspace_settings",
        sa.Column(
            "deal_funnel_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(DEAL_FUNNEL_DEFAULT),
        ),
    )
    op.add_column(
        "workspace_settings",
        sa.Column(
            "prospect_funnel_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(PROSPECT_FUNNEL_DEFAULT),
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace_settings", "prospect_funnel_config")
    op.drop_column("workspace_settings", "deal_funnel_config")
