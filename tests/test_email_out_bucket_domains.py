"""Emails Out bucketing across our alternate sending domains.

Reps send manual mail from beaconli.com / beaconli.co as well as beacon.li and
tag Zippy in CC. Those two domains are also the Instantly cold-outreach
domains, so the sending domain ALONE cannot decide the bucket — doing that
filed genuine manual sends under Instantly and left them out of the manual
count. Positive manual evidence (a Zippy CC, or a rep-inbox sync source) wins;
otherwise the lookalike domains still default to Instantly.
"""
from types import SimpleNamespace

from app.api.v1.endpoints.analytics import (
    _email_out_bucket,
    _is_zippy_address,
    _zippy_in_cc_or_bcc,
)


def _row(**overrides):
    values = {
        "source": None,
        "external_source": None,
        "email_from": "sipra@beacon.li",
        "email_cc": None,
        "email_bcc": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ── Zippy address recognition ────────────────────────────────────────────────

def test_plain_and_alias_zippy_addresses_are_both_recognised():
    assert _is_zippy_address("zippy@beacon.li")
    assert _is_zippy_address("zippy+acme-corp@beacon.li")
    assert _is_zippy_address("ZIPPY+Acme@BEACON.LI")
    assert _is_zippy_address("zippy@beaconli.com")
    assert _is_zippy_address("zippy+acme@beaconli.co")


def test_non_zippy_addresses_are_not_recognised():
    assert not _is_zippy_address("sipra@beacon.li")
    assert not _is_zippy_address("zippy@gmail.com")       # our local-part, someone else's domain
    assert not _is_zippy_address("zippyclone@beacon.li")  # prefix must be exactly zippy or zippy+
    assert not _is_zippy_address("")
    assert not _is_zippy_address("not-an-address")


def test_alias_zippy_in_cc_is_detected():
    row = _row(email_cc="prospect@acme.com, zippy+acme-corp@beacon.li")
    assert _zippy_in_cc_or_bcc(row) is True


def test_zippy_detected_in_bcc_too():
    assert _zippy_in_cc_or_bcc(_row(email_bcc="zippy+acme@beacon.li")) is True


# ── The reported gap: manual sends from alternate domains ────────────────────

def test_manual_send_from_alternate_domain_with_zippy_cc_counts_as_manual():
    """The reported bug: this used to bucket as Instantly purely because
    beaconli.com is also an Instantly sending domain."""
    row = _row(email_from="sipra@beaconli.com", email_cc="zippy+acme@beacon.li")
    assert _email_out_bucket(row) == "manual"


def test_manual_send_from_beaconli_co_with_zippy_cc_counts_as_manual():
    row = _row(email_from="pulkit@beaconli.co", email_cc="zippy@beacon.li")
    assert _email_out_bucket(row) == "manual"


def test_alternate_domain_send_via_rep_inbox_sync_counts_as_manual():
    row = _row(email_from="sipra@beaconli.com", source="personal_email_sync")
    assert _email_out_bucket(row) == "manual"


def test_alternate_domain_zippy_tagged_on_its_own_domain_counts_as_manual():
    """Rep working from beaconli.com may CC zippy on that domain, not beacon.li."""
    row = _row(email_from="sipra@beaconli.com", email_cc="zippy+acme@beaconli.com")
    assert _email_out_bucket(row) == "manual"


# ── Regressions: existing prod row shapes must not change bucket ─────────────

def test_instantly_source_stays_instantly_even_from_primary_domain():
    row = _row(email_from="annie@beacon.li", source="instantly")
    assert _email_out_bucket(row) == "instantly"


def test_instantly_external_source_stays_instantly():
    row = _row(email_from="annie@beaconli.com", external_source="instantly_sync")
    assert _email_out_bucket(row) == "instantly"


def test_unattributed_alternate_domain_send_still_defaults_to_instantly():
    """503 prod rows look like this — campaign traffic with no manual signal."""
    row = _row(email_from="pulkit@beaconli.com", source="instantly")
    assert _email_out_bucket(row) == "instantly"

    bare = _row(email_from="pulkit@beaconli.com")
    assert _email_out_bucket(bare) == "instantly"


def test_instantly_row_with_blank_sender_is_still_counted():
    """~419 prod rows look like this. Requiring one of our sending domains BEFORE
    the source check silently dropped them out of Emails Out entirely."""
    row = _row(email_from=None, source="instantly")
    assert _email_out_bucket(row) == "instantly"

    blank = _row(email_from="", source="instantly")
    assert _email_out_bucket(blank) == "instantly"


def test_instantly_row_sent_from_a_prospect_domain_is_still_counted():
    """~47 prod rows: Instantly reply/webhook shapes carry the prospect's domain."""
    row = _row(email_from="buyer@o9solutions.com", source="instantly")
    assert _email_out_bucket(row) == "instantly"


def test_primary_domain_personal_sync_stays_manual():
    row = _row(email_from="annie@beacon.li", source="personal_email_sync")
    assert _email_out_bucket(row) == "manual"


def test_primary_domain_with_no_signal_is_still_not_counted():
    assert _email_out_bucket(_row(email_from="annie@beacon.li")) is None


def test_external_sender_is_never_counted():
    row = _row(email_from="buyer@acme.com", source="personal_email_sync")
    assert _email_out_bucket(row) is None


def test_prospect_ccing_zippy_from_their_own_domain_is_not_our_send():
    """Guard the widened domain check: the CC rule must not promote inbound mail."""
    row = _row(email_from="buyer@acme.com", email_cc="zippy@beacon.li")
    assert _email_out_bucket(row) is None
