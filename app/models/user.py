from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(index=True, unique=True)
    name: str
    avatar_url: Optional[str] = None
    google_id: str = Field(index=True, unique=True)
    role: str = Field(default="sdr")  # superadmin | admin | ae | sdr | marketing
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_admin(self) -> bool:
        """Whether this account has workspace-administrator capabilities."""
        return self.role in {"admin", "superadmin"}


class UserRead(SQLModel):
    id: UUID
    email: str
    name: str
    avatar_url: Optional[str] = None
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserUpdate(SQLModel):
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
