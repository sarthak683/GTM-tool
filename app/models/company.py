from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import field_validator
from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Manual account-sourcing status. Canonical snake_case values stored in the DB;
# human labels live in ACCOUNT_STATUS_LABELS (app/api/v1/endpoints/analytics.py)
# and the frontend control (frontend/src/lib/accountStatus.ts, which owns the
# display order). Keep all three in lockstep.
ACCOUNT_STATUS_VALUES = {
    "cold",
    "in_progress",
    "meeting_booked",
    "meeting_done",
    "in_pipeline",
    "not_a_fit",
    "dnd",
    "reach_out_later",
}


class CompanyBase(SQLModel):
    name: str
    domain: str
    industry: Optional[str] = None
    vertical: Optional[str] = None
    employee_count: Optional[int] = None
    arr_estimate: Optional[float] = None
    funding_stage: Optional[str] = None
    region: Optional[str] = None  # e.g. "US", "EU", "APAC"
    headquarters: Optional[str] = None  # e.g. "Paris, France"
    has_dap: bool = False
    dap_tool: Optional[str] = None


class Company(CompanyBase, table=True):
    __tablename__ = "companies"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    tech_stack: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    icp_score: Optional[int] = None
    icp_tier: Optional[str] = None
    enrichment_sources: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    enriched_at: Optional[datetime] = None
    # Account sourcing fields
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    intent_signals: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    sourcing_batch_id: Optional[UUID] = Field(default=None, foreign_key="sourcing_batches.id", index=True)
    enrichment_cache: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    assigned_to_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    assigned_rep: Optional[str] = None
    assigned_rep_email: Optional[str] = Field(default=None, index=True)
    assigned_rep_name: Optional[str] = None
    sdr_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    sdr_email: Optional[str] = Field(default=None, index=True)
    sdr_name: Optional[str] = None
    outreach_status: Optional[str] = None
    disposition: Optional[str] = Field(default=None, index=True)
    # Manual sourcing status (see ACCOUNT_STATUS_VALUES). Distinct from
    # disposition; set by reps on the detail page.
    account_status: Optional[str] = Field(default=None, index=True)
    # Free-text quick notes SDRs keep on the account ("Outbound Summary"),
    # surfaced under the status control on the detail page.
    outbound_summary: Optional[str] = Field(default=None, sa_column=Column(Text))
    rep_feedback: Optional[str] = Field(default=None, sa_column=Column(Text))
    account_thesis: Optional[str] = Field(default=None, sa_column=Column(Text))
    why_now: Optional[str] = Field(default=None, sa_column=Column(Text))
    beacon_angle: Optional[str] = Field(default=None, sa_column=Column(Text))
    recommended_outreach_lane: Optional[str] = Field(default=None, index=True)
    instantly_campaign_id: Optional[str] = None
    prospecting_profile: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    outreach_plan: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    last_outreach_at: Optional[datetime] = None
    # Investor mapping fields
    ownership_stage: Optional[str] = None  # e.g. "PE-backed (KKR)", "Public (NYSE: BILL)"
    priority_tag: Optional[str] = None  # "P0", "P1", "P2"
    pe_investors: Optional[str] = Field(default=None, sa_column=Column(Text))
    vc_investors: Optional[str] = Field(default=None, sa_column=Column(Text))
    strategic_investors: Optional[str] = Field(default=None, sa_column=Column(Text))
    # Who added this account (manual add or CSV/Excel upload). created_by_name is
    # denormalized — like assigned_rep_name/sdr_name — so the UI can show
    # "Added by X" without a join. System-created rows (imports, AI sourcing,
    # seed) leave these null.
    created_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    created_by_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Opportunity Details (AE-filled, account-level) ────────────────────────
    # Core deal fields
    opp_name: Optional[str] = None
    opp_amount: Optional[float] = None
    opp_arr: Optional[float] = None
    opp_multiyear_license_fee: Optional[float] = None
    opp_service_fee: Optional[float] = None
    opp_type: Optional[str] = None
    opp_sales_category: Optional[str] = None
    opp_geolocation: Optional[str] = None
    opp_owner: Optional[str] = None
    opp_solution_engineer: Optional[str] = None
    opp_close_date: Optional[date] = None
    opp_forecast_category: Optional[str] = None
    opp_probability: Optional[float] = None
    opp_stage: Optional[str] = None
    opp_poc_start_date: Optional[date] = None
    opp_poc_status: Optional[str] = None
    opp_aop_doc_link: Optional[str] = None
    opp_msp_doc_link: Optional[str] = None
    # MEDDPICC
    medd_business_initiatives: Optional[str] = Field(default=None, sa_column=Column("medd_business_initiatives", Text))
    medd_business_pains: Optional[str] = Field(default=None, sa_column=Column("medd_business_pains", Text))
    medd_technical_pains: Optional[str] = Field(default=None, sa_column=Column("medd_technical_pains", Text))
    medd_size_business_pain: Optional[float] = None
    medd_who_impacted_business: Optional[str] = Field(default=None, sa_column=Column("medd_who_impacted_business", Text))
    medd_size_technical_pain: Optional[float] = None
    medd_who_impacted_technical: Optional[str] = Field(default=None, sa_column=Column("medd_who_impacted_technical", Text))
    medd_metrics: Optional[str] = Field(default=None, sa_column=Column("medd_metrics", Text))
    medd_decision_criteria: Optional[str] = Field(default=None, sa_column=Column("medd_decision_criteria", Text))
    medd_economic_buyer: Optional[str] = None
    medd_eb_top_2_priorities: Optional[str] = Field(default=None, sa_column=Column("medd_eb_top_2_priorities", Text))
    medd_decision_process: Optional[str] = Field(default=None, sa_column=Column("medd_decision_process", Text))
    medd_paper_process: Optional[str] = Field(default=None, sa_column=Column("medd_paper_process", Text))
    medd_champion: Optional[str] = None
    medd_champion_win: Optional[str] = Field(default=None, sa_column=Column("medd_champion_win", Text))
    medd_competition: Optional[str] = Field(default=None, sa_column=Column("medd_competition", Text))
    # Current deal status note
    opp_current_deal_status: Optional[str] = Field(default=None, sa_column=Column("opp_current_deal_status", Text))


