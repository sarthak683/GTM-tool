from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.gmail_sender import send_gmail_email
from app.clients.resend_client import send_email
from app.config import settings
from app.database import AsyncSessionLocal
from app.models.activity import Activity
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.meeting import Meeting
from app.models.settings import WorkspaceSettings
from app.models.user import User
from app.models.user_email_connection import UserEmailConnection
from app.services.gmail_oauth import GMAIL_SEND_SCOPE
from app.services.pre_meeting_intelligence import generate_meeting_demo_strategy, run_pre_meeting_intelligence


async def _personal_send_token_for_user(
    session: AsyncSession, user_id: UUID
) -> tuple[str, dict] | None:
    """Return (email_address, token_data) for a rep whose connected Gmail can send.

    Used by the pre-meeting brief sender so the brief lands in the rep's own
    inbox from their own address — feels native and shows up in their
    threading, instead of a workspace-wide service account.
    """
    conn = (
        await session.execute(
            select(UserEmailConnection).where(
                UserEmailConnection.user_id == user_id,
                UserEmailConnection.is_active == True,  # noqa: E712
            )
        )
    ).scalars().first()
    if not conn or not isinstance(conn.token_data, dict):
        return None
    scopes = conn.token_data.get("scopes") or []
    scope_strs = [str(s) for s in (scopes if isinstance(scopes, list) else [scopes])]
    if not any(GMAIL_SEND_SCOPE in s for s in scope_strs):
        return None
    return conn.email_address, conn.token_data


logger = logging.getLogger(__name__)

DEFAULT_PRE_MEETING_AUTOMATION_SETTINGS = {
    "enabled": True,
    "send_hours_before": 12,
    "generate_hours_before": 48,
    "auto_generate_if_missing": True,
}


