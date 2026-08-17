"""
Business-day (Mon-Fri) date math shared by every stuck/stalled-deal surface.

The stuck-deal thresholds in analytics settings are documented as BUSINESS
days (see app.core.analytics_defaults.DEFAULT_STUCK_THRESHOLDS_DAYS), but the
dwell computations historically counted calendar days — over a weekend a
7-day threshold fired ~40% early. Every consumer (performance_metrics
stuck_deals, the board's is_stalled flag) must use THIS helper so the
scorecard and the pipeline board can never disagree about what "stalled"
means again.
"""
from __future__ import annotations

from datetime import date, datetime


def business_days_between(start: datetime | date, end: datetime | date) -> int:
    """Count business days (Mon-Fri) in the half-open date range [start, end).

    Time-of-day is ignored: the count is over calendar DATES, counting each
    weekday from ``start``'s date up to but not including ``end``'s date.
    Consequences worth spelling out:

    - same calendar day -> 0 (regardless of hours elapsed)
    - Monday -> next Monday -> 5 (the weekend doesn't count)
    - Friday -> Monday -> 1 (only Friday counts)
    - start on Saturday/Sunday -> weekend days contribute 0
    - ``end`` before ``start`` -> 0 (clamped, never negative)
    """
    start_date = start.date() if isinstance(start, datetime) else start
    end_date = end.date() if isinstance(end, datetime) else end
    if end_date <= start_date:
        return 0

    total_days = (end_date - start_date).days
    full_weeks, remainder = divmod(total_days, 7)
    count = full_weeks * 5
    start_weekday = start_date.weekday()
    for offset in range(remainder):
        if (start_weekday + offset) % 7 < 5:  # Mon-Fri
            count += 1
    return count
