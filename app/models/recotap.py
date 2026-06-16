"""Recotap ABM account intelligence.

One row per account, keyed by ``domain`` so it can be joined to the sales
``companies`` table without coupling to it (no ``rtp_*`` columns on companies).
Stores the signals Recotap returns — journey stage, account score, engagement,
and the intent sub-scores — so the Account Sourcing UI can surface them.

See docs/RECOTAP_INTEGRATION.md §4.1 for the full contract.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Verified sandbox journey-stage order, low → high intent (GET /journey-stages).
RECOTAP_JOURNEY_STAGES = ["Unaware", "Aware", "Consideration", "Opportunity", "Customer"]
RECOTAP_ENGAGEMENT_LEVELS = ["Cold", "Warm", "Hot"]


class RecotapAccount(SQLModel, table=True):
    __tablename__ = "recotap_accounts"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    rtp_aid: Optional[str] = Field(default=None, index=True)  # Recotap PK; null until pushed
    domain: str = Field(index=True)                           # join/dedup key (lowercased)
    name: Optional[str] = None
    external_id: Optional[str] = None
    # FK enforces ON DELETE SET NULL at the DB level via migration 091 (SQLModel's
    # foreign_key= shorthand can't express ondelete). Deleting a company nulls this
    # link instead of raising; the row re-links by domain on the next Recotap pull.
    company_id: Optional[UUID] = Field(default=None, foreign_key="companies.id", index=True)
    tags: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    journey_stage: Optional[str] = None
    score: Optional[int] = None                               # rtp_account_score (0-100)
    engagement: Optional[str] = None                          # Cold / Warm / Hot
    icp_fit: Optional[str] = None                             # ICP Fit label
    advertising_activity_score: Optional[int] = None
    website_intent_score: Optional[int] = None
    g2_intent_score: Optional[int] = None
    bombora_intent_score: Optional[int] = None
    hq_location: Optional[str] = None
    last_account_date: Optional[datetime] = None
    raw: Optional[Any] = Field(default=None, sa_column=Column(JSONB))
    # "recotap" = pulled from the live API; "seed" = locally generated mock so the
    # UI has data to work with while the sandbox hasn't scored real accounts.
    # "pending" = freshly created, not yet populated by either path.
    source: str = Field(default="pending")
    pulled_at: Optional[datetime] = None
    pushed_at: Optional[datetime] = None        # last successful push to Recotap
    push_status: Optional[str] = None           # created / updated / failed / error
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RecotapAccountRead(SQLModel):
    domain: str
    name: Optional[str] = None
    rtp_aid: Optional[str] = None
    journey_stage: Optional[str] = None
    score: Optional[int] = None
    engagement: Optional[str] = None
    icp_fit: Optional[str] = None
    advertising_activity_score: Optional[int] = None
    website_intent_score: Optional[int] = None
    g2_intent_score: Optional[int] = None
    bombora_intent_score: Optional[int] = None
    hq_location: Optional[str] = None
    last_account_date: Optional[datetime] = None
    source: str = "recotap"
