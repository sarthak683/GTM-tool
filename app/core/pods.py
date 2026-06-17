"""Pod (sales team) rosters — single source of truth for pod membership.

Consumed by:
  - the daily pod call/forecast reports (app/services/us_pod_call_report.py),
  - the in-app pod-scoped analytics view (frontend pod selector via the
    `/performance/pods` endpoint).

Membership is declared by email (stable across display-name edits) and resolved
to user ids at query time. `ae_emails` is the subset whose deal pipeline the
forecast section/dashboard should reflect — for an SDR-heavy pod that's the same
as the reps; for the India pod it's just the AEs (Yashveer, Sandeep, Bhavya).
"""
from __future__ import annotations

from typing import Any

POD_DEFINITIONS: dict[str, dict[str, Any]] = {
    "us": {
        "key": "us",
        "label": "US Pod",
        "reps": [
            {"name": "Pravalika Jamalpur", "email": "pravalika@beacon.li", "aliases": ["pravalika"]},
            {"name": "Mahesh Pothula", "email": "mahesh@beacon.li", "aliases": ["mahesh"]},
            {"name": "Pulkit Anand", "email": "pulkit@beacon.li", "aliases": ["pulkit"]},
        ],
        "ae_emails": ["pravalika@beacon.li", "mahesh@beacon.li", "pulkit@beacon.li"],
    },
    "india": {
        "key": "india",
        "label": "India Pod",
        "reps": [
            {"name": "Annie Gupta", "email": "annie@beacon.li", "aliases": ["annie"]},
            {"name": "Dyuthith Din", "email": "dyuthith@beacon.li", "aliases": ["dyuthith"]},
            {"name": "Yashveer Singh", "email": "yash@beacon.li", "aliases": ["yashveer", "yash"]},
            {"name": "Bhavya Mukkera", "email": "bhavya@beacon.li", "aliases": ["bhavya"]},
            {"name": "Sandeep Sinha", "email": "sandeep@beacon.li", "aliases": ["sandeep"]},
            {"name": "Sipra Sonali Palta", "email": "sipra@beacon.li", "aliases": ["sipra"]},
        ],
        "ae_emails": ["yash@beacon.li", "bhavya@beacon.li", "sandeep@beacon.li"],
    },
}


def pod_keys() -> list[str]:
    return list(POD_DEFINITIONS.keys())


def get_pod(pod_key: str) -> dict[str, Any] | None:
    return POD_DEFINITIONS.get((pod_key or "").strip().lower())


def pod_rep_emails(pod_key: str) -> list[str]:
    pod = get_pod(pod_key)
    return [r["email"].lower() for r in pod["reps"]] if pod else []


def pod_ae_emails(pod_key: str) -> list[str]:
    pod = get_pod(pod_key)
    return [e.lower() for e in pod.get("ae_emails", [])] if pod else []
