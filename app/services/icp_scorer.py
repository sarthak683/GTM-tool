"""
ICP (Ideal Customer Profile) scoring engine.

Scores companies 0-100 across firmographic and uploaded-analyst dimensions and
maps to tiers:
  hot (75+) | warm (50-74) | monitor (25-49) | cold (0-24)
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.company import Company


_IN_SCOPE_CATEGORY_KEYWORDS = [
    "erp",
    "hris",
    "payroll",
    "insuretech",
    "policy",
    "claims",
    "billing",
    "finance",
    "ap",
    "ar",
    "rcm",
    "monetization",
    "field service",
    "healthtech",
    "ehr",
    "emr",
    "construction",
    "tax",
    "plm",
    "cpq",
    "procurement",
    "s2p",
    "legal ops",
    "ediscovery",
    "customer success",
]

_COMPLEXITY_KEYWORDS = [
    "workflow",
    "rule",
    "approval",
    "multi",
    "rollout",
    "deployment",
    "configuration",
    "implementation",
    "partner",
    "sandbox",
    "integration",
    "migration",
    "global",
    "template",
]


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _get_import_profile(company: "Company") -> dict[str, Any]:
    sources = getattr(company, "enrichment_sources", None)
    if not isinstance(sources, dict):
        return {}
    import_block = sources.get("import")
    if not isinstance(import_block, dict):
        return {}
    return import_block


def _score_firmographic(company: "Company") -> int:
    score = 0

    emp = company.employee_count or 0
    if 201 <= emp <= 1000:
        score += 30
    elif 1001 <= emp <= 5000:
        score += 28
    elif emp > 5000:
        score += 24
    elif 51 <= emp <= 200:
        score += 22
    elif 11 <= emp <= 50:
        score += 10

    funding = _normalize_text(company.funding_stage)
    late_series = re.search(r"series\s+([b-z])", funding)
    if late_series or any(s in funding for s in ["growth", "ipo", "public", "late stage", "private equity"]):
        score += 25
    elif "series a" in funding:
        score += 18
    elif any(s in funding for s in ["seed", "angel", "pre-seed"]):
        score += 10
    elif funding:
        score += 4

    if company.has_dap:
        score += 20

    industry = _normalize_text(company.industry)
    vertical = _normalize_text(company.vertical)
    combined = f"{industry} {vertical}"
    icp_keywords = [
        "hr", "human resource", "hcm", "people", "talent",
        "fintech", "finance", "banking", "insurance", "payment",
        "healthtech", "health", "medical", "pharma",
        "saas", "enterprise software", "software", "cloud",
        "payroll", "recruitment", "staffing",
        "technology", "information technology", "it services",
        "proptech", "legaltech", "procurement", "customer success",
    ]
    if any(kw in combined for kw in icp_keywords):
        score += 15
    elif industry or vertical:
        score += 5

    tech = company.tech_stack
    if isinstance(tech, dict) and tech:
        score += 10
    elif isinstance(tech, list) and tech:
        score += 10

    return min(score, 100)


def _score_uploaded_profile(company: "Company") -> int | None:
    import_profile = _get_import_profile(company)
    analyst = import_profile.get("analyst")
    if not isinstance(analyst, dict) or not analyst:
        return None

    score = 0
    category = _normalize_text(analyst.get("category"))
    core_focus = _normalize_text(analyst.get("core_focus"))
    fit_type = _normalize_text(analyst.get("fit_type"))
    revenue_funding = _normalize_text(analyst.get("revenue_funding"))
    classification = _normalize_text(analyst.get("classification"))
    confidence = _normalize_text(analyst.get("confidence"))

    # Product / domain fit (40)
    if any(keyword in category for keyword in _IN_SCOPE_CATEGORY_KEYWORDS):
        score += 28
    elif category:
        score += 14

    if any(keyword in core_focus for keyword in ["system of record", "source of truth", "sor", "master data", "workflow"]):
        score += 12
    elif core_focus:
        score += 6

    # Implementation complexity (20)
    if fit_type == "both":
        score += 16
    elif fit_type in {"complex implementation", "system-of-record", "system of record"}:
        score += 12
    elif fit_type:
        score += 7

    complexity_hits = sum(1 for keyword in _COMPLEXITY_KEYWORDS if keyword in core_focus)
    score += min(complexity_hits, 4)

    # Financial capacity (20)
    if any(keyword in revenue_funding for keyword in ["arr", "funding", "public", "revenue"]):
        score += 16
    elif company.arr_estimate or (company.employee_count or 0) >= 200:
        score += 10
    elif revenue_funding:
        score += 6

    if classification == "target":
        score += 4

    # Evidence quality (10)
    uploaded_signals = import_profile.get("uploaded_signals")
    positive_signals = uploaded_signals.get("positive", []) if isinstance(uploaded_signals, dict) else []
    if isinstance(positive_signals, list):
        score += min(len(positive_signals), 5)

    # Confidence / completeness (10)
    if confidence == "high":
        score += 10
    elif confidence == "medium":
        score += 7
    elif confidence == "low":
        score += 3

    completeness_fields = [
        analyst.get("category"),
        analyst.get("core_focus"),
        analyst.get("revenue_funding"),
        analyst.get("classification"),
        analyst.get("fit_type"),
    ]
    score += min(sum(1 for value in completeness_fields if value), 5)

    return min(score, 100)


def _get_analyst_icp_score(company: "Company") -> float | None:
    import_profile = _get_import_profile(company)
    analyst = import_profile.get("analyst")
    if not isinstance(analyst, dict):
        return None
    value = analyst.get("icp_fit_score")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= parsed <= 10:
        return parsed
    return None


def _score_with_uploaded_context(company: "Company", fallback_score: int) -> int:
    uploaded_score = _score_uploaded_profile(company)
    if uploaded_score is None:
        return fallback_score

    analyst_score = _get_analyst_icp_score(company)
    analyst = _get_import_profile(company).get("analyst")
    analyst_confidence = analyst.get("confidence") if isinstance(analyst, dict) else None
    confidence = _normalize_text(analyst_confidence)

    if analyst_score is None:
        return round((fallback_score * 0.45) + (uploaded_score * 0.55))

    analyst_score_100 = analyst_score * 10

    # When Claude gives a strong score (8+) with high confidence, trust it more
    classification = _normalize_text(analyst.get("classification") if isinstance(analyst, dict) else None)
    strong_signal = confidence == "high" and analyst_score >= 8 and classification == "target"

    if strong_signal:
        analyst_weight, uploaded_weight, fallback_weight = 0.65, 0.20, 0.15
    elif confidence == "high":
        analyst_weight, uploaded_weight, fallback_weight = 0.55, 0.30, 0.15
    elif confidence == "medium":
        analyst_weight, uploaded_weight, fallback_weight = 0.45, 0.35, 0.20
    else:
        analyst_weight, uploaded_weight, fallback_weight = 0.35, 0.40, 0.25

    return round(
        (analyst_score_100 * analyst_weight)
        + (uploaded_score * uploaded_weight)
        + (fallback_score * fallback_weight)
    )


def score_company(company: "Company") -> tuple[int, str]:
    """Return (score 0-100, tier string) for the given company."""
    fallback_score = _score_firmographic(company)
    score = min(_score_with_uploaded_context(company, fallback_score), 100)

    if score >= 75:
        tier = "hot"
    elif score >= 50:
        tier = "warm"
    elif score >= 25:
        tier = "monitor"
    else:
        tier = "cold"

    return score, tier
