"""
Centralised FastAPI dependency injectors.

All route files should import from here — not from app.database directly.
This single import point makes it trivial to swap the DB backend, add auth,
rate-limiting, or request-scoped tracing in one place later.
"""
from typing import Annotated, Optional

from fastapi import Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

# Re-export the session dependency so routes only need this module
from app.database import get_session
from app.models.user import User
from app.services.auth import decode_access_token
from app.core.exceptions import UnauthorizedError, ForbiddenError

# ── Typed shorthand ──────────────────────────────────────────────────────────
# Use as: async def my_route(session: DBSession): ...
DBSession = Annotated[AsyncSession, Depends(get_session)]


# ── Pagination params ────────────────────────────────────────────────────────
class PaginationParams:
    def __init__(
        self,
        skip: int = Query(default=0, ge=0, description="Records to skip"),
        limit: int = Query(default=50, ge=1, le=2000, description="Max records to return"),
    ):
        # Centralizing pagination defaults here keeps list endpoints consistent.
        self.skip = skip
        self.limit = limit


Pagination = Annotated[PaginationParams, Depends(PaginationParams)]


# ── Auth dependencies ────────────────────────────────────────────────────────


async def get_current_user(
    request: Request,
    session: DBSession,
    authorization: Optional[str] = Header(default=None),
) -> User:
    """
    Extract and validate JWT from the Authorization header.
    Returns the authenticated User or raises UnauthorizedError.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("Missing or invalid authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_access_token(token)
    if payload is None:
        raise UnauthorizedError("Invalid or expired token")

    # Impersonation is read-only: a superadmin viewing as another user may read
    # their data but never write/act as them. The `imp_by` claim marks such a
    # token; block any mutating method. (Safe reads are GET/HEAD/OPTIONS.)
    if payload.get("imp_by") and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise ForbiddenError(
            "Read-only: you're viewing as another user. Switch back to your own account to make changes."
        )

    # We still load the user from the database so auth reflects deactivation or
    # role changes that happened after the JWT was issued.
    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("Invalid token payload")

    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        raise UnauthorizedError("User not found or deactivated")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    """Dependency that ensures the current user has admin role."""
    if user.role != "admin":
        raise ForbiddenError("Admin access required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


__all__ = [
    "get_session", "DBSession", "PaginationParams", "Pagination",
    "get_current_user", "CurrentUser", "require_admin", "AdminUser",
]
