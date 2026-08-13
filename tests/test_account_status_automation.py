"""Unit tests for account-level status automation rules.

Tests the pure decision logic in ``app.services.account_status`` — no DB
required (the async bump helper just persists whatever this function decides).
"""
import pytest

from app.services.account_status import (
    ACCOUNT_STATUS_LIFECYCLE_RANK,
    should_advance_account_status,
)


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        # Forward lifecycle moves are allowed.
        (None, "in_progress", True),
        ("cold", "in_progress", True),
        ("in_progress", "meeting_booked", True),
        ("meeting_booked", "meeting_done", True),
        ("meeting_done", "in_pipeline", True),
        # Same-value no-ops.
        ("in_progress", "in_progress", False),
        (None, None, False),
        # Downgrades are rejected.
        ("meeting_booked", "in_progress", False),
        ("meeting_done", "meeting_booked", False),
        ("in_pipeline", "meeting_done", False),
        ("in_pipeline", "in_progress", False),
        # Cold is the weakest state — every lifecycle signal advances it.
        ("cold", "meeting_booked", True),
        ("cold", "meeting_done", True),
        ("cold", "in_pipeline", True),
    ],
)
def test_lifecycle_advance(current, target, expected):
    assert should_advance_account_status(current, target) is expected


@pytest.mark.parametrize(
    "parked",
    ["not_a_fit", "dnd", "reach_out_later"],
)
def test_parked_accounts_reject_in_progress(parked):
    """Weak engagement must NOT revive a manually-parked account."""
    assert should_advance_account_status(parked, "in_progress") is False


@pytest.mark.parametrize(
    "parked",
    ["not_a_fit", "dnd", "reach_out_later"],
)
@pytest.mark.parametrize("target", ["meeting_booked", "meeting_done", "in_pipeline"])
def test_parked_accounts_can_be_revived_by_strong_signal(parked, target):
    """A booked meeting / done meeting / live deal is reality — it may override
    a manual parked label."""
    assert should_advance_account_status(parked, target) is True


def test_cannot_automate_toward_manual_states():
    """Automation only targets lifecycle states; parked states are never a
    legitimate automated target."""
    for parked in ("not_a_fit", "dnd", "reach_out_later"):
        assert should_advance_account_status("cold", parked) is False
        assert should_advance_account_status("in_pipeline", parked) is False


def test_rank_table_is_complete():
    assert ACCOUNT_STATUS_LIFECYCLE_RANK == {
        "cold": 0,
        "in_progress": 1,
        "meeting_booked": 2,
        "meeting_done": 3,
        "in_pipeline": 4,
    }