def normalize_pre_meeting_settings(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    send_hours_before = int(raw.get("send_hours_before", DEFAULT_PRE_MEETING_AUTOMATION_SETTINGS["send_hours_before"]))
    generate_hours_before = int(raw.get("generate_hours_before", DEFAULT_PRE_MEETING_AUTOMATION_SETTINGS["generate_hours_before"]))
    send_hours_before = max(1, min(send_hours_before, 168))
    generate_hours_before = max(send_hours_before, min(generate_hours_before, 168))
    return {
        "enabled": bool(raw.get("enabled", DEFAULT_PRE_MEETING_AUTOMATION_SETTINGS["enabled"])),
        "send_hours_before": send_hours_before,
        "generate_hours_before": generate_hours_before,
        "auto_generate_if_missing": bool(
            raw.get(
                "auto_generate_if_missing",
                DEFAULT_PRE_MEETING_AUTOMATION_SETTINGS["auto_generate_if_missing"],
            )
        ),
    }


async def _get_or_create_settings(session: AsyncSession) -> WorkspaceSettings:
    row = await session.get(WorkspaceSettings, 1)
    if row is None:
        row = WorkspaceSettings(id=1)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def _collect_recipient_ids(session: AsyncSession, meeting: Meeting) -> list[UUID]:
    """
    Send pre-meeting intel to the most accountable rep we can identify.
    Prefer deal owner, then company owner, then meeting owner/sync owner.
    """
    recipient_ids: list[UUID] = []
    seen: set[UUID] = set()

    def push(value: UUID | None) -> None:
        if value and value not in seen:
            seen.add(value)
            recipient_ids.append(value)

    deal = await session.get(Deal, meeting.deal_id) if meeting.deal_id else None

    if deal and deal.assigned_to_id:
        # Primary: the AE/rep assigned to this specific deal
        push(deal.assigned_to_id)
    elif meeting.company_id:
        # Fallback: company assigned rep (only if no deal assigned)
        company = await session.get(Company, meeting.company_id)
        if company:
            push(company.assigned_to_id)

    push(meeting.owner_user_id)
    push(meeting.synced_by_user_id)

    return recipient_ids


def _build_meeting_intel_email(meeting: Meeting) -> tuple[str, str]:
    """
    Build a focused pre-meeting intel email.
    Sends the full executive briefing as the body so the rep has everything
    they need without having to open the CRM.
    """
    research = meeting.research_data if isinstance(meeting.research_data, dict) else {}
    executive_briefing = str(research.get("executive_briefing") or meeting.pre_brief or "").strip()
    recommendations = research.get("meeting_recommendations") if isinstance(research.get("meeting_recommendations"), list) else []
    why_now_signals = research.get("why_now_signals") if isinstance(research.get("why_now_signals"), list) else []
    crm_signals = research.get("crm_signals") if isinstance(research.get("crm_signals"), dict) else {}
    attendee_cards = (
        ((research.get("attendee_intelligence") or {}).get("stakeholder_cards"))
        if isinstance(research.get("attendee_intelligence"), dict)
        else []
    ) or []
    company_profile = research.get("company_profile") or {}

    frontend_base = (settings.FRONTEND_URL or "http://localhost:8080").rstrip("/")
    meeting_link = f"{frontend_base}/meetings/{meeting.id}"
    scheduled_label = meeting.scheduled_at.strftime("%b %d, %Y at %I:%M %p UTC") if meeting.scheduled_at else "TBD"
    meeting_number = crm_signals.get("meeting_number", 1)

    subject = f"Meeting #{meeting_number} prep: {meeting.title} — {scheduled_label}"

    # ── Plain text body ───────────────────────────────────────────────────────
    lines = [
        f"PRE-MEETING INTEL — {meeting.title}",
        f"Scheduled: {scheduled_label} | Meeting #{meeting_number}",
        f"Type: {meeting.meeting_type.upper() if meeting.meeting_type else 'Meeting'}",
        "",
    ]

    if company_profile:
        lines += [
            "ACCOUNT",
            f"  {company_profile.get('name', '')} | {company_profile.get('domain', '')}",
            f"  {company_profile.get('industry', '')} | {company_profile.get('employee_count', '?')} employees | {company_profile.get('funding_stage', '')}",
            f"  ICP: {company_profile.get('icp_tier', '?')} (score {company_profile.get('icp_score', '?')})",
            "",
        ]

    if executive_briefing:
        lines += [
            "─" * 60,
            "FULL INTEL BRIEF",
            "─" * 60,
            executive_briefing,
            "",
        ]
    else:
        # Fallback sections when briefing wasn't generated
        if why_now_signals:
            lines += ["WHY NOW"]
            for item in why_now_signals[:3]:
                detail = str((item or {}).get("detail") or "").strip()
                if detail:
                    lines.append(f"  • {detail}")
            lines.append("")

        if attendee_cards:
            lines += ["KEY PEOPLE IN THIS MEETING"]
            for card in attendee_cards[:4]:
                name = str(card.get("name") or "Stakeholder").strip()
                title = str(card.get("title") or card.get("role_label") or "").strip()
                focus = str(card.get("likely_focus") or "").strip()
                line = f"  • {name}"
                if title:
                    line += f" | {title}"
                if focus:
                    line += f"\n    Focus: {focus}"
                lines.append(line)
            lines.append("")

        if recommendations:
            lines += ["RECOMMENDED APPROACH"]
            for item in recommendations[:4]:
                if isinstance(item, str) and item.strip():
                    lines.append(f"  • {item.strip()}")
            lines.append("")

    lines += [
        "─" * 60,
        f"Full prep page: {meeting_link}",
        "",
        "Beacon generated this from account data, prior meetings, email threads, and call history.",
    ]

    return subject, "\n".join(lines).strip()


async def run_due_pre_meeting_intel_once() -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        settings_row = await _get_or_create_settings(session)
        config = normalize_pre_meeting_settings(settings_row.pre_meeting_automation_settings)
        if not config["enabled"]:
            return {"checked": 0, "generated": 0, "emailed": 0, "skipped": 0}

        now = datetime.utcnow()
        generate_window_end = now + timedelta(hours=config["generate_hours_before"])
        send_window_end = now + timedelta(hours=config["send_hours_before"])
        meetings = (
            await session.execute(
                select(Meeting).where(
                    Meeting.status == "scheduled",
                    Meeting.scheduled_at.is_not(None),
                    Meeting.scheduled_at > now,
                    Meeting.scheduled_at <= generate_window_end,
                )
            )
        ).scalars().all()

        checked = len(meetings)
        generated = 0
        emailed = 0
        skipped = 0

        def _research_is_empty(rd) -> bool:
            """Treat JSONB null, empty dict, empty string, and None all as 'no brief yet'."""
            if rd is None:
                return True
            if isinstance(rd, dict) and not rd:
                return True
            if isinstance(rd, str) and rd.strip().lower() in ("", "null", "{}"):
                return True
            return False

        for meeting in meetings:
            try:
                if config["auto_generate_if_missing"] and _research_is_empty(meeting.research_data):
                    await run_pre_meeting_intelligence(meeting.id, session)
                    generated += 1
                    meeting = await session.get(Meeting, meeting.id)

                if config["auto_generate_if_missing"] and meeting and not meeting.demo_strategy:
                    await generate_meeting_demo_strategy(meeting.id, session)
                    meeting = await session.get(Meeting, meeting.id)

                if not meeting:
                    skipped += 1
                    continue

                if meeting.intel_email_sent_at or not meeting.scheduled_at or meeting.scheduled_at > send_window_end:
                    continue

                recipient_ids = await _collect_recipient_ids(session, meeting)
                if not recipient_ids:
                    skipped += 1
                    continue

                users = (
                    await session.execute(select(User).where(User.id.in_(recipient_ids)))
                ).scalars().all()
                recipients = [user for user in users if user.is_active and user.email]
                if not recipients:
                    skipped += 1
                    continue

                subject, body = _build_meeting_intel_email(meeting)
                gmail_token_data = (
                    settings_row.report_sender_token_data
                    if settings_row.report_sender_email
                    and settings_row.report_sender_connected_email
                    and settings_row.report_sender_token_data
                    and settings_row.report_sender_email.lower() == settings_row.report_sender_connected_email.lower()
                    else None
                )
                sent_any = False
                for recipient in recipients:
                    # Prefer the rep's own connected Gmail when it has send scope.
                    # That way the brief lands as "From: themselves" in their
                    # inbox, which threads naturally and doesn't look like a
                    # service-account blast. Falls back to the workspace report
                    # sender, then to plain SMTP via Resend.
                    personal = await _personal_send_token_for_user(session, recipient.id)
                    if personal is not None:
                        personal_email, personal_token = personal
                        result, personal_token = await send_gmail_email(
                            token_data=personal_token,
                            from_email=personal_email,
                            to=recipient.email,
                            subject=subject,
                            body=body,
                            from_name=recipient.name or "Beacon Meeting Intel",
                        )
                        # Persist any refreshed token back to the rep's connection
                        if result.get("status") == "sent":
                            conn = (
                                await session.execute(
                                    select(UserEmailConnection).where(
                                        UserEmailConnection.user_id == recipient.id
                                    )
                                )
                            ).scalars().first()
                            if conn and personal_token != conn.token_data:
                                conn.token_data = personal_token
                                conn.updated_at = datetime.utcnow()
                                session.add(conn)
                    elif gmail_token_data:
                        result, gmail_token_data = await send_gmail_email(
                            token_data=gmail_token_data,
                            from_email=settings_row.report_sender_email,
                            to=recipient.email,
                            subject=subject,
                            body=body,
                            from_name="Beacon Meeting Intel",
                        )
                    else:
                        result = await send_email(
                            recipient.email,
                            subject,
                            body,
                            from_name="Beacon Meeting Intel",
                        )
                    if result.get("status") == "sent":
                        sent_any = True
                    elif gmail_token_data:
                        settings_row.report_sender_last_error = str(
                            result.get("error") or "Gmail send failed"
                        )[:500]

                if gmail_token_data and gmail_token_data != settings_row.report_sender_token_data:
                    settings_row.report_sender_token_data = gmail_token_data

                if not sent_any:
                    skipped += 1
                    continue

                if gmail_token_data:
                    settings_row.report_sender_last_error = None
                meeting.intel_email_sent_at = datetime.utcnow()
                meeting.updated_at = datetime.utcnow()
                session.add(meeting)
                if meeting.deal_id:
                    session.add(
                        Activity(
                            deal_id=meeting.deal_id,
                            type="note",
                            source="pre_meeting_automation",
                            content=f"Pre-meeting intel email sent for '{meeting.title}'",
                        )
                    )
                emailed += 1
            except Exception:
                logger.exception("Pre-meeting automation failed for meeting %s", meeting.id)
                skipped += 1

        await session.commit()
        return {
            "checked": checked,
            "generated": generated,
            "emailed": emailed,
            "skipped": skipped,
        }
