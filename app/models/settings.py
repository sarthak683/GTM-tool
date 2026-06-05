"""
WorkspaceSettings — single global row storing org-level configuration.

Design: there is always exactly one row (id=1). The GET endpoint creates it
with defaults on first read so migrations don't need to seed data.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel, Column
from sqlalchemy import JSON, Text


class WorkspaceSettings(SQLModel, table=True):
    __tablename__ = "workspace_settings"

    id: int = Field(default=1, primary_key=True)

    # Outreach sequence defaults
    # e.g. [0, 3, 7] means Step 1 on Day 0, Step 2 on Day 3, Step 3 on Day 7
    outreach_step_delays: list[int] = Field(
        default=[0, 3, 7],
        sa_column=Column(JSON, nullable=False, server_default="[0, 3, 7]"),
    )
    outreach_content_settings: dict = Field(
        default={
            "general_prompt": (
                "Write concise enterprise outbound emails for Beacon.li. Personalize to the contact and company, "
                "avoid hype, avoid fluff, and keep the CTA low-friction."
            ),
            "linkedin_prompt": (
                "Keep LinkedIn notes conversational and specific to the person's role or recent company context."
            ),
            "step_templates": [
                {
                    "step_number": 1,
                    "label": "Initial email",
                    "goal": "Start a personalized conversation with a specific reason for reaching out.",
                    "subject_hint": "Quick question about {{company_name}}",
                    "body_template": (
                        "Hi {{first_name}},\n\n"
                        "Noticed {{company_name}} is pushing on {{reason_to_reach_out}}. Beacon helps teams reduce "
                        "implementation drag without replacing the systems they already run.\n\n"
                        "Worth a quick compare?"
                    ),
                    "prompt_hint": "Open with a strong personalization point and end with a simple CTA.",
                },
                {
                    "step_number": 2,
                    "label": "Follow-up",
                    "goal": "Add one fresh signal or proof point without repeating the first note.",
                    "subject_hint": "Re: {{company_name}} implementation motion",
                    "body_template": (
                        "Hi {{first_name}},\n\n"
                        "Following up with one more angle: teams like yours use Beacon to remove manual coordination "
                        "from implementation work and get faster rollout consistency.\n\n"
                        "Happy to share a quick example if useful."
                    ),
                    "prompt_hint": "Reference the first email lightly and contribute one new idea, signal, or stat.",
                },
                {
                    "step_number": 3,
                    "label": "Final touch",
                    "goal": "Close the loop politely while keeping the door open.",
                    "subject_hint": "Re: {{company_name}}",
                    "body_template": (
                        "Hi {{first_name}},\n\n"
                        "Last nudge from me. If implementation orchestration is on your roadmap this quarter, "
                        "I can share what Beacon is doing for teams with similar rollout complexity.\n\n"
                        "If not relevant, no worries."
                    ),
                    "prompt_hint": "Be brief, respectful, and easy to ignore without sounding passive-aggressive.",
                },
            ],
        },
        sa_column=Column(
            JSON,
            nullable=False,
            server_default=(
                '{"general_prompt":"Write concise enterprise outbound emails for Beacon.li. Personalize to the contact and company, avoid hype, avoid fluff, and keep the CTA low-friction.",'
                '"linkedin_prompt":"Keep LinkedIn notes conversational and specific to the person''s role or recent company context.",'
                '"step_templates":['
                '{"step_number":1,"label":"Initial email","goal":"Start a personalized conversation with a specific reason for reaching out.","subject_hint":"Quick question about {{company_name}}","body_template":"Hi {{first_name}},\\n\\nNoticed {{company_name}} is pushing on {{reason_to_reach_out}}. Beacon helps teams reduce implementation drag without replacing the systems they already run.\\n\\nWorth a quick compare?","prompt_hint":"Open with a strong personalization point and end with a simple CTA."},'
                '{"step_number":2,"label":"Follow-up","goal":"Add one fresh signal or proof point without repeating the first note.","subject_hint":"Re: {{company_name}} implementation motion","body_template":"Hi {{first_name}},\\n\\nFollowing up with one more angle: teams like yours use Beacon to remove manual coordination from implementation work and get faster rollout consistency.\\n\\nHappy to share a quick example if useful.","prompt_hint":"Reference the first email lightly and contribute one new idea, signal, or stat."},'
                '{"step_number":3,"label":"Final touch","goal":"Close the loop politely while keeping the door open.","subject_hint":"Re: {{company_name}}","body_template":"Hi {{first_name}},\\n\\nLast nudge from me. If implementation orchestration is on your roadmap this quarter, I can share what Beacon is doing for teams with similar rollout complexity.\\n\\nIf not relevant, no worries.","prompt_hint":"Be brief, respectful, and easy to ignore without sounding passive-aggressive."}'
                ']}'
            ),
        ),
    )
    deal_funnel_config: dict = Field(
        default={
            "active": ["reprospect", "demo_scheduled", "demo_done", "qualified_lead", "poc_agreed", "poc_wip", "poc_done", "commercial_negotiation", "msa_review"],
            "inactive": ["closed_won", "churned", "not_a_fit", "cold", "closed_lost", "on_hold", "nurture", "closed"],
            "tofu": ["qualified_lead", "poc_agreed"],
            "mofu": ["poc_wip", "poc_done", "commercial_negotiation", "msa_review", "workshop"],
            "bofu": ["closed_won"],
            "visible_cards": ["active", "inactive", "tofu", "mofu", "bofu", "total"],
        },
        sa_column=Column(
            JSON,
            nullable=False,
            server_default='{"active":["reprospect","demo_scheduled","demo_done","qualified_lead","poc_agreed","poc_wip","poc_done","commercial_negotiation","msa_review"],"inactive":["closed_won","churned","not_a_fit","cold","closed_lost","on_hold","nurture","closed"],"tofu":["qualified_lead","poc_agreed"],"mofu":["poc_wip","poc_done","commercial_negotiation","msa_review","workshop"],"bofu":["closed_won"],"visible_cards":["active","inactive","tofu","mofu","bofu","total"]}',
        ),
    )
    deal_stage_settings: list[dict] = Field(
        default=[
            {"id": "reprospect", "label": "REPROSPECT", "group": "active", "color": "#8b5cf6"},
            {"id": "demo_scheduled", "label": "4.DEMO SCHEDULED", "group": "active", "color": "#4f6ddf"},
            {"id": "demo_done", "label": "5.DEMO DONE", "group": "active", "color": "#1d4ed8"},
            {"id": "qualified_lead", "label": "6.QUALIFIED LEAD", "group": "active", "color": "#6d5efc"},
            {"id": "poc_agreed", "label": "7.POC AGREED", "group": "active", "color": "#0ea5e9"},
            {"id": "poc_wip", "label": "8.POC WIP", "group": "active", "color": "#06b6d4"},
            {"id": "poc_done", "label": "9.POC DONE", "group": "active", "color": "#14b8a6"},
            {"id": "commercial_negotiation", "label": "10.COMMERCIAL NEGOTIATION", "group": "active", "color": "#f59e0b"},
            {"id": "msa_review", "label": "11.WORKSHOP/MSA", "group": "active", "color": "#a855f7"},
            {"id": "closed_won", "label": "12.CLOSED WON", "group": "closed", "color": "#22c55e"},
            {"id": "churned", "label": "CHURNED", "group": "closed", "color": "#ef4444"},
            {"id": "not_a_fit", "label": "NOT FIT", "group": "closed", "color": "#9ca3af"},
            {"id": "cold", "label": "COLD", "group": "closed", "color": "#94a3b8"},
            {"id": "closed_lost", "label": "CLOSED LOST", "group": "closed", "color": "#7c8da4"},
            {"id": "on_hold", "label": "ON HOLD - REVISIT LATER", "group": "closed", "color": "#7c3aed"},
            {"id": "nurture", "label": "NURTURE - FUTURE FIT", "group": "closed", "color": "#2dd4bf"},
            {"id": "closed", "label": "CLOSED", "group": "closed", "color": "#64748b"},
        ],
        sa_column=Column(JSON, nullable=False),
    )
    role_permissions: dict = Field(
        default={
            "ae": {
                "crm_import": False,
                "prospect_migration": True,
                "manage_team": False,
                "run_pre_meeting_intel": True,
            },
            "sdr": {
                "crm_import": False,
                "prospect_migration": True,
                "manage_team": False,
                "run_pre_meeting_intel": False,
            },
        },
        sa_column=Column(
            JSON,
            nullable=False,
            server_default='{"ae":{"crm_import":false,"prospect_migration":true,"manage_team":false,"run_pre_meeting_intel":true},"sdr":{"crm_import":false,"prospect_migration":true,"manage_team":false,"run_pre_meeting_intel":false}}',
        ),
    )
    pre_meeting_automation_settings: dict = Field(
        default={
            "enabled": True,
            "send_hours_before": 12,
            "generate_hours_before": 48,
            "auto_generate_if_missing": True,
        },
        sa_column=Column(
            JSON,
            nullable=False,
            server_default='{"enabled":true,"send_hours_before":12,"generate_hours_before":48,"auto_generate_if_missing":true}',
        ),
    )
    prospect_stage_settings: list[dict] = Field(
        default=[
            {"id": "outreach", "label": "Outreach", "group": "active", "color": "#2563eb"},
            {"id": "in_progress", "label": "In Progress", "group": "active", "color": "#7c3aed"},
            {"id": "meeting_booked", "label": "Meeting Booked", "group": "active", "color": "#0ea5e9"},
            {"id": "negative_response", "label": "Negative Response", "group": "closed", "color": "#ef4444"},
            {"id": "no_response", "label": "No Response", "group": "closed", "color": "#94a3b8"},
            {"id": "not_a_fit", "label": "Not a Fit", "group": "closed", "color": "#9ca3af"},
        ],
        sa_column=Column(JSON, nullable=True),
    )
    sync_schedule_settings: dict = Field(
        default={
            "tldv_sync_enabled": True,
            "tldv_sync_interval_minutes": 5,
            "tldv_page_size": 10,
            "tldv_max_pages": 2,
            "tldv_last_synced_at": None,
            "email_sync_interval_seconds": 180,
            "deal_health_hour": 2,
        },
        sa_column=Column(
            JSON,
            nullable=False,
            server_default='{"tldv_sync_enabled":true,"tldv_sync_interval_minutes":5,"tldv_page_size":10,"tldv_max_pages":2,"tldv_last_synced_at":null,"email_sync_interval_seconds":180,"deal_health_hour":2}',
        ),
    )
    clickup_crm_settings: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    # Performance analytics — targets, RAG bands, stuck thresholds, stage probabilities.
    # Defaults live in app.core.analytics_defaults.build_default_analytics_settings();
    # the settings endpoint fills it in lazily on first read.
    analytics_settings: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    # Domains considered "internal" for meeting filtering and related heuristics.
    # Meetings whose attendees are all from these domains are dropped at sync time.
    internal_domains: list[str] = Field(
        default=["beacon.li"],
        sa_column=Column(
            JSON,
            nullable=False,
            server_default='["beacon.li"]',
        ),
    )
    prospect_funnel_config: dict = Field(
        default={
            "active": ["outreach", "in_progress", "meeting_booked"],
            "inactive": ["negative_response", "no_response", "not_a_fit"],
            "tofu": ["outreach"],
            "mofu": ["in_progress"],
            "bofu": ["meeting_booked"],
            "visible_cards": ["active", "inactive", "tofu", "mofu", "bofu", "total"],
        },
        sa_column=Column(
            JSON,
            nullable=False,
            server_default='{"active":["outreach","in_progress","meeting_booked"],"inactive":["negative_response","no_response","not_a_fit"],"tofu":["outreach"],"mofu":["in_progress"],"bofu":["meeting_booked"],"visible_cards":["active","inactive","tofu","mofu","bofu","total"]}',
        ),
    )

    # Gmail shared inbox sync
    gmail_shared_inbox: Optional[str] = Field(default=None)
    gmail_connected_email: Optional[str] = Field(default=None)
    gmail_connected_at: Optional[datetime] = Field(default=None)
    gmail_token_data: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    gmail_last_error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    report_sender_email: Optional[str] = Field(default=None)
    report_sender_connected_email: Optional[str] = Field(default=None)
    report_sender_connected_at: Optional[datetime] = Field(default=None)
    report_sender_token_data: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    report_sender_last_error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # Zippy agent — admin-editable override for the global system prompt.
    # NULL means "use the hardcoded default in zippy_agent.SYSTEM_PROMPT".
    zippy_system_prompt: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # Prospect visibility grants: user_ids (strings) of non-admins allowed to
    # see ALL prospects, not just their own + unassigned. Admins always see all;
    # this widens specific people. NULL/[] = nobody extra.
    prospect_view_all_user_ids: Optional[list] = Field(default=None, sa_column=Column(JSON, nullable=True))


# ── Pydantic schemas ──────────────────────────────────────────────────────────


class ZippySystemPromptRead(SQLModel):
    prompt: str
    is_default: bool  # true when no DB override is set and we're returning the code constant
    updated_at: Optional[datetime] = None


class ZippySystemPromptUpdate(SQLModel):
    prompt: str

class OutreachSettingsRead(SQLModel):
    step_delays: list[int]
    steps_count: int
    steps: list["OutreachTimingStep"] = Field(default_factory=list)


class OutreachSettingsUpdate(SQLModel):
    step_delays: list[int] = Field(default_factory=list)
    steps: list["OutreachTimingStep"] = Field(default_factory=list)


class OutreachTimingStep(SQLModel):
    step_number: int
    day: int
    channel: str = "email"


class OutreachTemplateStep(SQLModel):
    step_number: int
    channel: str = "email"
    label: str
    goal: str
    subject_hint: Optional[str] = None
    body_template: Optional[str] = None
    prompt_hint: Optional[str] = None


class OutreachContentSettingsRead(SQLModel):
    general_prompt: str
    linkedin_prompt: str
    step_templates: list[OutreachTemplateStep]


class OutreachContentSettingsUpdate(SQLModel):
    general_prompt: str
    linkedin_prompt: str
    step_templates: list[OutreachTemplateStep]


class DealFunnelSettingsRead(SQLModel):
    tofu: list[str]
    mofu: list[str]
    bofu: list[str]


class DealFunnelSettingsUpdate(SQLModel):
    tofu: list[str]
    mofu: list[str]
    bofu: list[str]


class StageBucketSettings(SQLModel):
    active: list[str] = Field(default_factory=list)
    inactive: list[str] = Field(default_factory=list)
    tofu: list[str]
    mofu: list[str]
    bofu: list[str]


class PipelineSummarySectionSettings(StageBucketSettings):
    visible_cards: list[str] = Field(default_factory=lambda: ["active", "inactive", "tofu", "mofu", "bofu", "total"])


class DealStageSetting(SQLModel):
    id: str
    label: str
    group: str
    color: str


class DealStageSettingsRead(SQLModel):
    stages: list[DealStageSetting]


class DealStageSettingsUpdate(SQLModel):
    stages: list[DealStageSetting]


class ProspectStageSettingsRead(SQLModel):
    stages: list[DealStageSetting]


class ProspectStageSettingsUpdate(SQLModel):
    stages: list[DealStageSetting]


class PipelineSummarySettingsRead(SQLModel):
    deal: PipelineSummarySectionSettings
    prospect: PipelineSummarySectionSettings


class PipelineSummarySettingsUpdate(SQLModel):
    deal: PipelineSummarySectionSettings
    prospect: PipelineSummarySectionSettings


class RolePermissionFlags(SQLModel):
    crm_import: bool
    prospect_migration: bool
    manage_team: bool
    run_pre_meeting_intel: bool


class RolePermissionsRead(SQLModel):
    ae: RolePermissionFlags
    sdr: RolePermissionFlags


class RolePermissionsUpdate(SQLModel):
    ae: RolePermissionFlags
    sdr: RolePermissionFlags


class PreMeetingAutomationSettingsRead(SQLModel):
    enabled: bool
    send_mode: str = "hours_before"
    send_time: str = "07:00"
    timezone: str = "UTC"
    send_hours_before: int
    generate_hours_before: int
    auto_generate_if_missing: bool


class PreMeetingAutomationSettingsUpdate(SQLModel):
    enabled: bool
    send_mode: str = "hours_before"
    send_time: str = "07:00"
    timezone: str = "UTC"
    send_hours_before: int
    generate_hours_before: int
    auto_generate_if_missing: bool


class ClickUpCrmSettingsRead(SQLModel):
    team_id: Optional[str] = None
    space_id: Optional[str] = None
    deals_list_id: Optional[str] = None


class ClickUpCrmSettingsUpdate(SQLModel):
    team_id: Optional[str] = None
    space_id: Optional[str] = None
    deals_list_id: Optional[str] = None


class SyncScheduleSettingsRead(SQLModel):
    tldv_sync_enabled: bool
    tldv_sync_interval_minutes: int
    tldv_page_size: int
    tldv_max_pages: int
    tldv_last_synced_at: Optional[str] = None
    email_sync_interval_seconds: int
    deal_health_hour: int
    # When True, emails are tracked ONLY via zippy+<deal-alias> CC; the broad
    # contact-fallback match and personal-inbox bulk sync are paused.
    zippy_only_email_sync: bool = False


class SyncScheduleSettingsUpdate(SQLModel):
    tldv_sync_enabled: Optional[bool] = None
    tldv_sync_interval_minutes: Optional[int] = None
    tldv_page_size: Optional[int] = None
    tldv_max_pages: Optional[int] = None
    email_sync_interval_seconds: Optional[int] = None
    deal_health_hour: Optional[int] = None
    zippy_only_email_sync: Optional[bool] = None


class ProspectVisibilityRead(SQLModel):
    """User ids of non-admins granted 'see all prospects' access."""
    user_ids: list[str] = []


class ProspectVisibilityUpdate(SQLModel):
    user_ids: list[str] = []


class GmailSettingsRead(SQLModel):
    configured: bool
    inbox: Optional[str] = None
    connected_email: Optional[str] = None
    connected_at: Optional[datetime] = None
    interval_seconds: int
    last_sync_epoch: Optional[int] = None
    last_error: Optional[str] = None


class GmailSettingsUpdate(SQLModel):
    inbox: str


class GmailConnectUrlRead(SQLModel):
    url: str


class ReportSenderSettingsRead(SQLModel):
    configured: bool
    sender_email: Optional[str] = None
    connected_email: Optional[str] = None
    connected_at: Optional[datetime] = None
    last_error: Optional[str] = None
    has_send_scope: bool = False


class ReportSenderSettingsUpdate(SQLModel):
    sender_email: str


class SalesReportSettingsRead(SQLModel):
    enabled: bool
    recipients: list[str]
    send_timezone: str
    send_hour: int
    send_minute: int
    cutoff_timezone: str
    cutoff_hour: int
    report_label_timezone: str
    send_days: list[str]
    weekly_report_day: str
    skip_weekends: bool
    nonprod_scheduled_enabled: bool
    nonprod_recipients: list[str]
    last_scheduled_send_key: Optional[str] = None
    last_scheduled_send_at: Optional[str] = None


class SalesReportSettingsUpdate(SQLModel):
    enabled: Optional[bool] = None
    recipients: Optional[list[str]] = None
    send_timezone: Optional[str] = None
    send_hour: Optional[int] = None
    send_minute: Optional[int] = None
    cutoff_timezone: Optional[str] = None
    cutoff_hour: Optional[int] = None
    report_label_timezone: Optional[str] = None
    send_days: Optional[list[str]] = None
    weekly_report_day: Optional[str] = None
    skip_weekends: Optional[bool] = None
    nonprod_scheduled_enabled: Optional[bool] = None
    nonprod_recipients: Optional[list[str]] = None
