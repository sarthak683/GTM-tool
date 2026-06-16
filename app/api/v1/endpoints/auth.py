"""
Authentication endpoints — Google OAuth2 login, token exchange, user management.

The first user to sign in is automatically granted the 'admin' role.
Subsequent users default to 'sdr'.
"""
import logging
from typing import List, Optional
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, func, select

from app.config import settings
from app.core.dependencies import AdminUser, CurrentUser, DBSession
from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.models.user import User, UserRead, UserUpdate
from app.services.auth import (
    build_google_login_url,
    create_access_token,
    create_impersonation_token,
    exchange_google_code,
    is_superadmin,
)
from app.services.permissions import require_workspace_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
ALLOWED_USER_ROLES = {"admin", "ae", "sdr"}

# Emails that always get admin role regardless of signup order.
SUPERADMIN_EMAILS = {"sarthak@beacon.li", "rakesh@beacon.li", "maithili@beacon.li"}


# ── Google OAuth flow ────────────────────────────────────────────────────────


@router.get("/google/login")
async def google_login():
    """Redirect the user to Google's OAuth consent screen."""
    # Fail fast if the backend is missing the credentials required to start OAuth.
    if not settings.GOOGLE_CLIENT_ID:
        raise UnauthorizedError(
            "Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
        )
    url = build_google_login_url(settings.GOOGLE_REDIRECT_URI)
    return RedirectResponse(url)


@router.get("/google/callback")
async def google_callback(
    session: DBSession,
    code: str = Query(..., description="Authorization code from Google"),
):
    """
    Handle the OAuth callback from Google.
    Creates the user if first login, then issues a JWT and redirects to the frontend.
    """
    # Google sends back a short-lived authorization code; we exchange it for the
    # user's identity details that our app stores locally.
    try:
        google_info = await exchange_google_code(code, settings.GOOGLE_REDIRECT_URI)
    except Exception:
        # Log the underlying error server-side, but never echo provider/internal
        # detail back to the browser.
        logger.exception("Google OAuth exchange failed")
        raise UnauthorizedError("Failed to authenticate with Google. Please try again.")

    email = google_info["email"]

    # Domain allowlist (opt-in). When ALLOWED_EMAIL_DOMAINS is empty the list is
    # empty and we allow any Google account — preserving the current default so
    # nobody is locked out. When set (e.g. "beacon.li"), only emails on those
    # domains may sign in; everyone else is bounced to the login screen with a
    # machine-readable reason (no sensitive detail leaked).
    allowed_domains = settings.allowed_email_domains
    if allowed_domains:
        email_domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
        if email_domain not in allowed_domains:
            logger.warning(
                "Rejected sign-in for %s: domain %r not in allowlist",
                email,
                email_domain,
            )
            error_url = f"{settings.FRONTEND_URL}/login?{urlencode({'error': 'domain_not_allowed'})}"
            return RedirectResponse(error_url)

    # We treat Google as the source of truth for identity, so the lookup is keyed
    # by Google's stable user id rather than by email.
    user = (
        await session.execute(
            select(User).where(User.google_id == google_info["google_id"])
        )
    ).scalar_one_or_none()

    if user is None:
        # Check if a placeholder account already exists for this email (e.g. seeded
        # from ClickUp import with google_id='clickup_import_...' or 'seed_...').
        # If so, claim it by updating the google_id rather than creating a duplicate
        # which would violate the unique email constraint.
        existing_by_email = (
            await session.execute(
                select(User).where(User.email == google_info["email"])
            )
        ).scalar_one_or_none()

        if existing_by_email is not None:
            # Upgrade the placeholder: bind the real Google ID and refresh profile.
            existing_by_email.google_id = google_info["google_id"]
            existing_by_email.name = google_info["name"]
            existing_by_email.avatar_url = google_info.get("avatar_url")
            # Always enforce superadmin role for designated emails.
            if google_info["email"] in SUPERADMIN_EMAILS:
                existing_by_email.role = "admin"
            session.add(existing_by_email)
            await session.commit()
            await session.refresh(existing_by_email)
            user = existing_by_email
        else:
            # Genuinely new user — superadmins always get admin; emails on the
            # ADMIN_BOOTSTRAP_EMAILS allowlist get admin; otherwise the legacy
            # "first user to sign in becomes admin" fallback applies; everyone
            # else defaults to sdr. With ADMIN_BOOTSTRAP_EMAILS unset the
            # bootstrap set is empty, so behaviour is unchanged.
            user_count = (await session.execute(select(func.count(User.id)))).scalar_one()
            is_bootstrap_admin = email.lower() in settings.admin_bootstrap_emails
            role = (
                "admin"
                if google_info["email"] in SUPERADMIN_EMAILS
                or is_bootstrap_admin
                or user_count == 0
                else "sdr"
            )

            user = User(
                email=google_info["email"],
                name=google_info["name"],
                avatar_url=google_info.get("avatar_url"),
                google_id=google_info["google_id"],
                role=role,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
    else:
        # Keep profile fields in sync with Google so name/avatar changes show up
        # without requiring a separate profile-edit flow in this app.
        user.name = google_info["name"]
        user.avatar_url = google_info.get("avatar_url")
        # Always enforce superadmin role for designated emails.
        if google_info["email"] in SUPERADMIN_EMAILS:
            user.role = "admin"
        session.add(user)
        await session.commit()
        await session.refresh(user)

    # Deactivated users may still exist in the database, but they should not be
    # able to complete sign-in and receive a new application token.
    if not user.is_active:
        raise ForbiddenError("Your account has been deactivated")

    # After Google auth succeeds, this app issues its own JWT and hands it to the
    # frontend via a redirect so subsequent API calls use our auth scheme.
    token = create_access_token(user.id, user.role)
    frontend_url = f"{settings.FRONTEND_URL}/auth/callback?{urlencode({'token': token})}"
    return RedirectResponse(frontend_url)


# ── Current user ─────────────────────────────────────────────────────────────


@router.get("/me", response_model=UserRead)
async def get_me(user: CurrentUser):
    """Return the currently authenticated user's profile."""
    return user


class ImpersonateRequest(SQLModel):
    user_id: UUID


class ImpersonateResponse(SQLModel):
    token: str
    user: UserRead


@router.post("/impersonate", response_model=ImpersonateResponse)
async def impersonate_user(payload: ImpersonateRequest, session: DBSession, current_user: CurrentUser):
    """Superadmin-only: mint a READ-ONLY token that views the CRM as another
    teammate, so a superadmin can see the app from that person's exact
    perspective (their pipeline, their scoped meetings, their tasks). Writes are
    blocked while the token is active (enforced in get_current_user). The token
    records `imp_by` for audit.

    Note: this requires the caller's own (non-impersonation) token — the
    read-only guard blocks calling it while already impersonating, which also
    prevents impersonation chaining.
    """
    if not is_superadmin(current_user):
        raise ForbiddenError("Only superadmins can view as another user.")
    target = (
        await session.execute(select(User).where(User.id == payload.user_id))
    ).scalar_one_or_none()
    if not target or not target.is_active:
        raise NotFoundError("User not found or inactive.")
    if target.id == current_user.id:
        raise ForbiddenError("You are already signed in as this user.")
    token = create_impersonation_token(target.id, target.role, current_user.id)
    logging.getLogger(__name__).info(
        "impersonation: %s now viewing as %s (%s)", current_user.email, target.email, target.id
    )
    return ImpersonateResponse(token=token, user=target)


# ── User management (admin) ─────────────────────────────────────────────────


@router.get("/users", response_model=List[UserRead])
async def list_users(session: DBSession, current_user: CurrentUser):
    """List all users for teammates allowed to manage team permissions."""
    await require_workspace_permission(session, current_user, "manage_team")
    result = await session.execute(select(User).order_by(User.name))
    return result.scalars().all()


@router.get("/users/all", response_model=List[UserRead])
async def list_all_users(session: DBSession, _user: CurrentUser):
    """List all active users. Available to any authenticated user (for assignment dropdowns)."""
    result = await session.execute(
        select(User).where(User.is_active == True).order_by(User.name)  # noqa: E712
    )
    return result.scalars().all()


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID,
    data: UserUpdate,
    session: DBSession,
    current_user: CurrentUser,
):
    """Update a user's role or active status."""
    await require_workspace_permission(session, current_user, "manage_team")
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")

    if data.role and data.role not in ALLOWED_USER_ROLES:
        raise ForbiddenError("Role must be one of: admin, ae, sdr")

    # Keep role changes focused on teammate administration, not self-service,
    # so nobody can accidentally lock themselves out from this screen.
    if user.id == current_user.id and data.role and data.role != user.role:
        raise ForbiddenError("Cannot change your own role from this screen")

    admin_count = (
        await session.execute(
            select(func.count(User.id)).where(User.role == "admin", User.is_active == True)  # noqa: E712
        )
    ).scalar_one()
    demoting_last_admin = user.role == "admin" and data.role and data.role != "admin" and admin_count <= 1
    deactivating_last_admin = user.role == "admin" and data.is_active is False and admin_count <= 1
    if demoting_last_admin or deactivating_last_admin:
        raise ForbiddenError("At least one active admin must remain on the workspace")

    # Only apply fields that were explicitly sent in the PATCH payload.
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)

    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.delete("/users/{user_id}", response_model=dict)
