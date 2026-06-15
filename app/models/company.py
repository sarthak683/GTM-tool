from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


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
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


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
    created_at: datetime
    updated_at: datetime


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
