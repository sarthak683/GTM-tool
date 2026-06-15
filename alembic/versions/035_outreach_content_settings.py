"""Add outreach AI content settings to workspace settings."""

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


OUTREACH_CONTENT_DEFAULT = """'{
  "general_prompt": "Write concise enterprise outbound emails for Beacon.li. Personalize to the contact and company, avoid hype, avoid fluff, and keep the CTA low-friction.",
  "linkedin_prompt": "Keep LinkedIn notes conversational and specific to the person''s role or recent company context.",
  "step_templates": [
    {
      "step_number": 1,
      "label": "Initial email",
      "goal": "Start a personalized conversation with a specific reason for reaching out.",
      "subject_hint": "Quick question about {{company_name}}",
      "body_template": "Hi {{first_name}},\\n\\nNoticed {{company_name}} is pushing on {{reason_to_reach_out}}. Beacon helps teams reduce implementation drag without replacing the systems they already run.\\n\\nWorth a quick compare?",
      "prompt_hint": "Open with a strong personalization point and end with a simple CTA."
    },
    {
      "step_number": 2,
      "label": "Follow-up",
      "goal": "Add one fresh signal or proof point without repeating the first note.",
      "subject_hint": "Re: {{company_name}} implementation motion",
      "body_template": "Hi {{first_name}},\\n\\nFollowing up with one more angle: teams like yours use Beacon to remove manual coordination from implementation work and get faster rollout consistency.\\n\\nHappy to share a quick example if useful.",
      "prompt_hint": "Reference the first email lightly and contribute one new idea, signal, or stat."
    },
    {
      "step_number": 3,
      "label": "Final touch",
      "goal": "Close the loop politely while keeping the door open.",
      "subject_hint": "Re: {{company_name}}",
      "body_template": "Hi {{first_name}},\\n\\nLast nudge from me. If implementation orchestration is on your roadmap this quarter, I can share what Beacon is doing for teams with similar rollout complexity.\\n\\nIf not relevant, no worries.",
      "prompt_hint": "Be brief, respectful, and easy to ignore without sounding passive-aggressive."
    }
  ]
}'::jsonb"""


def upgrade() -> None:
    op.add_column(
        "workspace_settings",
        sa.Column(
            "outreach_content_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(OUTREACH_CONTENT_DEFAULT),
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace_settings", "outreach_content_settings")
