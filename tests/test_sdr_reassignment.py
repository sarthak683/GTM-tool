from datetime import datetime, timedelta

from app.models.contact import Contact
from app.services.sdr_reassignment import (
    instantly_counts_since_assignment,
    open_timestamp_within_assignment,
    reset_contact_outreach_progress,
)


def _engaged_contact(**overrides) -> Contact:
    defaults = dict(
        first_name="A",
        last_name="Prospect",
        email="a@example.com",
        email_open_count=3,
        email_click_count=1,
        email_last_opened_at=datetime(2026, 7, 1, 12, 0, 0),
        call_status="connected",
        call_disposition="interested_follow_up_required",
        call_notes="call notes",
        call_last_at=datetime(2026, 7, 1, 13, 0, 0),
        next_followup_at=datetime(2026, 7, 2, 13, 0, 0),
        linkedin_status="accepted",
        linkedin_last_at=datetime(2026, 7, 1, 14, 0, 0),
        sequence_status="sent",
        instantly_status="active",
    )
    defaults.update(overrides)
    return Contact(**defaults)


def test_reset_contact_outreach_progress_clears_channel_state():
    contact = _engaged_contact()

    reset_contact_outreach_progress(contact)

    assert contact.email_open_count == 0
    assert contact.email_click_count == 0
    assert contact.email_last_opened_at is None
    assert contact.call_status is None
    assert contact.call_disposition is None
    assert contact.call_notes is None
    assert contact.call_last_at is None
    assert contact.next_followup_at is None
    assert contact.linkedin_status is None
    assert contact.linkedin_last_at is None
    assert contact.sequence_status is None
    assert contact.instantly_status is None


def test_reset_stamps_watermark_so_activity_aggregates_can_be_filtered():
    """Call attempts live in the Activity table and cannot be zeroed without
    destroying the audit trail — the watermark is what hides them."""
    contact = _engaged_contact()
    assert contact.sdr_assigned_at is None

    before = datetime.utcnow()
    reset_contact_outreach_progress(contact)

    assert contact.sdr_assigned_at is not None
    assert contact.sdr_assigned_at >= before


def test_reset_records_instantly_baseline_so_the_poller_cannot_restore_counts():
    contact = _engaged_contact(email_open_count=7, email_click_count=2)

    reset_contact_outreach_progress(contact)

    # Instantly still reports the lifetime totals it always did...
    lead = {"email_open_count": 7, "email_click_count": 2}
    assert instantly_counts_since_assignment(contact, lead) == (0, 0)

    # ...and only genuinely new engagement moves the count off zero.
    lead = {"email_open_count": 9, "email_click_count": 3}
    assert instantly_counts_since_assignment(contact, lead) == (2, 1)


def test_instantly_baseline_accumulates_across_successive_reassignments():
    contact = _engaged_contact(email_open_count=4, email_click_count=1)
    reset_contact_outreach_progress(contact)

    # Second SDR earns 3 more opens, then hands the prospect on again.
    contact.email_open_count = 3
    reset_contact_outreach_progress(contact)

    assert instantly_counts_since_assignment(contact, {"email_open_count": 7}) == (0, 0)
    assert instantly_counts_since_assignment(contact, {"email_open_count": 8}) == (1, 0)


def test_instantly_counts_never_go_negative_on_a_shrinking_lead_total():
    """Deleting and re-adding a lead in Instantly resets its totals; a stale
    baseline must not produce a negative count."""
    contact = _engaged_contact(email_open_count=10)
    reset_contact_outreach_progress(contact)

    assert instantly_counts_since_assignment(contact, {"email_open_count": 1}) == (0, 0)


def test_instantly_counts_untouched_for_a_contact_never_reassigned():
    contact = _engaged_contact()

    lead = {"email_open_count": 5, "email_click_count": 2}
    assert instantly_counts_since_assignment(contact, lead) == (5, 2)


def test_open_timestamp_before_reassignment_is_dropped():
    contact = _engaged_contact()
    reset_contact_outreach_progress(contact)
    watermark = contact.sdr_assigned_at

    stale = watermark - timedelta(days=3)
    fresh = watermark + timedelta(hours=1)

    assert open_timestamp_within_assignment(contact, stale) is None
    assert open_timestamp_within_assignment(contact, fresh) == fresh
    assert open_timestamp_within_assignment(contact, None) is None


def test_open_timestamp_kept_when_never_reassigned():
    contact = _engaged_contact()
    stamp = datetime(2026, 7, 1, 12, 0, 0)

    assert open_timestamp_within_assignment(contact, stamp) == stamp
