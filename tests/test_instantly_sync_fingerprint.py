"""Change detection for the Instantly campaign poll.

``sync_active_instantly_campaigns`` runs every 900 seconds and issues one
Instantly API call PER SEQUENCE. With no change detection it re-fetched every
linked lead on every pass: production logs show the identical result each run —
``synced: 731, campaigns_checked: 748, errors: 0`` — taking ~250 seconds, about
a 28% duty cycle on a worker and ~70k API calls a day, to discover nothing had
moved.

A campaign's analytics payload aggregates its leads' activity, so an identical
payload means no lead in it changed and the per-lead fetch can be skipped.

Pure-logic tests: no database, Redis, or Celery.
"""
from __future__ import annotations

import inspect
from datetime import datetime

from app.tasks import instantly_sync
from app.tasks.instantly_sync import _analytics_fingerprint


ANALYTICS = {"campaign_status": 1, "emails_sent_count": 120, "open_count": 44, "reply_count": 3}


def test_identical_analytics_produce_the_same_fingerprint():
    assert _analytics_fingerprint(dict(ANALYTICS)) == _analytics_fingerprint(dict(ANALYTICS))


def test_key_order_does_not_count_as_a_change():
    """A reordered payload must not force a pointless full sync."""
    shuffled = {k: ANALYTICS[k] for k in reversed(list(ANALYTICS))}
    assert _analytics_fingerprint(shuffled) == _analytics_fingerprint(ANALYTICS)


def test_a_new_reply_changes_the_fingerprint():
    """The case the skip must never swallow: real lead activity."""
    moved = {**ANALYTICS, "reply_count": 4}
    assert _analytics_fingerprint(moved) != _analytics_fingerprint(ANALYTICS)


def test_a_new_open_changes_the_fingerprint():
    moved = {**ANALYTICS, "open_count": 45}
    assert _analytics_fingerprint(moved) != _analytics_fingerprint(ANALYTICS)


def test_fingerprint_is_scoped_to_a_utc_day():
    """Bounded staleness: the same payload fingerprints differently tomorrow,
    so every campaign gets one full reconciliation a day even if its counters
    never move."""
    fingerprint = _analytics_fingerprint(ANALYTICS)
    today = datetime.utcnow().date().isoformat()
    assert fingerprint.endswith(f":{today}")

    digest, _, day = fingerprint.rpartition(":")
    assert digest and day == today


def test_unusable_analytics_fall_back_to_always_syncing():
    """Returning None means "no fingerprint", which takes the full path — the
    old behaviour, always correct, just slower."""
    for junk in (None, "not a dict", [], 42):
        assert _analytics_fingerprint(junk) is None


def test_unserialisable_payload_does_not_raise():
    """An exotic value must degrade to a full sync, never break the task."""
    class Exotic:
        pass

    # default=str keeps this serialisable; the point is that it does not raise.
    assert _analytics_fingerprint({"weird": Exotic()}) is not None


# ── how the task uses it ─────────────────────────────────────────────────────

def test_task_skips_the_lead_fetch_when_the_fingerprint_matches():
    source = inspect.getsource(instantly_sync._async_sync_active_campaigns)
    assert "if fingerprint and seq.instantly_sync_fingerprint == fingerprint:" in source
    assert "skipped_unchanged += 1" in source


def test_task_records_the_fingerprint_only_after_a_clean_pass():
    """Recording it on a failed fetch would mark the campaign 'reconciled' and
    defer the retry until its analytics happened to change."""
    source = inspect.getsource(instantly_sync._async_sync_active_campaigns)
    assign = "seq.instantly_sync_fingerprint = fingerprint"
    assert assign in source

    # The assignment must sit in the `else` of the per-contact try/except,
    # never in the except branch.
    before = source[: source.index(assign)]
    assert before.rstrip().endswith("if fingerprint:"), "fingerprint assignment moved out of the success path"
    assert "errors += 1\n                        # Scope matters" in source, "per-contact error handler changed shape"


def test_task_reports_the_skip_count():
    """A run where this stays 0 means change detection has stopped working and
    the task is back to its full ~250s sweep — worth being able to see."""
    source = inspect.getsource(instantly_sync._async_sync_active_campaigns)
    assert '"skipped_unchanged": skipped_unchanged,' in source
