"""Pure-logic tests for ``_resolve_analytics_window`` (Fix 7).

No database, Redis, or app lifespan is touched: the function under test is a
small, deterministic date helper. We assert that

  * malformed ``from_date``/``to_date`` raise HTTP 422 instead of bubbling a
    raw ``ValueError`` (which previously 500'd /sales-dashboard and
    /sales-activity-drilldown), and
  * valid / omitted inputs keep their existing semantics (end is exclusive,
    i.e. ``to_date`` + 1 day; default window is ``window_days`` back from now).
"""
import unittest
from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.api.v1.endpoints.analytics import _resolve_analytics_window, _rolling_period_starts


class ResolveAnalyticsWindowTests(unittest.TestCase):
    def test_malformed_from_date_raises_422(self) -> None:
        for bad in ("not-a-date", "2026-13-01", "01-01-2026", "yesterday"):
            with self.assertRaises(HTTPException) as ctx:
                _resolve_analytics_window(90, bad, None)
            self.assertEqual(ctx.exception.status_code, 422)
            self.assertIn("ISO 8601", ctx.exception.detail)

    def test_malformed_to_date_raises_422(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _resolve_analytics_window(90, None, "garbage")
        self.assertEqual(ctx.exception.status_code, 422)

    def test_valid_explicit_window_uses_exclusive_end(self) -> None:
        start, end = _resolve_analytics_window(90, "2026-01-01", "2026-01-31")
        self.assertEqual(start, datetime(2026, 1, 1))
        # End is exclusive: to_date + 1 day so the whole 31st is included.
        self.assertEqual(end, datetime(2026, 2, 1))

    def test_default_window_is_midnight_aligned(self) -> None:
        # _utcnow() is naive UTC, so this compares cleanly against utcnow().
        before = datetime.utcnow()
        start, end = _resolve_analytics_window(7, None, None)
        after = datetime.utcnow()
        # window_end is "now" (to-date semantics, partial current day included).
        self.assertGreaterEqual(end, before)
        self.assertLessEqual(end, after)
        # window_start is MIDNIGHT UTC of (now - window_days): the quick "Last
        # 7 days" pick must cover the same span as an explicit 7-day from_date
        # range, which parses to midnight. The old rolling-instant boundary
        # made the two disagree by the time elapsed since midnight.
        self.assertEqual(start.time().isoformat(), "00:00:00")
        self.assertEqual(start.date(), (before - timedelta(days=7)).date())

    def test_rolling_period_starts_returns_daily_buckets_for_1_week_window(self) -> None:
        start = datetime(2026, 1, 1, 10, 30)
        end = datetime(2026, 1, 8, 9, 0)
        periods = _rolling_period_starts(start, end, daily=True, bucket_end_date=date(2026, 1, 8))
        self.assertEqual([period.isoformat() for period in periods], [
            "2026-01-01",
            "2026-01-02",
            "2026-01-03",
            "2026-01-04",
            "2026-01-05",
            "2026-01-06",
            "2026-01-07",
            "2026-01-08",
        ])


if __name__ == "__main__":
    unittest.main()