class CompanyCreate(CompanyBase):
    tech_stack: Optional[Any] = None


class CompanyRead(CompanyBase):
    id: UUID
    tech_stack: Optional[Any] = None
    icp_score: Optional[int] = None
    icp_tier: Optional[str] = None
    enrichment_sources: Optional[Any] = None
    enriched_at: Optional[datetime] = None
    description: Optional[str] = None
    intent_signals: Optional[Any] = None
    sourcing_batch_id: Optional[UUID] = None
    enrichment_cache: Optional[Any] = None
    assigned_to_id: Optional[UUID] = None
    assigned_to_name: Optional[str] = None  # populated via JOIN
    assigned_rep: Optional[str] = None
    assigned_rep_email: Optional[str] = None
    assigned_rep_name: Optional[str] = None
    sdr_id: Optional[UUID] = None
    sdr_email: Optional[str] = None
    sdr_name: Optional[str] = None
    outreach_status: Optional[str] = None
    disposition: Optional[str] = None
    account_status: Optional[str] = None
    outbound_summary: Optional[str] = None
    rep_feedback: Optional[str] = None
    account_thesis: Optional[str] = None
    why_now: Optional[str] = None
    beacon_angle: Optional[str] = None
    recommended_outreach_lane: Optional[str] = None
    instantly_campaign_id: Optional[str] = None
    prospecting_profile: Optional[Any] = None
    outreach_plan: Optional[Any] = None
    last_outreach_at: Optional[datetime] = None
    ownership_stage: Optional[str] = None
    priority_tag: Optional[str] = None
    pe_investors: Optional[str] = None
    vc_investors: Optional[str] = None
    strategic_investors: Optional[str] = None
    created_by_id: Optional[UUID] = None
    created_by_name: Optional[str] = None
    # Recotap ABM signals (journey stage, score, engagement, intent sub-scores),
    # joined by domain from recotap_accounts. Populated only by the Account
    # Sourcing endpoints — no rtp_* columns on the companies table itself.
    recotap: Optional[Any] = None
    created_at: datetime
    updated_at: datetime
    # Opportunity Details
    opp_name: Optional[str] = None
    opp_amount: Optional[float] = None
    opp_arr: Optional[float] = None
    opp_multiyear_license_fee: Optional[float] = None
    opp_service_fee: Optional[float] = None
    opp_type: Optional[str] = None
    opp_sales_category: Optional[str] = None
    opp_geolocation: Optional[str] = None
    opp_owner: Optional[str] = None
    opp_solution_engineer: Optional[str] = None
    opp_close_date: Optional[date] = None
    opp_forecast_category: Optional[str] = None
    opp_probability: Optional[float] = None
    opp_stage: Optional[str] = None
    opp_poc_start_date: Optional[date] = None
    opp_poc_status: Optional[str] = None
    opp_aop_doc_link: Optional[str] = None
    opp_msp_doc_link: Optional[str] = None
    medd_business_initiatives: Optional[str] = None
    medd_business_pains: Optional[str] = None
    medd_technical_pains: Optional[str] = None
    medd_size_business_pain: Optional[float] = None
    medd_who_impacted_business: Optional[str] = None
    medd_size_technical_pain: Optional[float] = None
    medd_who_impacted_technical: Optional[str] = None
    medd_metrics: Optional[str] = None
    medd_decision_criteria: Optional[str] = None
    medd_economic_buyer: Optional[str] = None
    medd_eb_top_2_priorities: Optional[str] = None
    medd_decision_process: Optional[str] = None
    medd_paper_process: Optional[str] = None
    medd_champion: Optional[str] = None
    medd_champion_win: Optional[str] = None
    medd_competition: Optional[str] = None
    opp_current_deal_status: Optional[str] = None


