from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


# ── Stage definitions ────────────────────────────────────────────────────────

DEAL_STAGES = [
    "reprospect", "demo_scheduled", "demo_done", "qualified_lead",
    "poc_agreed", "poc_wip", "poc_done", "commercial_negotiation", "msa_review", "workshop",
    "closed_won", "closed_lost", "not_a_fit", "cold", "on_hold", "nurture", "churned", "closed",
]

PROSPECT_STAGES = [
    "cold_account", "prospecting", "in_progress", "converted", "blocked", "not_a_fit",
]

ALL_STAGES = frozenset(DEAL_STAGES + PROSPECT_STAGES)

PRIORITIES = frozenset(["urgent", "high", "normal", "low"])

# ── MEDDPICC qualification framework ────────────────────────────────────────

MEDDPICC_FIELDS = [
    "metrics", "economic_buyer", "decision_criteria", "decision_process",
    "paper_process", "identify_pain", "champion", "competition",
]

def compute_meddpicc_score(qualification: dict | None) -> int | None:
    """Compute MEDDPICC score (0-100) from qualification.meddpicc dict.

    Each of the 8 dimensions is scored 0-3 (not_started, identified,
    validated, confirmed).  Total 0-24, scaled to 0-100.
    """
    if not qualification:
        return None
    meddpicc = qualification.get("meddpicc")
    if not meddpicc or not isinstance(meddpicc, dict):
        return None
    total = sum(meddpicc.get(f, 0) for f in MEDDPICC_FIELDS)
    filled = sum(1 for f in MEDDPICC_FIELDS if meddpicc.get(f, 0) > 0)
    if filled == 0:
        return None
    return round(total / 24 * 100)


# ── Deal ─────────────────────────────────────────────────────────────────────

class DealBase(SQLModel):
    name: str
    pipeline_type: str = "deal"
    stage: str = "reprospect"
    priority: str = "normal"
    department: Optional[str] = None
    geography: Optional[str] = None
    source: Optional[str] = None
    close_date_est: Optional[date] = None
    health: str = "green"
    health_score: Optional[int] = None
    days_in_stage: int = 0
    stage_entered_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    stakeholder_count: int = 0
    owner_id: Optional[str] = None


class Deal(DealBase, table=True):
    __tablename__ = "deals"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    company_id: Optional[UUID] = Field(default=None, foreign_key="companies.id", index=True)
    assigned_to_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)  # AE
    # Originating SDR, stamped from the contact at conversion so SDR-sourced
    # pipeline credit survives. Mirrors Contact.sdr_id (no hard FK, just an index).
    sdr_id: Optional[UUID] = Field(default=None, index=True)
    email_cc_alias: Optional[str] = Field(default=None, index=True)
    external_source: Optional[str] = Field(default=None, index=True)
    external_source_id: Optional[str] = Field(default=None, index=True)
    value: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    qualification: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    tags: list[str] = Field(default=[], sa_column=Column(JSONB, nullable=False, server_default="[]"))
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    next_step: Optional[str] = Field(default=None, sa_column=Column(Text))
    # Timestamp for next_step itself — only moves when next_step text changes.
    # last_activity_at and updated_at are too noisy (any activity / any column
    # write bumps them), so the UI can't use them to answer "is this note
    # current?". Set in the create/update endpoints when next_step diffs.
    next_step_updated_at: Optional[datetime] = None
    # When the next step is due. Drives the pipeline reminder task, which fires a
    # one-time in-app notification to the assigned rep when this time passes.
    next_step_due_at: Optional[datetime] = None
    commit_to_deal: bool = Field(default=False)
    ai_tasks_refreshed_at: Optional[datetime] = None
    ai_tasks_input_hash: Optional[str] = None
    ai_tasks_refresh_requested_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DealCreate(SQLModel):
    name: str
    pipeline_type: str = "deal"
    stage: Optional[str] = None
    priority: str = "normal"
    company_id: Optional[UUID] = None
    assigned_to_id: Optional[UUID] = None
    sdr_id: Optional[UUID] = None
    value: Optional[Decimal] = None
    close_date_est: Optional[date] = None
    department: Optional[str] = None
    geography: Optional[str] = None
    source: Optional[str] = None
    description: Optional[str] = None
    next_step: Optional[str] = None
    next_step_due_at: Optional[datetime] = None
    tags: list[str] = []
    qualification: Optional[Any] = None
    health: str = "green"
    owner_id: Optional[str] = None
    email_cc_alias: Optional[str] = None


