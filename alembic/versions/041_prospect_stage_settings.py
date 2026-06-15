"""Add configurable prospect stage settings

Revision ID: 041_prospect_stage_settings
Revises: 040_task_perms_meeting_auto
Create Date: 2026-04-06 04:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "041_prospect_stage_settings"
down_revision = "041_clickup_crm_settings"
branch_labels = None
depends_on = None

DEFAULT_PROSPECT_STAGES = (
    '[{"id":"outreach","label":"Outreach","group":"active","color":"#2563eb"},'
    '{"id":"in_progress","label":"In Progress","group":"active","color":"#7c3aed"},'
    '{"id":"meeting_booked","label":"Meeting Booked","group":"active","color":"#0ea5e9"},'
    '{"id":"negative_response","label":"Negative Response","group":"closed","color":"#ef4444"},'
    '{"id":"no_response","label":"No Response","group":"closed","color":"#94a3b8"},'
    '{"id":"not_a_fit","label":"Not a Fit","group":"closed","color":"#9ca3af"}]'
)


def upgrade() -> None:
    op.add_column(
        "workspace_settings",
        sa.Column(
            "prospect_stage_settings",
            sa.JSON(),
            nullable=True,
            server_default=DEFAULT_PROSPECT_STAGES,
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace_settings", "prospect_stage_settings")
