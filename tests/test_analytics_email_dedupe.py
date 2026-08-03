from datetime import datetime
from types import SimpleNamespace

from app.api.v1.endpoints.analytics import _manual_email_dedupe_key, _reported_email_from


def _email_row(**overrides):
    values = {
        "source": "personal_email_sync",
        "external_source": None,
        "email_from": "annie@beacon.li",
        "email_to": "Prospect Person <prospect@example.com>",
        "contact_id": None,
        "email_subject": "Re: Beacon implementation plan",
        "created_at": datetime(2026, 6, 10, 9, 15),
        "email_message_id": None,
        "event_metadata": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_manual_email_dedupe_key_matches_same_recipient_day_and_subject():
    first = _email_row(created_at=datetime(2026, 6, 10, 9, 15))
    second = _email_row(
        created_at=datetime(2026, 6, 10, 14, 45),
        email_subject="Fwd: Re: Beacon implementation plan",
    )

    assert _manual_email_dedupe_key(first, "rep-1") == _manual_email_dedupe_key(second, "rep-1")


def test_manual_email_dedupe_key_keeps_different_subjects_separate():
    first = _email_row(email_subject="Beacon implementation plan")
    second = _email_row(email_subject="Commercial terms")

    assert _manual_email_dedupe_key(first, "rep-1") != _manual_email_dedupe_key(second, "rep-1")


def test_manual_email_dedupe_key_collapses_blank_subject_same_recipient_day():
    first = _email_row(email_subject="", created_at=datetime(2026, 7, 17, 12, 32))
    second = _email_row(email_subject="", created_at=datetime(2026, 7, 17, 12, 35))

    assert _manual_email_dedupe_key(first, "rep-1") == _manual_email_dedupe_key(second, "rep-1")


def test_manual_email_dedupe_key_does_not_touch_instantly_rows():
    row = _email_row(source="instantly", email_from="annie@beaconli.com")

    assert _manual_email_dedupe_key(row, "rep-1") is None


def test_manual_email_dedupe_key_keeps_distinct_gmail_messages():
    first = _email_row(email_message_id="<first@example.com>")
    second = _email_row(email_message_id="<second@example.com>")

    assert _manual_email_dedupe_key(first, "rep-1") is None
    assert _manual_email_dedupe_key(second, "rep-1") is None


def test_reported_email_from_prefers_retained_raw_sender():
    row = _email_row(
        email_from="sipra@beacon.li",
        event_metadata={"raw_email_from": "sipra@beaconli.com"},
    )

    assert _reported_email_from(row) == "sipra@beaconli.com"
