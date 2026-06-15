"""Add workspace deal stage settings

Revision ID: 037_deal_stage_settings
Revises: 036
Create Date: 2026-04-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "037_deal_stage_settings"
down_revision = "036"
branch_labels = None
depends_on = None


DEFAULT_STAGE_SETTINGS = """
[
  {"id":"open","label":"OPEN","group":"active","color":"#3b82f6"},
  {"id":"reprospect","label":"REPROSPECT","group":"active","color":"#8b5cf6"},
  {"id":"demo_scheduled","label":"4.DEMO SCHEDULED","group":"active","color":"#4f6ddf"},
  {"id":"demo_done","label":"5.DEMO DONE","group":"active","color":"#1d4ed8"},
  {"id":"qualified_lead","label":"6.QUALIFIED LEAD","group":"active","color":"#6d5efc"},
  {"id":"poc_agreed","label":"7.POC AGREED","group":"active","color":"#0ea5e9"},
  {"id":"poc_wip","label":"8.POC WIP","group":"active","color":"#06b6d4"},
  {"id":"poc_done","label":"9.POC DONE","group":"active","color":"#14b8a6"},
  {"id":"commercial_negotiation","label":"10.COMMERCIAL NEGOTIATION","group":"active","color":"#f59e0b"},
  {"id":"msa_review","label":"11.WORKSHOP/MSA","group":"active","color":"#a855f7"},
  {"id":"closed_won","label":"12.CLOSED WON","group":"closed","color":"#22c55e"},
  {"id":"churned","label":"CHURNED","group":"closed","color":"#ef4444"},
  {"id":"not_a_fit","label":"NOT FIT","group":"closed","color":"#9ca3af"},
  {"id":"cold","label":"COLD","group":"closed","color":"#94a3b8"},
  {"id":"closed_lost","label":"CLOSED LOST","group":"closed","color":"#7c8da4"},
  {"id":"on_hold","label":"ON HOLD - REVISIT LATER","group":"closed","color":"#7c3aed"},
  {"id":"nurture","label":"NURTURE - FUTURE FIT","group":"closed","color":"#2dd4bf"},
  {"id":"closed","label":"CLOSED","group":"closed","color":"#64748b"}
]
"""


def upgrade() -> None:
    op.add_column(
        "workspace_settings",
        sa.Column("deal_stage_settings", sa.JSON(), nullable=False, server_default=sa.text(f"'{DEFAULT_STAGE_SETTINGS}'")),
    )


def downgrade() -> None:
    op.drop_column("workspace_settings", "deal_stage_settings")
