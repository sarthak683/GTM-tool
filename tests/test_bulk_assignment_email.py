import asyncio
from datetime import datetime

from app.services import account_assignment_notify as n


def _send(monkeypatch, **kwargs):
    sent = {}

    async def fake_send(subject, plain, html_body, recipients, log_ref):
        sent.update(subject=subject, plain=plain, html=html_body)

    monkeypatch.setattr(n, "_send_to_recipients", fake_send)
    asyncio.run(
        n.notify_accounts_bulk_assigned(
            role_label="AE",
            assignee_name="Sandeep Sinha",
            assignee_email="s@beacon.li",
            assigned_by_name="Annie Gupta",
            assigned_at=datetime(2026, 10, 14, 7, 38),
            **kwargs,
        )
    )
    return sent


def test_lists_account_names_escaped_and_sorted(monkeypatch):
    sent = _send(monkeypatch, count=2, account_names=["Zeta <b>Inc", "acme"])
    assert "- acme\n- Zeta <b>Inc" in sent["plain"]
    assert "Zeta &lt;b&gt;Inc" in sent["html"] and "<b>Inc" not in sent["html"]
    assert "more" not in sent["plain"]


def test_caps_at_50_with_remainder(monkeypatch):
    names = [f"Account {i:03d}" for i in range(120)]
    sent = _send(monkeypatch, count=120, account_names=names)
    assert sent["plain"].count("\n- ") == 50
    assert "…and 70 more" in sent["plain"]
    assert "…and 70 more" in sent["html"]
    assert sent["subject"].startswith("120 accounts bulk-assigned")


def test_without_names_email_is_unchanged(monkeypatch):
    sent = _send(monkeypatch, count=5)
    assert "Accounts:\n-" not in sent["plain"]
    assert "<ul" not in sent["html"]