async def delete_user(
    user_id: UUID,
    session: DBSession,
    current_user: CurrentUser,
):
    """Delete a user account from the workspace (admin/team-managers only)."""
    await require_workspace_permission(session, current_user, "manage_team")
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")

    if user.id == current_user.id:
        raise ForbiddenError("Cannot delete your own account from this screen")

    admin_count = (
        await session.execute(
            select(func.count(User.id)).where(User.role == "admin", User.is_active == True)  # noqa: E712
        )
    ).scalar_one()
    deleting_last_admin = user.role == "admin" and user.is_active and admin_count <= 1
    if deleting_last_admin:
        raise ForbiddenError("At least one active admin must remain on the workspace")

    try:
        await session.delete(user)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ForbiddenError("Cannot delete this user because related records still reference this account. Deactivate instead.")

    return {"status": "deleted", "user_id": str(user_id)}


class SeedUserPayload(SQLModel):
    email: str
    name: str
    role: str = "ae"


class SeedUsersRequest(SQLModel):
    users: list[SeedUserPayload]


class SeedUsersResponse(SQLModel):
    created: int
    skipped: int
    users: list[UserRead]


@router.post("/users/seed", response_model=SeedUsersResponse)
async def seed_users(payload: SeedUsersRequest, session: DBSession, _admin: AdminUser):
    """Bulk-create team members so they can be matched during imports (ClickUp, CSV, etc.)."""
    created = 0
    skipped = 0
    all_users: list[User] = []

    for entry in payload.users:
        email = entry.email.strip().lower()
        existing = (
            await session.execute(select(User).where(func.lower(User.email) == email).limit(1))
        ).scalar_one_or_none()
        if existing:
            skipped += 1
            all_users.append(existing)
            continue

        if entry.role not in ALLOWED_USER_ROLES:
            continue

        user = User(
            email=email,
            name=entry.name.strip(),
            google_id=f"seed_{email}",
            role=entry.role,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        created += 1
        all_users.append(user)

    await session.commit()
    for u in all_users:
        await session.refresh(u)

    return SeedUsersResponse(created=created, skipped=skipped, users=all_users)
