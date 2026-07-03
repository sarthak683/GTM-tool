from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class UserAlias(SQLModel, table=True):
    """
    Maps an alternate Google account (google_id + email) to a primary CRM user.

    Used when a team member has multiple Google accounts — e.g. sipra@beacon.li
    and sipra@beaconli.com. Both google_ids resolve to the same User row so the
    rep sees one profile regardless of which account they sign in with.
    """
    __tablename__ = "user_aliases"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    google_id: str = Field(index=True, unique=True)
    email: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
