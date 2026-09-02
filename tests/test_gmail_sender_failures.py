from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.v1.endpoints import settings as settings_endpoint
from app.clients import gmail_sender
from app.services import us_pod_call_report, weekly_digest


class _InvalidGrantResponse:
    status_code = 400

    @staticmethod
    def json():
        return {"error": "invalid_grant", "error_description": "Bad Request"}


class _InvalidGrantClient:
    calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        type(self).calls += 1
        return _InvalidGrantResponse()


async def test_send_gmail_email_maps_invalid_grant_to_reconnect(monkeypatch):
    _InvalidGrantClient.calls = 0
    monkeypatch.setattr(gmail_sender.httpx, "AsyncClient", lambda **_kwargs: _InvalidGrantClient())
    token_data = {
        "token": "expired-access-token",
        "refresh_token": "revoked-refresh-token",
        "scopes": [gmail_sender.GMAIL_SEND_SCOPE],
        "expiry": "2000-01-01T00:00:00+00:00",
    }

    result, updated_token = await gmail_sender.send_gmail_email(
        token_data=token_data,
        from_email="sender@example.com",
        to="recipient@example.com",
        subject="Report",
        body="Body",
    )

    assert result == {
        "status": "failed",
        "error": gmail_sender.GMAIL_RECONNECT_REQUIRED_ERROR,
        "reconnect_required": True,
    }
    assert updated_token == token_data
    assert _InvalidGrantClient.calls == 1


async def test_daily_report_stops_refreshing_after_reconnect_failure(monkeypatch):
    report_day = date(2026, 9, 1)
    report = {
        "report_date": report_day,
        "report_type": "daily",
        "period_start": report_day,
        "period_end": report_day,
        "timezone": "America/Chicago",
    }
    row = SimpleNamespace(
        report_sender_email="sender@example.com",
        report_sender_connected_email="sender@example.com",
        report_sender_token_data={"token": "expired"},
        report_sender_last_error=None,
    )
    session = SimpleNamespace(get=AsyncMock(return_value=row), add=lambda _row: None, commit=AsyncMock())
    monkeypatch.setattr(us_pod_call_report, "load_sales_report_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(us_pod_call_report, "build_us_pod_call_report", AsyncMock(return_value=report))
    monkeypatch.setattr(us_pod_call_report, "_report_subject", lambda _report: "Report")
    monkeypatch.setattr(us_pod_call_report, "_render_report_text", lambda _report: "Body")
    monkeypatch.setattr(us_pod_call_report, "_render_report_html", lambda _report: "<p>Body</p>")
    monkeypatch.setattr(
        us_pod_call_report,
        "_resolve_report_recipients",
        lambda _recipients, _settings: (["one@example.com", "two@example.com", "three@example.com"], []),
    )
    send = AsyncMock(
        return_value=(
            {
                "status": "failed",
                "error": gmail_sender.GMAIL_RECONNECT_REQUIRED_ERROR,
                "reconnect_required": True,
            },
            row.report_sender_token_data,
        )
    )
    monkeypatch.setattr(us_pod_call_report, "send_gmail_email", send)

    result = await us_pod_call_report.send_us_pod_call_report_email(session, report_day)

    assert send.await_count == 1
    assert len(result["send_results"]) == 3
    assert all(item["reconnect_required"] for item in result["send_results"])
    assert row.report_sender_last_error == gmail_sender.GMAIL_RECONNECT_REQUIRED_ERROR
    session.commit.assert_awaited_once()


async def test_weekly_digest_stops_refreshing_after_reconnect_failure(monkeypatch):
    period_start = date(2026, 8, 24)
    period_end = date(2026, 8, 30)
    digest = weekly_digest.WeeklyDigest(period_start, period_end, "Asia/Kolkata")
    digest.subject = "Weekly digest"
    digest.body = "Body"
    digest.html_body = "<p>Body</p>"
    row = SimpleNamespace(
        report_sender_email="sender@example.com",
        report_sender_connected_email="sender@example.com",
        report_sender_token_data={"token": "expired"},
        report_sender_last_error=None,
    )
    session = SimpleNamespace(get=AsyncMock(return_value=row), add=lambda _row: None, commit=AsyncMock())
    monkeypatch.setattr(weekly_digest, "build_weekly_digest", AsyncMock(return_value=digest))
    monkeypatch.setattr(
        weekly_digest,
        "_resolve_digest_recipients",
        lambda _recipients, _settings: (["one@example.com", "two@example.com"], []),
    )
    send = AsyncMock(
        return_value=(
            {
                "status": "failed",
                "error": gmail_sender.GMAIL_RECONNECT_REQUIRED_ERROR,
                "reconnect_required": True,
            },
            row.report_sender_token_data,
        )
    )
    monkeypatch.setattr(weekly_digest, "send_gmail_email", send)

    result = await weekly_digest.send_weekly_digest_email(
        session,
        period_start,
        period_end,
        digest_settings=weekly_digest.DEFAULT_WEEKLY_DIGEST_SETTINGS,
    )

    assert send.await_count == 1
    assert len(result.send_results) == 2
    assert all(item["reconnect_required"] for item in result.send_results)
    assert row.report_sender_last_error == gmail_sender.GMAIL_RECONNECT_REQUIRED_ERROR
    session.commit.assert_awaited_once()


async def test_report_sender_status_requires_reconnect_after_invalid_grant(monkeypatch):
    row = SimpleNamespace(
        report_sender_email="sender@example.com",
        report_sender_connected_email="sender@example.com",
        report_sender_token_data={"scopes": [gmail_sender.GMAIL_SEND_SCOPE]},
        report_sender_connected_at=None,
        report_sender_last_error=gmail_sender.GMAIL_RECONNECT_REQUIRED_ERROR,
    )
    monkeypatch.setattr(settings_endpoint, "_get_or_create", AsyncMock(return_value=row))

    status = await settings_endpoint._report_sender_status(SimpleNamespace())

    assert status.configured is False
    assert status.has_send_scope is True
    assert status.last_error == gmail_sender.GMAIL_RECONNECT_REQUIRED_ERROR
