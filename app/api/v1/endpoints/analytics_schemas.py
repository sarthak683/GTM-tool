"""Response models for the analytics endpoints.

These 21 Pydantic models are the wire contract of app/api/v1/endpoints/
analytics.py. They sat interleaved with that module's query code, where 292
lines of pure shape declaration separated the reader from the six route
handlers. Nothing outside analytics.py imports them (checked across app/,
tests/, scripts/ and alembic/), so they move as one block with no call-site
changes -- analytics.py re-imports every name, which keeps `response_model=`
annotations working exactly as before.

Note the frontend mirrors several of these names as TypeScript interfaces in
frontend/src/lib/api.ts and pages/SalesAnalytics.tsx. Those are hand-maintained
copies, not generated -- changing a field here means changing it there too.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class MilestoneDealRow(BaseModel):
    milestone_key: str
    deal_name: Optional[str] = None
    company_name: Optional[str] = None
    reached_at: str
    close_date_est: Optional[str] = None
    deal_value: Optional[float] = None
    assigned_ae: Optional[str] = None
    assigned_sdr: Optional[str] = None
    meeting_booked_with: Optional[str] = None


class SalesSummary(BaseModel):
    pipeline_amount: float
    weighted_pipeline_amount: float
    forecast_amount: float
    active_deals: int
    average_deal_size: float
    overdue_close_count: int
    missing_close_date_count: int
    stale_deal_count: int
    # Milestone-based counts (deduplicated: first time per company)
    demo_scheduled_count: int = 0
    qualified_lead_count: int = 0
    demo_done_count: int = 0
    poc_agreed_count: int = 0
    poc_wip_count: int = 0
    poc_done_count: int = 0
    commercial_negotiation_count: int = 0
    workshop_msa_count: int = 0
    closed_won_count: int = 0
    closed_won_value: float = 0.0
    milestone_deals: list[MilestoneDealRow] = []
    # Same metrics for the immediately-preceding window of equal length, so the
    # UI can render period-over-period trend deltas on the milestone KPIs. These
    # are window-bound counts (point-in-time pipeline metrics are not compared).
    prev_demo_scheduled_count: int = 0
    prev_qualified_lead_count: int = 0
    prev_demo_done_count: int = 0
    prev_poc_agreed_count: int = 0
    prev_poc_wip_count: int = 0
    prev_poc_done_count: int = 0
    prev_commercial_negotiation_count: int = 0
    prev_workshop_msa_count: int = 0
    prev_closed_won_count: int = 0
    prev_closed_won_value: float = 0.0


class RepActivityRow(BaseModel):
    key: str
    user_id: Optional[UUID] = None
    rep_name: str
    # "ae" | "sdr" | None. Drives the SDR/AE leaderboard split on the client.
    role: Optional[str] = None
    calls: int
    connected_calls: int = 0
    live_calls: int = 0
    emails: int
    manual_emails: int = 0
    instantly_emails: int = 0
    email_opens: int = 0
    email_replies: int = 0
    linkedin_reachouts: int = 0
    linkedin_accepted: int = 0
    linkedin_meeting_booked: int = 0
    email_meeting_booked: int = 0
    call_meeting_booked: int = 0
    meetings: int
    total: int
    active_deals: int
    pipeline_amount: float
    # Upcoming scheduled meetings bucketed by how far ahead they are from today
    meetings_next_1w: int = 0
    meetings_next_2w: int = 0
    meetings_beyond_2w: int = 0
    # Meetings with VP/SVP/Head/Chief within the selected analytics window
    direct_sql: int = 0
    # SDR demo funnel (attributed to the account's SDR). demos_converted counts
    # done demos whose account reached a qualified deal or beyond.
    demos_scheduled: int = 0
    demos_done: int = 0
    demos_converted: int = 0
    # AE demo funnel — only deals where sdr_id == assigned_to_id (AE sourced their own deal).
    ae_demos_scheduled: int = 0
    ae_demos_done: int = 0
    ae_demos_converted: int = 0
    # Call touchpoint breakdown
    call_first_attempt: int = 0
    call_second_plus: int = 0
    # Email touchpoint breakdown
    email_first_attempt: int = 0
    email_min_3_attempts: int = 0
    # LinkedIn touchpoint breakdown
    linkedin_connection_requested: int = 0
    linkedin_intro_msg: int = 0
    linkedin_followup_msg: int = 0
    # Prospect / contact coverage
    total_prospects: int = 0
    total_mobile_numbers: int = 0
    # Demo reschedules: deal in demo_scheduled stage with close_date_est updated
    # after it was already set. Logged as Activity(type="demo_rescheduled").
    demos_rescheduled: int = 0
    # Same headline metrics for the equal-length window immediately preceding
    # window_start, with identical rep/geography scoping — powers the
    # period-over-period deltas on the leaderboard rows. Follows the prev_*
    # naming convention established on SalesSummary.
    prev_total: int = 0
    prev_calls: int = 0
    prev_connected_calls: int = 0
    prev_live_calls: int = 0
    prev_emails: int = 0
    prev_linkedin_reachouts: int = 0
    prev_meetings: int = 0


class RepActivityWeekRow(BaseModel):
    week_key: str
    label: str
    week_start: str
    week_end: str
    emails: int = 0
    manual_emails: int = 0
    instantly_emails: int = 0
    calls: int = 0
    connected_calls: int = 0
    live_calls: int = 0
    linkedin_reachouts: int = 0
    meetings: int = 0
    total: int = 0


class RepWeeklyActivityRow(BaseModel):
    key: str
    user_id: Optional[UUID] = None
    rep_name: str
    active_deals: int
    pipeline_amount: float
    totals: RepActivityRow
    weeks: list[RepActivityWeekRow]


class StageBucket(BaseModel):
    key: str
    label: str
    color: str
    deal_count: int
    amount: float
    weighted_amount: float = 0


class PipelineOwnerRow(BaseModel):
    key: str
    user_id: Optional[UUID] = None
    rep_name: str
    deal_count: int
    amount: float
    weighted_amount: float
    stages: list[StageBucket]


class VelocityRow(BaseModel):
    key: str
    label: str
    color: str
    deal_count: int
    average_days_in_stage: float
    stale_deals: int


class ForecastRow(BaseModel):
    key: str
    label: str
    deal_count: int
    amount: float
    weighted_amount: float


class QuotaState(BaseModel):
    configured: bool
    title: str
    message: str
    # Target maps straight from analytics_settings (key → metric → target;
    # keys are the settings' grouping — role buckets like "sdr"/"ae" today,
    # rep ids if an admin configures per-rep maps). All optional so existing
    # clients and cached payloads keep validating.
    weekly_targets: Optional[dict[str, dict[str, float]]] = None
    monthly_targets: Optional[dict[str, dict[str, float]]] = None
    # Simple team aggregate: per metric, the sum of that metric's target across
    # every key in the corresponding map — the "team goal line" value.
    team_weekly_targets: Optional[dict[str, float]] = None
    team_monthly_targets: Optional[dict[str, float]] = None


class SalesHighlightDrilldown(BaseModel):
    entity_type: Literal["deal"] = "deal"
    stage_key: Optional[str] = None
    rep_user_id: Optional[UUID] = None
    stalled_only: bool = False
    overdue_close_date: bool = False
    missing_close_date: bool = False
    close_month: Optional[str] = None


class SalesHighlight(BaseModel):
    key: str
    message: str
    title: Optional[str] = None
    subtitle: Optional[str] = None
    drilldown: Optional[SalesHighlightDrilldown] = None


class MonthlyUniqueFunnelRow(BaseModel):
    month_key: str
    label: str
    demo_done: int
    poc_agreed: int = 0
    poc_wip: int
    poc_done: int
    closed_won: int


class MeetingBucketTotals(BaseModel):
    meetings_next_1w: int = 0
    meetings_next_2w: int = 0
    meetings_beyond_2w: int = 0
    direct_sql: int = 0
    demo_rescheduled: int = 0


class SalesDashboardRead(BaseModel):
    generated_at: datetime
    window_days: int
    # The resolved window, inclusive on both ends — always populated, including
    # for preset picks where the request carried no explicit dates.
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    # Bucket size of rep_weekly_activity, derived from the RESOLVED window
    # (≤14 days → daily), so the client can label the chart correctly.
    activity_bucket_granularity: Literal["daily", "weekly"] = "weekly"
    summary: SalesSummary
    highlights: list[SalesHighlight]
    rep_activity: list[RepActivityRow]
    rep_weekly_activity: list[RepWeeklyActivityRow]
    pipeline_by_stage: list[StageBucket]
    pipeline_by_owner: list[PipelineOwnerRow]
    velocity_by_stage: list[VelocityRow]
    forecast_by_month: list[ForecastRow]
    forecast_by_week: list[ForecastRow] = []
    forecast_buckets: list[ForecastRow] = []
    forecast_granularity: str = "month"
    monthly_unique_funnel: list[MonthlyUniqueFunnelRow]
    quota: QuotaState
    # Global (unique) counts for the Output "Meetings Booked" buckets. Each demo
    # is counted once here so the week buckets reconcile to the Demo Scheduled
    # total; rep_activity keeps per-rep attribution for the leaderboards.
    meeting_bucket_totals: "MeetingBucketTotals" = None


class SalesActivityDrilldownRow(BaseModel):
    id: UUID
    kind: Literal["activity", "meeting"]
    activity_type: str
    occurred_at: datetime
    rep_user_id: Optional[UUID] = None
    rep_name: str
    source: Optional[str] = None
    source_label: Optional[str] = None
    subject: Optional[str] = None
    direction: Optional[str] = None
    from_email: Optional[str] = None
    to_email: Optional[str] = None
    call_outcome: Optional[str] = None
    call_duration: Optional[int] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    company_name: Optional[str] = None
    deal_name: Optional[str] = None
    # Optional entity ids so a drilldown row can navigate to a deal/company.
    # Populated cheaply (pass-through of ids already on the row); we do not run
    # extra lookups just to fill these for activity rows.
    deal_id: Optional[str] = None
    company_id: Optional[str] = None
    email_body: Optional[str] = None  # full email body for expand-in-drilldown (1.2)
    # Deal-level AE/SDR assignment, populated for pipeline-backed drilldowns
    # (demo funnel) so leaderboard rows show who owns the deal.
    ae_name: Optional[str] = None
    sdr_name: Optional[str] = None


class SalesActivityDrilldownRead(BaseModel):
    generated_at: datetime
    metric: str
    window_days: int
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    rep_user_id: Optional[UUID] = None
    rep_name: Optional[str] = None
    returned_count: int
    has_more: bool
    limit: int
    offset: int
    rows: list[SalesActivityDrilldownRow]


class MeetingBucketDealRow(BaseModel):
    deal_id: str
    deal_name: str
    ae_name: str
    sdr_name: str
    date_of_meeting: Optional[str] = None
    meeting_booked_with: Optional[str] = None


class MeetingBucketDealsResponse(BaseModel):
    deals: list[MeetingBucketDealRow]


class PipelineDealItem(BaseModel):
    deal_id: str
    deal_name: str
    stage: str
    stage_label: str
    deal_value: float
    ae_name: Optional[str]


class MeetingBookedFromDealItem(BaseModel):
    deal_id: str
    deal_name: str
    meeting_booked_with: Optional[str]
    meeting_booked_from: Optional[str]
    ae_name: Optional[str]
    sdr_name: Optional[str]
