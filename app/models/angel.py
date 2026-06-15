from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


# ── Angel Investor (the connector) ──────────────────────────────────────────


class AngelInvestorBase(SQLModel):
    name: str
    current_role: Optional[str] = None
    current_company: Optional[str] = None
    linkedin_url: Optional[str] = None


class AngelInvestor(AngelInvestorBase, table=True):
    __tablename__ = "angel_investors"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    career_history: Optional[str] = Field(default=None, sa_column=Column(Text))
    networks: Optional[str] = Field(default=None, sa_column=Column(Text))
    pe_vc_connections: Optional[str] = Field(default=None, sa_column=Column(Text))
    sectors: Optional[str] = Field(default=None, sa_column=Column(Text))
    notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AngelInvestorCreate(AngelInvestorBase):
    career_history: Optional[str] = None
    networks: Optional[str] = None
    pe_vc_connections: Optional[str] = None
    sectors: Optional[str] = None
    notes: Optional[str] = None


class AngelInvestorRead(AngelInvestorBase):
    id: UUID
    career_history: Optional[str] = None
    networks: Optional[str] = None
    pe_vc_connections: Optional[str] = None
    sectors: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AngelInvestorUpdate(SQLModel):
    name: Optional[str] = None
    current_role: Optional[str] = None
    current_company: Optional[str] = None
    linkedin_url: Optional[str] = None
    career_history: Optional[str] = None
    networks: Optional[str] = None
    pe_vc_connections: Optional[str] = None
    sectors: Optional[str] = None
    notes: Optional[str] = None


# ── Angel Mapping (prospect ↔ angel connection) ────────────────────────────


class AngelMappingBase(SQLModel):
    strength: int = Field(ge=1, le=5, description="Connection strength 1-5")
    rank: int = Field(ge=1, le=10, description="Priority rank (1 = top)")


class AngelMapping(AngelMappingBase, table=True):
    __tablename__ = "angel_mappings"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    contact_id: UUID = Field(foreign_key="contacts.id", index=True)
    company_id: Optional[UUID] = Field(default=None, foreign_key="companies.id", index=True)
    angel_investor_id: UUID = Field(foreign_key="angel_investors.id", index=True)
    connection_path: Optional[str] = Field(default=None, sa_column=Column(Text))
    why_it_works: Optional[str] = Field(default=None, sa_column=Column(Text))
    recommended_strategy: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AngelMappingCreate(AngelMappingBase):
    contact_id: UUID
    company_id: Optional[UUID] = None
    angel_investor_id: UUID
    connection_path: Optional[str] = None
    why_it_works: Optional[str] = None
    recommended_strategy: Optional[str] = None


class AngelMappingRead(AngelMappingBase):
    id: UUID
    contact_id: UUID
    company_id: Optional[UUID] = None
    angel_investor_id: UUID
    # Populated via JOINs
    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    contact_linkedin: Optional[str] = None
    company_name: Optional[str] = None
    angel_name: Optional[str] = None
    angel_current_role: Optional[str] = None
    angel_current_company: Optional[str] = None
    connection_path: Optional[str] = None
    why_it_works: Optional[str] = None
    recommended_strategy: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AngelMappingUpdate(SQLModel):
    strength: Optional[int] = Field(default=None, ge=1, le=5)
    rank: Optional[int] = Field(default=None, ge=1, le=10)
    connection_path: Optional[str] = None
    why_it_works: Optional[str] = None
    recommended_strategy: Optional[str] = None
