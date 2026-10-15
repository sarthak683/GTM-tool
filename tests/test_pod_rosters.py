from app.core.pods import pod_rep_emails
from app.services.us_pod_call_report import (
    INDIA_POD_REPS,
    US_POD_REPORT_EXCLUDED_EMAILS,
    US_POD_REPS,
)


EXPECTED_US_REPS = {
    "jacob@beacon.li",
    "pravalika@beacon.li",
    "mahesh@beacon.li",
    "pulkit@beacon.li",
}

EXPECTED_INDIA_REPS = {
    "dyuthith@beacon.li",
    "yash@beacon.li",
    "bhavya@beacon.li",
    "sandeep@beacon.li",
    "sipra@beacon.li",
}


def test_us_analytics_and_call_report_rosters_match():
    # Analytics keeps the full pod; the call report drops only the explicitly
    # excluded members (Pulkit — an AE, not a caller).
    assert set(pod_rep_emails("us")) == EXPECTED_US_REPS
    assert US_POD_REPORT_EXCLUDED_EMAILS == {"pulkit@beacon.li"}
    assert {rep["email"] for rep in US_POD_REPS} == EXPECTED_US_REPS - US_POD_REPORT_EXCLUDED_EMAILS


def test_india_analytics_and_call_report_rosters_match():
    assert set(pod_rep_emails("india")) == EXPECTED_INDIA_REPS
    assert {rep["email"] for rep in INDIA_POD_REPS} == EXPECTED_INDIA_REPS
