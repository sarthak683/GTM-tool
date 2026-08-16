"""Pure-logic tests for the shared metric dictionary + workspace timezone.

These encode the contracts BOTH metric engines rely on: meeting dedupe /
happened-inference, and timezone-aware window boundaries. No DB required.
"""
import unittest
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.services.metric_definitions import (
    dedupe_meetings_across_sources,
    local_midnight_utc,
    meeting_happened,
    meeting_rep_ids,
    workspace_today,
    workspace_zoneinfo,
)
from app.services.performance_metrics import resolve_period


@dataclass
class FakeMeeting:
    id: UUID = field(default_factory=uuid4)
    scheduled_at: datetime | None = None
    status: str | None = None
    company_id: UUID | None = None
    deal_id: UUID | None = None
    external_source: str | None = None
    owner_user_id: UUID | None = None
    attendees: list | None = None


class MeetingHappenedTests(unittest.TestCase):
    def test_explicit_held_status_wins_even_in_future(self) -> None:
        m = FakeMeeting(scheduled_at=datetime.utcnow() + timedelta(days=1), status="held")
        self.assertTrue(meeting_happened(m))

    def test_cancelled_never_happened(self) -> None:
        m = FakeMeeting(scheduled_at=datetime.utcnow() - timedelta(days=1), status="cancelled")
        self.assertFalse(meeting_happened(m))

    def test_past_non_cancelled_is_inferred_done(self) -> None:
        # Reps rarely flip statuses by hand — a past, non-cancelled meeting
        # counts. This inference is what the dashboard always used; the
        # scorecard requiring explicit statuses was the divergence.
        m = FakeMeeting(scheduled_at=datetime.utcnow() - timedelta(hours=2), status="scheduled")
        self.assertTrue(meeting_happened(m))

    def test_future_meeting_not_done_yet(self) -> None:
        m = FakeMeeting(scheduled_at=datetime.utcnow() + timedelta(hours=2), status="scheduled")
        self.assertFalse(meeting_happened(m))


class MeetingDedupeTests(unittest.TestCase):
    def test_tldv_and_calendar_twin_count_once_tldv_wins(self) -> None:
        company = uuid4()
        t = datetime(2026, 8, 10, 9, 30)
        cal = FakeMeeting(company_id=company, scheduled_at=t, external_source="google_calendar")
        tldv = FakeMeeting(company_id=company, scheduled_at=t + timedelta(minutes=2), external_source="tldv")
        kept = dedupe_meetings_across_sources([cal, tldv])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].external_source, "tldv")

    def test_different_companies_never_merge(self) -> None:
        t = datetime(2026, 8, 10, 9, 30)
        a = FakeMeeting(company_id=uuid4(), scheduled_at=t, external_source="tldv")
        b = FakeMeeting(company_id=uuid4(), scheduled_at=t, external_source="google_calendar")
        self.assertEqual(len(dedupe_meetings_across_sources([a, b])), 2)

    def test_same_company_far_apart_stays_two_meetings(self) -> None:
        company = uuid4()
        a = FakeMeeting(company_id=company, scheduled_at=datetime(2026, 8, 10, 9, 0), external_source="tldv")
        b = FakeMeeting(company_id=company, scheduled_at=datetime(2026, 8, 10, 15, 0), external_source="tldv")
        self.assertEqual(len(dedupe_meetings_across_sources([a, b])), 2)


class MeetingAttributionTests(unittest.TestCase):
    def test_owner_then_attendees_no_duplicates(self) -> None:
        rep = uuid4()
        m = FakeMeeting(
            owner_user_id=rep,
            attendees=[{"email": "rep@beacon.li"}, {"email": "prospect@acme.com"}],
        )
        ids = meeting_rep_ids(m, deal_owner={}, user_ids_by_email={"rep@beacon.li": rep})
        self.assertEqual(ids, [rep])

    def test_unowned_unattended_is_unassigned_bucket(self) -> None:
        m = FakeMeeting()
        self.assertEqual(meeting_rep_ids(m, deal_owner={}, user_ids_by_email={}), [None])


class WorkspaceTimezoneTests(unittest.TestCase):
    def test_bad_zone_falls_back_to_utc(self) -> None:
        self.assertEqual(str(workspace_zoneinfo({"workspace_timezone": "Not/AZone"})), "UTC")
        self.assertEqual(str(workspace_zoneinfo(None)), "UTC")

    def test_ist_midnight_is_1830_utc_previous_day(self) -> None:
        ist = ZoneInfo("Asia/Kolkata")
        self.assertEqual(
            local_midnight_utc(date(2026, 8, 16), ist),
            datetime(2026, 8, 15, 18, 30),
        )

    def test_workspace_today_flips_at_local_midnight(self) -> None:
        ist = ZoneInfo("Asia/Kolkata")
        # 19:30 UTC on the 15th is 01:00 IST on the 16th — the workspace day
        # has already turned even though UTC hasn't.
        self.assertEqual(workspace_today(ist, datetime(2026, 8, 15, 19, 30)), date(2026, 8, 16))

    def test_resolve_period_month_uses_local_midnights(self) -> None:
        p = resolve_period("month", anchor=date(2026, 8, 10), tz_name="Asia/Kolkata")
        self.assertEqual(p.start, datetime(2026, 7, 31, 18, 30))  # Aug 1 00:00 IST
        self.assertEqual(p.end, datetime(2026, 8, 31, 18, 30))    # Sep 1 00:00 IST
        self.assertEqual(p.label, "August 2026")

    def test_resolve_period_bad_tz_name_falls_back_to_offset_zero(self) -> None:
        p = resolve_period("month", anchor=date(2026, 8, 10), tz_name="Nope/Nope")
        self.assertEqual(p.start, datetime(2026, 8, 1, 0, 0))


if __name__ == "__main__":
    unittest.main()