class DealRead(DealBase):
    id: UUID
    company_id: Optional[UUID] = None
    assigned_to_id: Optional[UUID] = None
    sdr_id: Optional[UUID] = None
    email_cc_alias: Optional[str] = None
    value: Optional[Decimal] = None
    qualification: Optional[Any] = None
    tags: list[str] = []
    description: Optional[str] = None
    next_step: Optional[str] = None
    next_step_updated_at: Optional[datetime] = None
    next_step_due_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    # Joined fields populated by board/detail queries
    company_name: Optional[str] = None
    assigned_rep_name: Optional[str] = None
    contact_count: int = 0
    # Computed from qualification.meddpicc
    meddpicc_score: Optional[int] = None
    seller_engagement_at: Optional[datetime] = None
    client_engagement_at: Optional[datetime] = None
    # Signal justification — what activity drove the latest touch
    seller_engagement_signal: Optional[dict] = None  # {type, source, label}
    client_engagement_signal: Optional[dict] = None  # {type, source, label}
    seller_engagement_reason: Optional[str] = None
    client_engagement_reason: Optional[str] = None
    commit_to_deal: bool = False
    ai_tasks_refreshed_at: Optional[datetime] = None
    ai_tasks_refresh_requested_at: Optional[datetime] = None
    # Flag matrix — derived from qualification.meddpicc + meddpicc_details.
    # Populated alongside meddpicc_score in DealRepository.
    flags: Optional[dict[str, str]] = None  # e.g. {"metrics": "green", "champion": "yellow", ...}
    forecast_category: Optional[str] = None  # "commit" | "best_case" | "pipeline"
    flag_green_count: Optional[int] = None
    flag_yellow_count: Optional[int] = None
    flag_red_count: Optional[int] = None
    flag_blockers: Optional[list[str]] = None
    flag_yellows: Optional[list[str]] = None


class DealUpdate(SQLModel):
    name: Optional[str] = None
    pipeline_type: Optional[str] = None
    stage: Optional[str] = None
    priority: Optional[str] = None
    company_id: Optional[UUID] = None
    assigned_to_id: Optional[UUID] = None
    value: Optional[Decimal] = None
    close_date_est: Optional[date] = None
    health: Optional[str] = None
    health_score: Optional[int] = None
    qualification: Optional[Any] = None
    tags: Optional[list[str]] = None
    department: Optional[str] = None
    geography: Optional[str] = None
    source: Optional[str] = None
    description: Optional[str] = None
    next_step: Optional[str] = None
    next_step_due_at: Optional[datetime] = None
    days_in_stage: Optional[int] = None
    stage_entered_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    stakeholder_count: Optional[int] = None
    owner_id: Optional[str] = None
    email_cc_alias: Optional[str] = None
    commit_to_deal: Optional[bool] = None


# ── DealContact junction ─────────────────────────────────────────────────────

class DealContact(SQLModel, table=True):
    __tablename__ = "deal_contacts"

    deal_id: UUID = Field(foreign_key="deals.id", primary_key=True)
    contact_id: UUID = Field(foreign_key="contacts.id", primary_key=True)
    role: Optional[str] = None
    added_at: datetime = Field(default_factory=datetime.utcnow)


class DealContactCreate(SQLModel):
    contact_id: UUID
    role: Optional[str] = None


class DealContactRead(SQLModel):
    deal_id: UUID
    contact_id: UUID
    role: Optional[str] = None
    added_at: datetime
    # Joined contact fields
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    title: Optional[str] = None
    persona: Optional[str] = None
