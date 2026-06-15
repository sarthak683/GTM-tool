"""
Google Calendar client — fetches upcoming events for a connected personal account.

Uses the same OAuth2 token stored in UserEmailConnection.token_data.
Requires calendar.readonly scope (added to the personal Gmail OAuth flow).

Key concepts:
  - We only fetch events from 'now' forward (timeMin) up to 60 days out
  - Each event is deduped by Google event ID (stored as external_source_id on Meeting)
  - Attendees are matched to CRM contacts by email address
  - Declined events and all-day events without confirmed bookings are skipped
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.services.gmail_oauth import GOOGLE_TOKEN_URL

logger = logging.getLogger(__name__)

CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"


@dataclass
class CalendarEvent:
    """Parsed Google Calendar event."""
    event_id: str                         # Google event ID (stable, use for dedup)
    title: str                            # event summary / title
    start_dt: Optional[datetime]          # None for all-day events we skip
    end_dt: Optional[datetime]
    attendee_emails: list[str] = field(default_factory=list)
    organizer_email: str = ""
    meeting_link: Optional[str] = None   # Google Meet / Zoom link if present
    status: str = "confirmed"            # confirmed | tentative | cancelled
    html_link: str = ""                  # link to the event in Google Calendar


async def _refresh_token_if_needed(token_data: dict, client_id: str, client_secret: str, force: bool = False) -> dict:
    """Refresh the access token if expired. Returns updated token_data.

    When `force=True`, refreshes regardless of expiry — used when the stored
    access_token has narrower scopes than the refresh_token can grant (e.g.
    user reconnected with calendar scope, but their cached access_token from
    a prior gmail-only consent is still valid by expiry). Forcing the refresh
    rotates the access_token to one that reflects the current refresh_token's
    scope grants.
    """
    if not force:
        expiry_str = token_data.get("expiry")
        if expiry_str:
            try:
                expiry = datetime.fromisoformat(expiry_str)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                # Refresh if less than 5 minutes remaining
                if expiry > datetime.now(timezone.utc) + timedelta(minutes=5):
                    return token_data
            except Exception:
                pass

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise ValueError("No refresh_token available — user must reconnect")

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        new_tokens = resp.json()

    updated = dict(token_data)
    updated["token"] = new_tokens["access_token"]
    updated["expiry"] = (
        datetime.now(timezone.utc) + timedelta(seconds=int(new_tokens.get("expires_in", 3600)))
    ).isoformat()
    # Persist the actual granted scopes from Google's refresh response so the
    # cached scopes metadata reflects reality, not the original consent grant.
    if new_tokens.get("scope"):
        updated["scopes"] = new_tokens["scope"].split(" ")
    return updated


def _parse_event(raw: dict) -> Optional[CalendarEvent]:
    """Parse a raw Google Calendar event dict into a CalendarEvent."""
    event_id = raw.get("id", "")
    if not event_id:
        return None

    # Skip cancelled events
    if raw.get("status") == "cancelled":
        return None

    title = (raw.get("summary") or "").strip() or "Untitled meeting"

    # Parse start time — skip all-day events (they have 'date' not 'dateTime')
    start_block = raw.get("start") or {}
    end_block = raw.get("end") or {}
    start_str = start_block.get("dateTime")
    end_str = end_block.get("dateTime")
    if not start_str:
        return None  # all-day event — skip

    try:
        start_dt = datetime.fromisoformat(start_str)
        end_dt = datetime.fromisoformat(end_str) if end_str else None
    except Exception:
        return None

    # Check if the current user declined
    self_entry = next(
        (a for a in raw.get("attendees", []) if a.get("self")), None
    )
    if self_entry and self_entry.get("responseStatus") == "declined":
        return None

    # Collect attendee emails (external only — skip self)
    attendee_emails = [
        a["email"].lower().strip()
        for a in raw.get("attendees", [])
        if a.get("email") and not a.get("self") and a.get("responseStatus") != "declined"
    ]

    organizer = (raw.get("organizer") or {}).get("email", "").lower().strip()

    # Extract meeting link (Google Meet or first entry point)
    meeting_link: Optional[str] = None
    conf = raw.get("conferenceData") or {}
    for entry in conf.get("entryPoints", []):
        if entry.get("entryPointType") in ("video", "more"):
            meeting_link = entry.get("uri")
            break

    return CalendarEvent(
        event_id=event_id,
        title=title,
        start_dt=start_dt,
        end_dt=end_dt,
        attendee_emails=attendee_emails,
        organizer_email=organizer,
        meeting_link=meeting_link,
        status=raw.get("status", "confirmed"),
        html_link=raw.get("htmlLink", ""),
    )


async def fetch_upcoming_events(
    token_data: dict,
    client_id: str,
    client_secret: str,
    days_ahead: int = 60,
    max_results: int = 100,
) -> tuple[list[CalendarEvent], dict]:
    """
    Fetch upcoming calendar events for the connected account.

    Returns (events, updated_token_data). The updated token_data should be
    saved back to the UserEmailConnection if the token was refreshed.

    Only returns events with at least one external attendee (i.e. customer meetings,
    not internal-only calls).
    """
    # Do not rely exclusively on the stored scopes metadata. Some connections
    # may have stale or incomplete scope lists even when the live token can
    # access Calendar. We still attempt the API call and only stop on a real
    # Google 403.
    granted_scopes = token_data.get("scopes", [])
    if isinstance(granted_scopes, list):
        has_calendar = any(CALENDAR_SCOPE in s for s in granted_scopes)
    else:
        has_calendar = CALENDAR_SCOPE in str(granted_scopes)
    # When the stored access_token's recorded scopes don't include calendar,
    # force a refresh — the refresh_token may have broader scopes that the
    # access_token doesn't reflect (happens when consent was upgraded after
    # the initial gmail-only connect, or when the cached metadata is stale).
    if not has_calendar:
        logger.info(
            "google_calendar: calendar scope missing from stored metadata; forcing token refresh"
        )

    updated_token = await _refresh_token_if_needed(
        token_data, client_id, client_secret, force=not has_calendar
    )
    access_token = updated_token["token"]

    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()

    events: list[CalendarEvent] = []
    page_token: Optional[str] = None

    async with httpx.AsyncClient(timeout=20) as http:
        while True:
            params: dict[str, Any] = {
                "timeMin": time_min,
                "timeMax": time_max,
                "maxResults": min(max_results, 250),
                "singleEvents": "true",
                "orderBy": "startTime",
            }
            if page_token:
                params["pageToken"] = page_token

            resp = await http.get(
                CALENDAR_EVENTS_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )

            if resp.status_code == 403:
                logger.warning("google_calendar: 403 — calendar scope likely not granted")
                return [], updated_token

            resp.raise_for_status()
            data = resp.json()

            for raw in data.get("items", []):
                parsed = _parse_event(raw)
                if parsed and parsed.attendee_emails:
                    events.append(parsed)

            page_token = data.get("nextPageToken")
            if not page_token or len(events) >= max_results:
                break

    logger.info("google_calendar: fetched %d events with external attendees", len(events))
    return events, updated_token
