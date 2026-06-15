"""
Authentication service — JWT tokens + Google OAuth2 token exchange.

Flow:
1. Frontend redirects user to GET /api/v1/auth/google/login
2. Backend builds Google OAuth URL → redirects user to Google
3. Google redirects back to GET /api/v1/auth/google/callback?code=...
4. Backend exchanges code for access_token via Google's token endpoint
5. Backend calls Google userinfo endpoint → gets email, name, picture
6. Backend creates/finds User in DB
7. Backend issues JWT → redirects to frontend with ?token=...
"""
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

import httpx
import jwt

from app.config import settings

# ── JWT ──────────────────────────────────────────────────────────────────────

ALGORITHM = "HS256"


def create_access_token(user_id: UUID, role: str, expires_minutes: int = 0) -> str:
    """Create a signed JWT for the given user."""
    ttl = expires_minutes or settings.JWT_EXPIRE_MINUTES
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": datetime.utcnow() + timedelta(minutes=ttl),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT. Returns payload dict or None if invalid/expired."""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ── Superadmin + impersonation ────────────────────────────────────────────────

# Distinct from the `admin` role (which several people hold). Only these two can
# impersonate a specific teammate to see the CRM from their exact perspective.
# Kept in sync with SUPERADMIN_EMAILS in frontend/src/lib/AuthContext.tsx.
SUPERADMIN_EMAILS = {"sarthak@beacon.li", "rakesh@beacon.li"}


def is_superadmin(user) -> bool:
    return bool(user and (getattr(user, "email", "") or "").strip().lower() in SUPERADMIN_EMAILS)


def create_impersonation_token(
    target_user_id: UUID, role: str, impersonator_id: UUID, expires_minutes: int = 0
) -> str:
    """JWT that acts AS `target_user_id` while recording who is behind it.

    The `imp_by` claim is the marker the read-only guard (get_current_user) keys
    off to block writes — so a superadmin can *see* a teammate's data but cannot
    act as them. Identity resolution is otherwise identical to a normal token, so
    every endpoint scopes to the target user with no other changes.
    """
    ttl = expires_minutes or settings.JWT_EXPIRE_MINUTES
    payload = {
        "sub": str(target_user_id),
        "role": role,
        "imp_by": str(impersonator_id),
        "exp": datetime.utcnow() + timedelta(minutes=ttl),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


# ── Google OAuth2 ────────────────────────────────────────────────────────────

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def build_google_login_url(redirect_uri: str) -> str:
    """Build the Google OAuth2 authorization URL."""
    from urllib.parse import urlencode

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        # Force the standard consent flow in dev/local environments.
        "prompt": "consent",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_google_code(code: str, redirect_uri: str) -> dict:
    """
    Exchange an authorization code for Google user info.
    Returns dict with: email, name, picture, google_id.
    """
    async with httpx.AsyncClient() as client:
        # Step 1: exchange the one-time callback code for a Google access token.
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()
        access_token = tokens["access_token"]

        # Step 2: use that access token to fetch the user's profile details.
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo_resp.raise_for_status()
        info = userinfo_resp.json()

    return {
        "google_id": info["id"],
        "email": info["email"],
        "name": info.get("name", info["email"].split("@")[0]),
        "avatar_url": info.get("picture"),
    }
