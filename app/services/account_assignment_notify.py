"""
Email notifications for account (company) AE/SDR assignment — both a brand
new account being created with an owner picked, and an existing account
being (re)assigned later. Mirrors the pipeline stage-change alert's Gmail
send path (app/api/v1/endpoints/deals.py::_send_stage_change_alert_background)
so both features share the same sender account, failure handling, and
token-refresh bookkeeping.

Every email always includes maithili@beacon.li and annie@beacon.li in
addition to the assigned person, per standing instruction — regardless of
who did the assigning or which of the two (AE/SDR) slot was set.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

ALWAYS_NOTIFY_EMAILS = ["maithili@beacon.li", "annie@beacon.li"]


def _dedupe_emails(emails: list[str | None]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in emails:
        e = (raw or "").strip()
        key = e.lower()
        if e and key not in seen:
            seen.add(key)
            out.append(e)
    return out


async def _send_to_recipients(subject: str, plain_body: str, html_body: str, recipients: list[str], *, log_ref: str) -> None:
    """The shared Gmail send loop — one send per recipient (send_gmail_email
    takes a single `to` address), threading the refreshed token through each
    call and recording the last error, exactly like the stage-change alert.
    """
    from app.clients.gmail_sender import send_gmail_email
    from app.database import AsyncSessionLocal
    from app.models.settings import WorkspaceSettings

    async with AsyncSessionLocal() as session:
        try:
            settings_row = await session.get(WorkspaceSettings, 1)
            if (
                not settings_row
                or not settings_row.report_sender_email
                or not settings_row.report_sender_connected_email
                or not settings_row.report_sender_token_data
            ):
                logger.warning("account-assignment email: report sender Gmail not connected — skipping %s", log_ref)
                return
            if settings_row.report_sender_email.lower() != settings_row.report_sender_connected_email.lower():
                logger.warning(
                    "account-assignment email: configured sender %s does not match connected Gmail %s — skipping %s",
                    settings_row.report_sender_email, settings_row.report_sender_connected_email, log_ref,
                )
                return

            token_data = settings_row.report_sender_token_data
            failures: list[str] = []
            for email in recipients:
                try:
                    result, token_data = await send_gmail_email(
                        token_data=token_data,
                        from_email=settings_row.report_sender_email,
                        to=email,
                        subject=subject,
                        body=plain_body,
                        html_body=html_body,
                        from_name="Beacon Account Assignments",
                    )
                except Exception as exc:
                    logger.exception("account-assignment email: send to %s failed for %s", email, log_ref)
                    result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                if result.get("status") != "sent":
                    failures.append(f"{email}: {result.get('error') or 'Gmail send failed'}")

            if token_data != settings_row.report_sender_token_data:
                settings_row.report_sender_token_data = token_data
            settings_row.report_sender_last_error = " | ".join(failures)[:500] if failures else None
            session.add(settings_row)
            await session.commit()
        except Exception:
            logger.exception("account-assignment email background task failed for %s", log_ref)


def _table_row(label: str, value: str) -> str:
    return (
        f'<tr><td style="padding:4px 14px 4px 0;color:#5b6b7f;white-space:nowrap;">{label}</td>'
        f'<td style="padding:4px 0;font-weight:700;color:#1f2d3d;">{value}</td></tr>'
    )


async def notify_account_assigned(
    *,
    account_name: str,
    role_label: str,  # "AE" or "SDR"
    assignee_name: str,
    assignee_email: str,
    assigned_by_name: str,
    assigned_at: datetime,
    is_new_account: bool,
) -> None:
    """One account, one assignment (either a brand-new account created with
    an owner picked, or an existing account reassigned) — sent right away.
    """
    when_str = assigned_at.strftime("%b %d, %Y, %I:%M %p") + " UTC"
    safe_account = html.escape(account_name)
    safe_assignee = html.escape(assignee_name)
    safe_by = html.escape(assigned_by_name)
    verb = "added and assigned" if is_new_account else "assigned"

    subject = f"Account {verb}: {account_name} → {assignee_name} ({role_label})"
    html_body = f"""
    <p style="font-family:-apple-system,sans-serif;font-size:14px;color:#1f2d3d;">
      <strong>{safe_account}</strong> was {verb} to <strong>{safe_assignee}</strong> as <strong>{role_label}</strong>.
    </p>
    <table style="border-collapse:collapse;font-family:-apple-system,sans-serif;font-size:14px;">
      {_table_row("Account", safe_account)}
      {_table_row("Assigned to", f"{safe_assignee} ({role_label})")}
      {_table_row("Assigned by", safe_by)}
      {_table_row("When", html.escape(when_str))}
    </table>
    """
    plain_body = (
        f"Account: {account_name}\n"
        f"Assigned to: {assignee_name} ({role_label})\n"
        f"Assigned by: {assigned_by_name}\n"
        f"When: {when_str}"
    )
    recipients = _dedupe_emails([assignee_email, *ALWAYS_NOTIFY_EMAILS])
    await _send_to_recipients(subject, plain_body, html_body, recipients, log_ref=account_name)


async def notify_accounts_bulk_assigned(
    *,
    count: int,
    role_label: str,
    assignee_name: str,
    assignee_email: str,
    assigned_by_name: str,
    assigned_at: datetime,
) -> None:
    """Many accounts, one recipient, one action — a single summary email
    instead of one email per account, so a large bulk reassignment doesn't
    flood anyone's inbox.
    """
    if count <= 0:
        return
    when_str = assigned_at.strftime("%b %d, %Y, %I:%M %p") + " UTC"
    safe_assignee = html.escape(assignee_name)
    safe_by = html.escape(assigned_by_name)
    plural = "account" if count == 1 else "accounts"

    subject = f"{count} {plural} bulk-assigned to {assignee_name} ({role_label})"
    html_body = f"""
    <p style="font-family:-apple-system,sans-serif;font-size:14px;color:#1f2d3d;">
      <strong>{count}</strong> {plural} {"was" if count == 1 else "were"} bulk-assigned to <strong>{safe_assignee}</strong> as <strong>{role_label}</strong>.
    </p>
    <table style="border-collapse:collapse;font-family:-apple-system,sans-serif;font-size:14px;">
      {_table_row("Accounts", str(count))}
      {_table_row("Assigned to", f"{safe_assignee} ({role_label})")}
      {_table_row("Assigned by", safe_by)}
      {_table_row("When", html.escape(when_str))}
    </table>
    """
    plain_body = (
        f"Accounts: {count}\n"
        f"Assigned to: {assignee_name} ({role_label})\n"
        f"Assigned by: {assigned_by_name}\n"
        f"When: {when_str}"
    )
    recipients = _dedupe_emails([assignee_email, *ALWAYS_NOTIFY_EMAILS])
    await _send_to_recipients(subject, plain_body, html_body, recipients, log_ref=f"bulk:{assignee_name}:{count}")
