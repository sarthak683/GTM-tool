"""Pure-logic tests for app.services.business_days.business_days_between.

The stuck-deal thresholds are documented as BUSINESS days; this helper is the
single definition of business-day dwell shared by performance_metrics
(scorecard "stuck deals") and DealRepository (board ``is_stalled``). These
tests pin down the half-open [start, end) date semantics so neither surface
can drift.

2025-06 calendar used throughout:
    Mon 2  Tue 3  Wed 4  Thu 5  Fri 6  Sat 7  Sun 8  Mon 9 ...
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.services.business_days import business_days_between


MON = datetime(2025, 6, 2, 9, 0)
TUE = datetime(2025, 6, 3, 9, 0)
WED = datetime(2025, 6, 4, 9, 0)
FRI = datetime(2025, 6, 6, 9, 0)
SAT = datetime(2025, 6, 7, 9, 0)
SUN = datetime(2025, 6, 8, 9, 0)
NEXT_MON = datetime(2025, 6, 9, 9, 0)
NEXT_TUE = datetime(2025, 6, 10, 9, 0)


class TestSameDayAndOrdering:
    def test_same_instant_is_zero(self):
        assert business_days_between(MON, MON) == 0

    def test_same_day_different_times_is_zero(self):
        assert business_days_between(MON, MON.replace(hour=23, minute=59)) == 0

    def test_end_before_start_clamps_to_zero(self):
        assert business_days_between(NEXT_MON, MON) == 0
        assert business_days_between(TUE, MON) == 0

    def test_time_of_day_is_ignored(self):
        # 23:59 Mon -> 00:01 Tue is still one (business) date step.
        late_mon = MON.replace(hour=23, minute=59)
        early_tue = TUE.replace(hour=0, minute=1)
        assert business_days_between(late_mon, early_tue) == 1


class TestWeekdaySpans:
    def test_consecutive_weekdays(self):
        assert business_days_between(MON, TUE) == 1
        assert business_days_between(TUE, WED) == 1

    def test_monday_to_friday(self):
        assert business_days_between(MON, FRI) == 4

    def test_full_week_monday_to_monday_is_five(self):
        assert business_days_between(MON, NEXT_MON) == 5

    def test_wednesday_to_next_wednesday_is_five(self):
        assert business_days_between(WED, WED + timedelta(days=7)) == 5


class TestWeekendSpans:
    def test_friday_to_saturday_counts_friday(self):
        assert business_days_between(FRI, SAT) == 1

    def test_friday_to_monday_is_one(self):
        # Calendar says 3 days; only Friday is a business day in [Fri, Mon).
        assert business_days_between(FRI, NEXT_MON) == 1

    def test_friday_to_next_tuesday_is_two(self):
        assert business_days_between(FRI, NEXT_TUE) == 2

    def test_seven_calendar_days_over_weekend_is_five_business(self):
        # The exact bug: a 7-business-day threshold fired at 7 CALENDAR days.
        assert (NEXT_MON - MON).days == 7
        assert business_days_between(MON, NEXT_MON) == 5


class TestStartOnWeekend:
    def test_saturday_to_sunday_is_zero(self):
        assert business_days_between(SAT, SUN) == 0

    def test_saturday_to_monday_is_zero(self):
        assert business_days_between(SAT, NEXT_MON) == 0

    def test_sunday_to_monday_is_zero(self):
        assert business_days_between(SUN, NEXT_MON) == 0

    def test_saturday_to_next_tuesday_counts_only_monday(self):
        assert business_days_between(SAT, NEXT_TUE) == 1


class TestLongSpans:
    def test_two_full_weeks(self):
        assert business_days_between(MON, MON + timedelta(days=14)) == 10

    def test_thirty_calendar_days_from_monday(self):
        # Jun 2 -> Jul 2 2025: 30 calendar days = 4 full weeks (20) + Mon/Tue (2).
        assert business_days_between(MON, MON + timedelta(days=30)) == 22

    def test_ninety_days_matches_bruteforce(self):
        start = datetime(2025, 1, 1, 12, 0)  # Wednesday
        for span in (1, 5, 13, 30, 60, 90):
            end = start + timedelta(days=span)
            brute = sum(
                1
                for offset in range((end.date() - start.date()).days)
                if (start.date() + timedelta(days=offset)).weekday() < 5
            )
            assert business_days_between(start, end) == brute, f"span={span}"


class TestDateInputs:
    def test_accepts_plain_dates(self):
        assert business_days_between(date(2025, 6, 2), date(2025, 6, 9)) == 5

    def test_mixed_datetime_and_date(self):
        assert business_days_between(MON, date(2025, 6, 9)) == 5
        assert business_days_between(date(2025, 6, 6), NEXT_MON) == 1


class TestThresholdSemantics:
    """The consumers use ``dwell > threshold`` (strictly greater)."""

    @pytest.mark.parametrize(
        ("start", "end", "threshold", "stalled"),
        [
            (MON, NEXT_MON, 5, False),                       # exactly at threshold -> not stalled
            (MON, NEXT_TUE, 5, True),                        # one business day over
            (FRI, NEXT_MON, 1, False),                       # weekend span, at threshold
            (MON, MON + timedelta(days=9), 7, False),        # 9 calendar days = 7 business
            (MON, MON + timedelta(days=11), 7, True),        # 11 calendar days = 9 business
        ],
    )
    def test_strictly_greater(self, start, end, threshold, stalled):
        assert (business_days_between(start, end) > threshold) is stalled
