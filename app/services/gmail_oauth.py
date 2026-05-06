from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
import jwt

from app.config import settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GOOGLE_EMAIL_SCOPE = "openid email"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
# Combined scope requested during OAuth — both mail and calendar in one consent
PERSONAL_OAUTH_SCOPES = f"{GMAIL_SCOPE} {CALENDAR_SCOPE} {DRIVE_SCOPE} {DRIVE_FILE_SCOPE}"
STATE_ALGORITHM = "HS256"


def build_gmail_connect_url(state: str, include_calendar: bool = True, scopes: str | None = None) -> str:
    from urllib.parse import urlencode

    scope = scopes or (PERSONAL_OAUTH_SCOPES if include_calendar else GMAIL_SCOPE)
    params = {
        "client_id": settings.gmail_client_id,
        "redirect_uri": settings.GMAIL_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def create_gmail_oauth_state(user_id: str, *, flow: str = "shared") -> str:
    payload = {
        "sub": user_id,
        "scope": "gmail_connect",
        "flow": flow,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(minutes=15),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=STATE_ALGORITHM)


def decode_gmail_oauth_state(token: str) -> Optional[dict[str, Any]]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[STATE_ALGORITHM])
        if payload.get("scope") != "gmail_connect":
            return None
        return payload
    except jwt.PyJWTError:
        return None


async def exchange_gmail_code(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.gmail_client_id,
                "client_secret": settings.gmail_client_secret,
                "redirect_uri": settings.GMAIL_OAUTH_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        email_address = None
        auth_headers = {"Authorization": f"Bearer {token_data['access_token']}"}

        profile_resp = await client.get(GMAIL_PROFILE_URL, headers=auth_headers)
        if profile_resp.status_code < 400:
            email_address = profile_resp.json().get("emailAddress")

        if not email_address:
            userinfo_resp = await client.get(GOOGLE_USERINFO_URL, headers=auth_headers)
            if userinfo_resp.status_code < 400:
                email_address = userinfo_resp.json().get("email")

        if not email_address and token_data.get("id_token"):
            id_claims = jwt.decode(token_data["id_token"], options={"verify_signature": False})
            email_address = id_claims.get("email")

        if not email_address:
            profile_resp.raise_for_status()
            raise ValueError("Google OAuth response did not include an email address")

    return {
        "email_address": email_address,
        "token_data": {
            "token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "scopes": token_data.get("scope", GMAIL_SCOPE).split(" "),
            "expiry": (datetime.utcnow() + timedelta(seconds=int(token_data.get("expires_in", 3600)))).isoformat(),
        },
    }
