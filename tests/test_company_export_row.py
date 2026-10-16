from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from app.api.v1.endpoints.account_sourcing import _company_export_row
from app.models.company import Company
from app.models.user import User


def _company(**kw):
    base = dict(id=uuid4(), name="Acme", domain="acme.com", created_at=datetime(2026, 10, 7, 10, 46, 42), updated_at=datetime(2026, 10, 14, 13, 18))
    base.update(kw)
    return Company(**base)


def _row(company, users=None, stages=None, mql=False):
    return _company_export_row(company, users_by_id=users or {}, deal_stages=stages or [], is_mql=mql)


def test_columns_are_the_short_trusted_set():
    row = _row(_company())
    assert list(row) == [
        "company_id", "name", "domain", "industry", "employee_count", "funding_stage",
        "arr_estimate_usd", "icp_score", "icp_tier", "account_status", "deal_stage", "mql", "events",
        "ae_name", "ae_email", "sdr_name", "sdr_email", "outreach_status", "disposition",
        "rep_feedback", "last_outreach_utc", "instantly_campaign_id", "created_utc", "updated_utc", "enriched_utc",
    ]
    assert not any(k.startswith(("researched_", "uploaded_", "source_")) for k in row)


def test_placeholder_domain_is_blank_and_real_domain_kept():
    assert _row(_company(domain="paradigm.unknown"))["domain"] == ""
    assert _row(_company(domain="syndigo.com"))["domain"] == "syndigo.com"


def test_numbers_are_plain_and_zero_is_kept():
    row = _row(_company(arr_estimate=Decimal("800000000.00"), icp_score=0, employee_count=2800))
    assert row["arr_estimate_usd"] == "800000000"
    assert row["icp_score"] == "0"
    assert row["employee_count"] == "2800"
    assert _row(_company(arr_estimate=None, icp_score=None))["arr_estimate_usd"] == ""
    assert _row(_company(icp_score=None))["icp_score"] == ""


def test_status_events_mql_and_stage():
    row = _row(
        _company(account_status="meeting_booked", events=["TSI Event", "CS Summit, London"]),
        stages=["Marketing Lead (MQL)", "Demo Scheduled"], mql=True,
    )
    assert row["account_status"] == "Meeting Booked"
    assert row["events"] == "TSI Event; CS Summit, London"
    assert row["deal_stage"] == "Marketing Lead (MQL); Demo Scheduled"
    assert row["mql"] == "Yes"
    assert _row(_company())["mql"] == "No"


def test_owner_comes_from_user_row_with_denormalised_fallback():
    ae, sdr = User(id=uuid4(), name="Pravalika Jamalpur", email="pravalika@beacon.li"), User(id=uuid4(), name="Mahesh Pothula", email="mahesh@beacon.li")
    c = _company(assigned_to_id=ae.id, sdr_id=sdr.id, assigned_rep_name="Old Name", sdr_name="Old SDR")
    row = _row(c, users={ae.id: ae, sdr.id: sdr})
    assert (row["ae_name"], row["ae_email"], row["sdr_name"], row["sdr_email"]) == (
        "Pravalika Jamalpur", "pravalika@beacon.li", "Mahesh Pothula", "mahesh@beacon.li")
    stale = _row(_company(assigned_rep_name="Fallback AE", assigned_rep_email="a@beacon.li", sdr_name="Fallback SDR"))
    assert (stale["ae_name"], stale["ae_email"], stale["sdr_name"]) == ("Fallback AE", "a@beacon.li", "Fallback SDR")


def test_dates_are_utc_minutes():
    row = _row(_company(last_outreach_at=datetime(2026, 10, 1, 9, 5, 30)))
    assert row["created_utc"] == "2026-10-07 10:46"
    assert row["last_outreach_utc"] == "2026-10-01 09:05"
    assert row["enriched_utc"] == ""
