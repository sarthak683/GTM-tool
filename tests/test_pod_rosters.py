from app.core.pods import pod_rep_emails
from app.services.us_pod_call_report import INDIA_POD_REPS, US_POD_REPS


EXPECTED_US_REPS = {
    "awinja@beacon.li",
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
    assert set(pod_rep_emails("us")) == EXPECTED_US_REPS
    assert {rep["email"] for rep in US_POD_REPS} == EXPECTED_US_REPS


def test_india_analytics_and_call_report_rosters_match():
    assert set(pod_rep_emails("india")) == EXPECTED_INDIA_REPS
    assert {rep["email"] for rep in INDIA_POD_REPS} == EXPECTED_INDIA_REPS
