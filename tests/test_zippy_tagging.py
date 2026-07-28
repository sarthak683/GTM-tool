"""The Zippy tagging rule — the single capture point for reps' manual sends.

Reps CC Zippy on their own outbound so the CRM sees the send without every rep
connecting every mailbox they send from. Ingestion (app/tasks/email_sync.py) and
the Emails Out metric (analytics) must agree on what counts as tagged, so both
import these helpers.
"""
from app.services.zippy_tagging import (
    BEACON_SENDING_DOMAINS,
    any_zippy_address,
    is_our_sending_address,
    is_zippy_address,
)


def test_all_three_sending_domains_are_known():
    assert BEACON_SENDING_DOMAINS == {"beacon.li", "beaconli.co", "beaconli.com"}


def test_plain_zippy_mailbox_is_tagged():
    assert is_zippy_address("zippy@beacon.li")


def test_per_deal_alias_is_tagged():
    """The form reps actually use most — 59 of 72 tagged emails in prod."""
    assert is_zippy_address("zippy+acme-corp@beacon.li")


def test_zippy_tagged_on_an_alternate_sending_domain():
    """A rep working out of sipra@beaconli.com may CC Zippy on that domain."""
    assert is_zippy_address("zippy@beaconli.com")
    assert is_zippy_address("zippy+acme@beaconli.co")


def test_case_and_padding_are_ignored():
    assert is_zippy_address("  ZIPPY+Acme@BEACON.LI  ")


def test_lookalikes_are_not_tagged():
    assert not is_zippy_address("zippy@gmail.com")        # our local-part, their domain
    assert not is_zippy_address("zippyclone@beacon.li")    # must be zippy or zippy+
    assert not is_zippy_address("notzippy@beacon.li")
    assert not is_zippy_address("sipra@beacon.li")
    assert not is_zippy_address("")
    assert not is_zippy_address(None)
    assert not is_zippy_address("no-at-sign")


def test_any_zippy_address_scans_a_recipient_list():
    addrs = {"buyer@acme.com", "sipra@beaconli.com", "zippy+acme@beacon.li"}
    assert any_zippy_address(addrs)
    assert not any_zippy_address({"buyer@acme.com", "sipra@beaconli.com"})
    assert not any_zippy_address([])
    assert not any_zippy_address(None)


def test_our_sending_addresses_span_all_three_domains():
    assert is_our_sending_address("sipra@beacon.li")
    assert is_our_sending_address("sipra@beaconli.com")
    assert is_our_sending_address("pulkit@beaconli.co")
    assert is_our_sending_address("  Sipra@Beacon.LI ")


def test_prospect_addresses_are_not_ours():
    """This guard is what stops a prospect's reply-all on a Zippy-CC'd thread
    being counted as one of OUR sends."""
    assert not is_our_sending_address("buyer@acme.com")
    assert not is_our_sending_address("someone@beacon.li.evil.com")
    assert not is_our_sending_address("")
    assert not is_our_sending_address(None)


def test_unmatched_alias_is_still_our_send():
    """The shape that made the first fix useless in practice.

    Reps tag `zippy+<alias>` on 59 of every 72 tagged emails. When that alias
    names no deal — a typo, or far more often a prospecting thread with no deal
    yet — ingestion used to return early and drop the email, before any
    dealless fallback could see it. The send is still ours and still counts.
    """
    assert is_zippy_address("zippy+no-such-deal@beacon.li")
    assert is_our_sending_address("sipra@beaconli.com")


def test_the_fallback_gate_as_ingestion_applies_it():
    """Mirrors the condition in email_sync: a dealless email is recorded only
    when Zippy is tagged AND the sender is one of ours."""

    def would_record(sender, addrs):
        return any_zippy_address(addrs) and is_our_sending_address(sender)

    # Rep's cold outreach from an alternate domain, Zippy CC'd -> counted.
    assert would_record("sipra@beaconli.com", {"prospect@acme.com", "zippy+acme@beacon.li"})
    # Same but no Zippy tag -> not ours to count.
    assert not would_record("sipra@beaconli.com", {"prospect@acme.com"})
    # Prospect replies on a Zippy-CC'd thread -> not our send.
    assert not would_record("prospect@acme.com", {"sipra@beaconli.com", "zippy@beacon.li"})