class CompanySourcingSummary(SQLModel):
    total_companies: int
    hot_count: int
    warm_count: int
    high_priority_count: int
    engaged_count: int
    unresolved_count: int
    unenriched_count: int
    researched_count: int
    target_verdict_count: int
    watch_verdict_count: int
    enriched_count: int
    total_contacts: int


class CompanyUpdate(SQLModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    industry: Optional[str] = None
    vertical: Optional[str] = None
    employee_count: Optional[int] = None
    arr_estimate: Optional[float] = None
    funding_stage: Optional[str] = None
    region: Optional[str] = None
    headquarters: Optional[str] = None
    tech_stack: Optional[Any] = None
    has_dap: Optional[bool] = None
    dap_tool: Optional[str] = None
    icp_score: Optional[int] = None
    icp_tier: Optional[str] = None
    enrichment_sources: Optional[Any] = None
    enriched_at: Optional[datetime] = None
    intent_signals: Optional[Any] = None
    description: Optional[str] = None
    sourcing_batch_id: Optional[UUID] = None
    enrichment_cache: Optional[Any] = None
    assigned_to_id: Optional[UUID] = None
    assigned_rep: Optional[str] = None
    assigned_rep_email: Optional[str] = None
    assigned_rep_name: Optional[str] = None
    sdr_id: Optional[UUID] = None
    sdr_email: Optional[str] = None
    sdr_name: Optional[str] = None
    outreach_status: Optional[str] = None
    disposition: Optional[str] = None
    account_status: Optional[str] = None
    outbound_summary: Optional[str] = None
    rep_feedback: Optional[str] = None
    account_thesis: Optional[str] = None
    why_now: Optional[str] = None
    beacon_angle: Optional[str] = None
    recommended_outreach_lane: Optional[str] = None
    instantly_campaign_id: Optional[str] = None
    prospecting_profile: Optional[Any] = None
    outreach_plan: Optional[Any] = None
    last_outreach_at: Optional[datetime] = None
    ownership_stage: Optional[str] = None
    priority_tag: Optional[str] = None
    pe_investors: Optional[str] = None
    vc_investors: Optional[str] = None
    strategic_investors: Optional[str] = None
    # Opportunity Details
    opp_name: Optional[str] = None
    opp_amount: Optional[float] = None
    opp_arr: Optional[float] = None
    opp_multiyear_license_fee: Optional[float] = None
    opp_service_fee: Optional[float] = None
    opp_type: Optional[str] = None
    opp_sales_category: Optional[str] = None
    opp_geolocation: Optional[str] = None
    opp_owner: Optional[str] = None
    opp_solution_engineer: Optional[str] = None
    opp_close_date: Optional[date] = None
    opp_forecast_category: Optional[str] = None
    opp_probability: Optional[float] = None
    opp_stage: Optional[str] = None
    opp_poc_start_date: Optional[date] = None
    opp_poc_status: Optional[str] = None
    opp_aop_doc_link: Optional[str] = None
    opp_msp_doc_link: Optional[str] = None
    medd_business_initiatives: Optional[str] = None
    medd_business_pains: Optional[str] = None
    medd_technical_pains: Optional[str] = None
    medd_size_business_pain: Optional[float] = None
    medd_who_impacted_business: Optional[str] = None
    medd_size_technical_pain: Optional[float] = None
    medd_who_impacted_technical: Optional[str] = None
    medd_metrics: Optional[str] = None
    medd_decision_criteria: Optional[str] = None
    medd_economic_buyer: Optional[str] = None
    medd_eb_top_2_priorities: Optional[str] = None
    medd_decision_process: Optional[str] = None
    medd_paper_process: Optional[str] = None
    medd_champion: Optional[str] = None
    medd_champion_win: Optional[str] = None
    medd_competition: Optional[str] = None
    opp_current_deal_status: Optional[str] = None

    @field_validator("account_status", mode="before")
    @classmethod
    def _validate_account_status(cls, value):
        """Accept a canonical status, blank (→ clear), or reject anything else."""
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if not normalized:
            return None
        if normalized not in ACCOUNT_STATUS_VALUES:
            raise ValueError(
                f"account_status must be one of {sorted(ACCOUNT_STATUS_VALUES)} or empty"
            )
        return normalized
