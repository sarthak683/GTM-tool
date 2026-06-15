"""
Sales Knowledge Base — uploadable resources (ROI templates, case studies,
competitive intel, product docs, etc.) that feed context into AI modules.

Each resource declares which modules it's relevant to via the `modules`
JSONB list, enabling targeted context injection into prompts.
"""
from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class SalesResource(SQLModel, table=True):
    __tablename__ = "sales_resources"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Core fields
    title: str = Field(max_length=255)
    category: str = Field(max_length=50)  # roi_template, case_study, competitive_intel, product_info, pricing, objection_handling, email_template, playbook, other
    description: Optional[str] = Field(default=None, max_length=500)
    content: str = Field(sa_column=Column(Text, nullable=False))  # Extracted text

    # File metadata
    filename: Optional[str] = Field(default=None, max_length=255)
    file_size: Optional[int] = Field(default=None)

    # Targeting
    tags: List[Any] = Field(default=[], sa_column=Column(JSONB, server_default="[]"))
    modules: List[Any] = Field(
        default=[],
        sa_column=Column(JSONB, server_default="[]"),
    )  # ["pre_meeting", "outreach", "demo_strategy", "account_sourcing", "custom_demo", "prospecting"]

    # RAG: list of {"text": str, "embedding": list[float]} per chunk.
    chunks: List[Any] = Field(
        default=[],
        sa_column=Column(JSONB, server_default="[]"),
    )

    is_active: bool = Field(default=True)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class SalesResourceCreate(BaseModel):
    title: str
    category: str
    description: Optional[str] = None
    content: str
    tags: List[str] = []
    modules: List[str] = []


class SalesResourceRead(BaseModel):
    id: UUID
    title: str
    category: str
    description: Optional[str]
    content: str
    filename: Optional[str]
    file_size: Optional[int]
    tags: List[str]
    modules: List[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SalesResourceUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    modules: Optional[List[str]] = None
    is_active: Optional[bool] = None
