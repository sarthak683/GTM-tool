"""
Resend email client — sends outreach emails via Resend API.

Mock mode: logs the email but doesn't send when RESEND_API_KEY is empty.
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _build_html(subject: str, body_text: str, from_name: str = "Beacon Sales") -> str:
    """Wrap plain text body in a minimal, clean HTML email template."""
    # Convert newlines to <br> for HTML display
    html_body = body_text.replace("\n", "<br>")
    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
        <div style="border-left: 3px solid #6366f1; padding-left: 16px; margin-bottom: 24px;">
            <p style="font-size: 12px; color: #888; margin: 0 0 4px 0;">From {from_name} · Beacon.li</p>
        </div>
        <div style="font-size: 15px; line-height: 1.7; color: #222;">
            {html_body}
        </div>
        <hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">
        <p style="font-size: 11px; color: #aaa;">
            Sent via <a href="https://beacon.li" style="color: #6366f1;">Beacon CRM</a>
        </p>
    </div>
    """


async def send_email(
    to: str,
    subject: str,
    body: str,
    from_name: str = "Beacon Sales",
) -> dict:
    """
    Send a single email via Resend.
    Returns dict with 'id' on success or 'error' on failure.
    """
    if not settings.RESEND_API_KEY:
        logger.warning("Resend API key not configured — cannot send email to %s", to)
        return {"error": "Resend API key not configured", "status": "not_configured"}

    try:
        params = {
            "from": f"{from_name} <{settings.RESEND_FROM_EMAIL}>",
            "to": [to],
            "subject": subject,
            "html": _build_html(subject, body, from_name),
            "text": body,  # plain text fallback
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=params,
            )
        if response.status_code >= 400:
            logger.error("Resend send failed: %s %s", response.status_code, response.text[:500])
            return {
                "error": response.text,
                "status": "failed",
                "status_code": response.status_code,
            }
        payload = response.json()
        logger.info("Email sent via Resend: %s", payload)
        return {"id": payload.get("id", ""), "status": "sent"}

    except Exception as e:
        logger.error(f"Resend send failed: {e}")
        return {"error": str(e), "status": "failed"}
