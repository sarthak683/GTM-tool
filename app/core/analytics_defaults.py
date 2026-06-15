"""
Default configuration for Performance Analytics.

These defaults are copied into `workspace_settings.analytics_settings` the
first time the settings row is loaded (same lazy-create pattern the rest of
workspace_settings uses). Admins edit them through the settings UI; the
metric engine always reads the live value, not these constants.

Numbers derived from the GTM analytics spec (core metric dictionary +
weekly scorecard targets + stuck-deal thresholds + stage probabilities).
OCR-lossy values in the source PDF have been resolved to sensible round
numbers — admins can override in-app without a code change.
"""
from __future__ import annotations


# Per-role weekly targets used to compute the RAG badge on the scorecard.
# Keys: role id (matches User.role). Metrics: any id the engine supports.
DEFAULT_WEEKLY_TARGETS: dict[str, dict[str, int]] = {
    "sdr": {
        "calls_connected": 200,
        "emails_sent": 150,
        "demos_booked": 5,
    },
    "ae": {
        "calls_connected": 200,
        "emails_sent": 150,
        "demos_booked": 5,
        "demos_done": 3,
        "pocs_procured": 1,
    },
}

DEFAULT_MONTHLY_TARGETS: dict[str, dict[str, int]] = {
    "sdr": {"calls_connected": 800, "emails_sent": 600, "demos_booked": 20},
    "ae": {
        "calls_connected": 800,
        "emails_sent": 600,
        "demos_booked": 20,
        "demos_done": 12,
        "pocs_procured": 4,
    },
}


# RAG bands are percentages of target.  Green >= green_min, Amber in
# [amber_min, green_min), Red < amber_min.  A single band applies to every
# metric by default; per-metric overrides can live in analytics_settings.
DEFAULT_RAG_BANDS: dict[str, float] = {
    "green_min": 1.0,    # >=100% of target
    "amber_min": 0.70,   # 70% to 99% of target
    # below amber_min → red
}


# Stuck-deal dwell thresholds (business days). A deal in the given stage for
# longer than the threshold is flagged on the Deal Health dashboard.
DEFAULT_STUCK_THRESHOLDS_DAYS: dict[str, int] = {
    "demo_done": 7,
    "qualified_lead": 21,
    "poc_agreed": 10,
    "poc_wip": 14,
    "poc_done": 14,
    "commercial_negotiation": 21,
    "workshop": 21,
    "msa_review": 21,
}


# Default stage probability used for weighted-pipeline math. AE categories
# (Commit / Best / Worst) override the *category* but not the probability —
# that keeps the weighted number honest.
DEFAULT_STAGE_PROBABILITIES: dict[str, float] = {
    "reprospect": 0.00,
    "demo_scheduled": 0.00,
    "demo_done": 0.15,
    "qualified_lead": 0.20,
    "poc_agreed": 0.25,
    "poc_wip": 0.30,
    "poc_done": 0.33,
    "commercial_negotiation": 0.60,
    "workshop": 0.80,
    "msa_review": 0.80,
    "closed_won": 1.00,
}


# Stage-conversion grid shown in the funnel dashboard. Ordered list of
# (from_stage, to_stage) pairs to report on.
DEFAULT_CONVERSION_TRANSITIONS: list[dict[str, str]] = [
    {"from": "reprospect", "to": "demo_scheduled"},
    {"from": "demo_scheduled", "to": "demo_done"},
    {"from": "demo_done", "to": "qualified_lead"},
    {"from": "qualified_lead", "to": "poc_agreed"},
    {"from": "poc_agreed", "to": "poc_wip"},
    {"from": "poc_wip", "to": "poc_done"},
    {"from": "poc_done", "to": "commercial_negotiation"},
    {"from": "commercial_negotiation", "to": "msa_review"},
    {"from": "msa_review", "to": "closed_won"},
]


# Stages that count as "outcomes" for the scorecard.
OUTCOME_STAGES: dict[str, str] = {
    "demo_scheduled": "demos_booked",
    "demo_done": "demos_done",
    "qualified_lead": "qualified_leads",
    "poc_agreed": "pocs_procured",
    "poc_wip": "pocs_wip",
    "poc_done": "pocs_done",
    "closed_won": "closed_won",
    "closed_lost": "closed_lost",
    "not_a_fit": "disqualified",
}


def build_default_analytics_settings() -> dict:
    """Compose the full default analytics_settings JSON blob."""
    return {
        "weekly_targets": DEFAULT_WEEKLY_TARGETS,
        "monthly_targets": DEFAULT_MONTHLY_TARGETS,
        "rag_bands": DEFAULT_RAG_BANDS,
        "stuck_thresholds_days": DEFAULT_STUCK_THRESHOLDS_DAYS,
        "stage_probabilities": DEFAULT_STAGE_PROBABILITIES,
        "conversion_transitions": DEFAULT_CONVERSION_TRANSITIONS,
        "workspace_timezone": "UTC",
        "email_reply_lookback_days": 30,
    }
