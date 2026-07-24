from datetime import datetime

from app.models.contact import Contact
from app.services.sdr_reassignment import reset_contact_outreach_progress


def test_reset_contact_outreach_progress_clears_channel_state():
    contact = Contact(
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
