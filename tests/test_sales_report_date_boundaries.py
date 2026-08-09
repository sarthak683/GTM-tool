from datetime import date, datetime, timezone

from app.models.activity import Activity
from app.services.us_pod_call_report import (
    _activity_report_date,
    _utc_bounds_for_report_day,
    default_report_date,
    weekly_report_period,
)


US_SETTINGS = {
    "cutoff_timezone": "Asia/Kolkata",
    "cutoff_hour": 7,
    "cutoff_minute": 0,
    "report_label_timezone": "America/Chicago",
}

INDIA_SETTINGS = {
    "cutoff_timezone": "Asia/Kolkata",
    "cutoff_hour": 0,
    "cutoff_minute": 0,
    "report_label_timezone": "Asia/Kolkata",
}


def test_us_report_date_and_bounds_match_completed_chicago_business_day():
    now = datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc)

    assert default_report_date(now, US_SETTINGS) == date(2026, 8, 5)
    assert _utc_bounds_for_report_day(date(2026, 8, 5), US_SETTINGS) == (
        datetime(2026, 8, 5, 1, 30),
        datetime(2026, 8, 6, 1, 30),
    )


def test_india_report_date_and_bounds_cover_previous_completed_ist_day():
    now = datetime(2026, 8, 6, 3, 30, tzinfo=timezone.utc)

    assert default_report_date(now, INDIA_SETTINGS) == date(2026, 8, 5)
    assert _utc_bounds_for_report_day(date(2026, 8, 5), INDIA_SETTINGS) == (
        datetime(2026, 8, 4, 18, 30),
        datetime(2026, 8, 5, 18, 30),
    )


def test_india_activity_is_labelled_with_its_ist_business_day():
    activity = Activity(
        type="call",
        source="manual",
        created_at=datetime(2026, 8, 5, 4, 30),
    )

    assert _activity_report_date(activity, INDIA_SETTINGS) == date(2026, 8, 5)


def test_india_weekly_report_ends_on_friday_for_saturday_send():
    now = datetime(2026, 8, 8, 3, 30, tzinfo=timezone.utc)
    report_date = default_report_date(now, INDIA_SETTINGS)

    assert report_date == date(2026, 8, 7)
    assert weekly_report_period(report_date, INDIA_SETTINGS) == (
        date(2026, 8, 3),
        date(2026, 8, 7),
    )
