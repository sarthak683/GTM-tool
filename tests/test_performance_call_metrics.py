from sqlalchemy.dialects import postgresql

from app.services.performance_metrics import (
    _connected_call_filter,
    _distinct_call_day_key,
)


def _compiled(expression) -> str:
    return str(
        expression.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


def test_connected_call_filter_supports_current_and_legacy_manual_outcomes():
    sql = _compiled(_connected_call_filter())

    assert "connected" in sql
    assert "answered" in sql
    assert "demo scheduled/booked%" in sql
    assert "call back later/rescheduled%" in sql


def test_call_metric_dedupe_key_includes_rep_contact_and_day():
    sql = _compiled(_distinct_call_day_key())

    assert "activities.created_by_id" in sql
    assert "activities.contact_id" in sql
    assert "date(activities.created_at)" in sql
