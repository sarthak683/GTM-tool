"""
Account sourcing service — tiered enrichment orchestrator.

Pipeline per company:
  Tier 1 (free):  Website scraping + DuckDuckGo intent signals
  Tier 2 (paid):  Apollo org/enrich + people/search
  Tier 3 (paid):  Hunter.io domain search + email verification (fills Apollo gaps)
  AI tier:        Claude summarization + ICP scoring + persona classification

Credit conservation:
  - Always runs Tier 1 first (free)
  - Apollo single-record calls only (no bulk)
  - Hunter.io only for NEW contacts not already found by Apollo
  - Caches all API responses in company.enrichment_cache JSONB
  - Re-enrich always runs (user controls via timestamp visibility)
"""
from __future__ import annotations

import asyncio
import csv
import copy
import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import func, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.database import AsyncSessionLocal
from app.models.company import Company
from app.models.contact import Contact
from app.models.sourcing_batch import SourcingBatch
from app.services.icp_scorer import score_company
from app.services.log_safety import safe_error_message
from app.services.prospect_hygiene import is_valid_prospect_candidate
from app.services.account_sourcing_tabular import (
    parse_csv,
    parse_prospect_upload_file,
    parse_prospect_xlsx,
    parse_tabular_file,
    parse_xlsx,
)

logger = logging.getLogger(__name__)


# ── CSV Parsing (reuses alias logic from prospecting) ─────────────────────────

_XLSX_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def _normalize_header(value: str) -> str:
    normalized = (value or "").strip().lower()
    replacements = {
        "&": " and ",
        "/": " ",
        "\\": " ",
        "+": " plus ",
        ">=": " ge ",
        "<=": " le ",
        ">": " gt ",
        "<": " lt ",
    }
    for needle, replacement in replacements.items():
        normalized = normalized.replace(needle, replacement)
    normalized = normalized.replace("≥", " ge ").replace("≤", " le ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"^(plus|minus)\s+", "", normalized)
    return normalized.strip()


_ALIASES_RAW: dict[str, list[str]] = {
    "name":           ["company name", "company", "organization", "accounts", "account", "comapnies", "companies", "name"],
    "domain":         ["domain", "domain name", "website", "url", "web"],
    "industry":       ["industry", "sector", "sector (pratice area & feed)",
                       "sector (practice area & feed)", "vertical", "category"],
    "employee_count": ["employee_count", "total employee count", "employees",
                       "headcount", "employee count", "no. of employees"],
    "funding_stage":  ["funding_stage", "company stage", "stage",
                       "funding stage", "round", "series"],
    "country":        ["country"],
    "city":           ["city", "location"],
    "description":    ["description", "overview", "about", "summary"],
    "total_funding":  ["total funding (usd)", "total funding",
                       "annual revenue (usd)", "annual revenue", "arr", "revenue"],
    "region":         ["region"],
    "headquarters":   ["headquarters", "hq", "headquarter"],
    "category_label": ["category"],
    "core_focus":     ["core sor / complex impl. focus", "core sor complex impl focus",
                       "core system of record / complex implementation focus"],
    "revenue_funding_label": ["revenue / funding", "revenue funding"],
    "classification": ["classification"],
    "analyst_icp_score": ["icp fit score (0-10)", "icp fit score 0 10", "icp fit score"],
    "analyst_intent_score": ["intent score (0-10)", "intent score 0 10", "intent score"],
    "fit_type":       ["fit type"],
    "confidence":     ["confidence"],
    "icp_why":        ["icp why"],
    "intent_why":     ["intent why"],
    "ps_impl_hiring": ["ps/impl hiring", "ps impl hiring"],
    "leadership_org_moves": ["leadership / org moves", "leadership org moves"],
    "pr_funding_expansion": ["pr / funding / expansion", "pr funding expansion"],
    "events_thought_leadership": ["events / thought leadership", "events thought leadership"],
    "reviews_case_studies": ["reviews / case studies", "reviews case studies"],
    "internal_ai_overlap": ["internal ai/agentic overlap", "internal ai agentic overlap"],
    "strategic_constraints": ["m and a / ipo / strategic constraints", "m a ipo strategic constraints", "m and a ipo strategic constraints"],
    "ps_cs_contraction": ["ps / cs contraction", "ps cs contraction"],
    "build_vs_buy_impl_auto": ["build vs buy for impl. auto", "build vs buy for impl auto"],
    "ai_acquisition_impl": ["ai acquisition for impl.", "ai acquisition for impl"],
    "final_qual":     ["final qual"],
    "sdr":            ["sdr", "sdr name", "sdr rep"],
    "ae":             ["ae", "ae name", "account executive",
                       "owner", "account owner", "assigned to", "rep", "sales rep"],
    "contact_name":   ["contact", "prospect name", "full name", "name"],
    "contact_first_name": ["first", "first name"],
    "contact_last_name": ["last", "last name"],
    "contact_title":  ["title", "job title", "job", "role"],
    "contact_email":  ["email", "work email"],
    "contact_phone":  [
        "direct mobile personal", "mobile", "phone", "phone number",
        "direct phone", "cell", "cell phone", "personal phone",
        "hq direct line", "direct line", "mobile phone",
    ],
    "contact_timezone": ["timezone", "time zone", "timezones", "time zones"],
    "linkedin_url":   ["linkedin", "linkedin url", "linkedin profile",
                       # Apollo / Sales Nav exports name the person column
                       # "Person Linkedin Url" (vs "Company Linkedin Url").
                       "person linkedin url", "person linkedin",
                       "linkedin profile url", "linkedin link",
                       "contact linkedin url", "contact linkedin"],
    "next_steps":     ["next steps", "recommended next step"],
    "ownership_stage": ["ownership stage"],
    "pe_investors":   ["pe investors"],
    "vc_growth_investors": ["vc growth investors", "vc / growth investors", "vc investors", "growth investors"],
    "strategic_investors": ["strategic other investors", "strategic / other investors", "strategic investors", "other investors", "investors"],
    "angel_1_name":   ["angel 1 name"],
    "angel_1_strength": ["angel 1 strength 1 5", "angel 1 strength", "connection strength 1 5"],
    "angel_1_path":   ["angel 1 connection path", "connection path"],
    "angel_1_why":    ["angel 1 why it works", "why it works"],
    "angel_2_name":   ["angel 2 name"],
    "angel_2_strength": ["angel 2 strength 1 5", "angel 2 strength", "connection strength 1 5 2"],
    "angel_2_path":   ["angel 2 connection path", "connection path 2"],
    "angel_2_why":    ["angel 2 why it works", "why it works 2"],
    "angel_3_name":   ["angel 3 name"],
    "angel_3_strength": ["angel 3 strength 1 5", "angel 3 strength", "connection strength 1 5 3"],
    "angel_3_path":   ["angel 3 connection path", "connection path 3"],
    "angel_3_why":    ["angel 3 why it works", "why it works 3"],
    "recommended_outreach_strategy": ["recommended outreach strategy"],
    "conversation_starter": ["conversation starter"],
    "what_they_do":   ["what they do"],
    "who_they_are":   ["who they are"],
    # Rich columns from the Beacon "The 100" workbook
    "tier":           ["tier", "account tier"],
    "email_confidence": ["email confidence"],
    "direct_mobile":  ["direct mobile personal", "direct mobile", "direct  mobile personal", "mobile phone"],
    "hq_direct_line": ["hq direct line"],
    "hq_switchboard": ["hq switchboard toll free", "hq switchboard", "hq switchboard  toll free"],
    "tenure_role":    ["tenure role"],
    "tenure_company": ["tenure company"],
    "contact_location": ["location"],
    "contact_icp_score": ["icp score"],
    "contact_intent_score": ["intent score"],
    "contact_source": ["source"],
    "apollo_id":      ["apollo id"],
    "career_arc":     ["career arc"],
    "linkedin_activity": ["linkedin activity posts", "linkedin activity"],
    "intent_signals": ["intent signals"],
    "primary_messaging_angle": ["primary messaging angle"],
    "personalization_hook": ["personalization hook"],
    "research_confidence": ["research confidence"],
    "outreach_priority": ["outreach priority"],
    "email1_subject": ["email 1 subject"],
    "email1_body":    ["email 1 body"],
    "email1_when":    ["email 1 when to use", "email 1  when to use"],
    "email2_subject": ["email 2 subject"],
    "email2_body":    ["email 2 body"],
    "email2_when":    ["email 2 when to use", "email 2  when to use"],
    "email3_subject": ["email 3 subject"],
    "email3_body":    ["email 3 body"],
    "email3_when":    ["email 3 when to use", "email 3  when to use"],
}

_ALIASES: dict[str, list[str]] = {
    field: [_normalize_header(alias) for alias in aliases]
    for field, aliases in _ALIASES_RAW.items()
}


def _find(row: dict, field: str) -> str:
    for alias in _ALIASES.get(field, [field]):
        for candidate in (alias, f"plus {alias}", f"minus {alias}"):
            val = str(row.get(candidate, "")).strip()
            if val:
                return val
    return ""


def _find_any_phone(row: dict) -> str:
    """Last-resort phone lookup: any column whose normalized header contains
    'phone'. A workbook with two "Phone" columns (direct + mobile — common in
    Apollo / Sales Nav exports) is de-collided by the parser to 'phone' and
    'phone 2'; 'phone 2' matches no fixed alias, so without this fallback a
    contact whose number sits only in the second column loses it on import."""
    for key, val in row.items():
        if "phone" in key and str(val).strip():
            return str(val).strip()
    return ""


def _find_person_linkedin(row: dict) -> str:
    """Last-resort person-LinkedIn lookup: any column whose normalized header
    contains 'linkedin' — excluding company pages and activity/posts columns —
    whose value actually looks like a linkedin.com link. Catches header
    variants the alias table doesn't know (e.g. a de-collided 'linkedin url 2')
    without ever grabbing 'Company Linkedin Url' or free-text activity notes."""
    for key, val in row.items():
        if "linkedin" not in key or "company" in key or "activit" in key:
            continue
        candidate = str(val).strip()
        if "linkedin." in candidate.lower():
            return candidate
    return ""


def _normalize_phone(value: Optional[str]) -> Optional[str]:
    """Strip a leading non-dialable prefix and drop non-numbers.

    Source sheets fuse a type tag into the number itself ("m+1 248-890-8149" =
    mobile, "d+1 …" = direct line). When that string is handed to a `tel:` URI
    the OS dialer T9-maps the leading letter to a digit (m->6, d->3), so the
    call goes to the wrong number. Keep a leading '+' and the digits; null out
    anything with fewer than 7 digits (e.g. "Not available (Apollo)")."""
    if not value:
        return None
    cleaned = re.sub(r"^[^0-9+]+", "", str(value).strip())
    if len(re.sub(r"[^0-9]", "", cleaned)) < 7:
        return None
    return cleaned


def _clean_domain(raw: str) -> str:
    raw = raw.strip().lower()
    if not raw:
        return ""
    if raw.startswith("http"):
        parsed = urlparse(raw)
        raw = parsed.netloc.lstrip("www.")
    raw = raw.lstrip("www.")
    return raw.split("/")[0]


def _slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", "", s.strip())
    return s or "unknown"


def _parse_employee_count(val: str) -> Optional[int]:
    if not val:
        return None
    base = val.split("(")[0]
    digits = re.sub(r"[^\d]", "", base)
    try:
        return int(digits) if digits else None
    except ValueError:
        return None


def _parse_number(val: str) -> Optional[float]:
    if not val:
        return None
    cleaned = re.sub(r"[,$\s]", "", val)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_score_0_10(val: str) -> Optional[float]:
    if not val:
        return None
    cleaned = re.sub(r"[^0-9.]", "", val)
    try:
        score = float(cleaned)
    except ValueError:
        return None
    if 0 <= score <= 10:
        return round(score, 1)
    return None


def _parse_int(val: str) -> Optional[int]:
    if not val:
        return None
    digits = re.sub(r"[^\d]", "", val)
    try:
        return int(digits) if digits else None
    except ValueError:
        return None


def _parse_strength_score(val: str) -> Optional[int]:
    if not val:
        return None
    cleaned = re.sub(r"[^0-9.]", "", val)
    try:
        score = float(cleaned)
    except ValueError:
        return None
    rounded = int(round(score))
    if 0 <= rounded <= 5:
        return rounded
    return None


def _clean_email(raw: str) -> str:
    value = (raw or "").strip().lower()
    if not value or "@" not in value:
        return ""
    return value


def _split_semistructured_list(value: str) -> list[str]:
    text = (value or "").strip()
    if not text:
        return []
    parts = re.split(r"(?:\r?\n|[|;•])+|\s-\s", text)
    cleaned = [part.strip(" \t\r\n-") for part in parts if part and part.strip(" \t\r\n-")]
    if cleaned:
        return cleaned
    return [text]


def _has_nonempty_text(value: str) -> bool:
    return bool((value or "").strip())


def _has_meaningful_text(value: str) -> bool:
    normalized = (value or "").strip().lower()
    return normalized not in {"", "-", "n/a", "na", "unknown"}


def _normalize_signal_text(value: str) -> str:
    return (value or "").strip().lower().rstrip(".")


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _has_signal_evidence(value: str) -> bool:
    normalized = _normalize_signal_text(value)
    return normalized not in {
        "",
        "-",
        "n/a",
        "na",
        "unknown",
        "none",
        "none observed",
        "not observed",
        "no",
        "no signal",
    }


def _has_positive_signal_evidence(key: str, value: str) -> bool:
    if not _has_signal_evidence(value):
        return False

    normalized = _normalize_signal_text(value)
    neutral_phrases = {
        "no major recent",
        "no major",
        "no significant",
        "no recent",
        "none observed",
        "limited specific",
        "limited implementation thought leadership",
        "relatively straightforward",
        "quick setup",
        "days to weeks",
        "stable senior leadership",
    }
    if _contains_any(normalized, neutral_phrases):
        complexity_overrides = {
            "multi-month",
            "multi month",
            "required",
            "complex",
            "integration",
            "rollout",
            "months",
            "implementation methodology",
            "delivery capacity",
        }
        if not _contains_any(normalized, complexity_overrides):
            return False

    key_specific_blockers = {
        "leadership_org_moves": {"no major", "no significant", "no recent"},
        "events_thought_leadership": {"limited", "conference participation"},
        "reviews_case_studies": {"quick setup", "days to weeks", "straightforward"},
    }
    blockers = key_specific_blockers.get(key, set())
    if blockers and _contains_any(normalized, blockers):
        positive_overrides = {
            "implementation methodology",
            "deployment",
            "rollout",
            "required",
            "complex",
            "integration",
            "months",
            "partner",
        }
        if not _contains_any(normalized, positive_overrides):
            return False

    return True


def _has_negative_signal_evidence(key: str, value: str) -> bool:
    if not _has_signal_evidence(value):
        return False

    normalized = _normalize_signal_text(value)
    neutral_phrases = {
        "no acquisition constraint",
        "no ipo constraint",
        "not a constraint",
        "none observed",
        "stable pe ownership",
        "stable ownership",
        "no constraint",
        "not constraining",
        "ps investment maintained",
        "not implementation-focused",
    }
    if _contains_any(normalized, neutral_phrases):
        return False

    negative_cues = {
        "acquired",
        "acquisition",
        "integration period",
        "integration focus",
        "constraint",
        "may affect timing",
        "timing",
        "restructuring",
        "contraction",
        "layoff",
        "layoffs",
        "hiring freeze",
        "reducing",
        "cost restructuring",
        "build internally",
        "agentic",
        "internal ai",
        "own platform",
        "m&a",
        "roll-up",
        "rollup",
        "take-private",
        "went private",
    }
    key_specific_cues = {
        "internal_ai_overlap": {"internal ai", "agentic", "copilot", "automation engine", "own platform"},
        "strategic_constraints": {"acquired", "acquisition", "integration", "ipo", "pre-ipo", "take-private", "went private", "m&a"},
        "ps_cs_contraction": {"contraction", "layoff", "layoffs", "hiring freeze", "restructuring", "reducing"},
        "build_vs_buy_impl_auto": {"build internally", "internal engine", "agentic", "orchestration layer", "own platform"},
        "ai_acquisition_impl": {"acquired", "acquisition", "ai company", "startup"},
    }
    cues = set(negative_cues)
    cues.update(key_specific_cues.get(key, set()))
    return _contains_any(normalized, cues)


def _infer_industry_from_category(category: str) -> Optional[str]:
    normalized = (category or "").strip().lower()
    if not normalized:
        return None

    mappings = {
        "erp": "ERP",
        "accounting": "Finance",
        "payroll": "HRIS/Payroll",
        "hris": "HRIS/Payroll",
        "procurement": "Procurement",
        "s2p": "Procurement",
        "insuretech": "Insurance",
        "healthtech": "Healthtech",
        "ehr": "Healthtech",
        "emr": "Healthtech",
        "customer success": "Customer Success",
        "field service": "Field Service",
        "tax": "Tax Compliance",
        "legal": "Legal Ops",
        "cpq": "CPQ",
        "plm": "PLM",
        "construction": "Construction",
    }
    for needle, label in mappings.items():
        if needle in normalized:
            return label
    return category.strip()[:120]


def _analyst_signal_columns() -> tuple[list[str], list[str]]:
    positive = [
        "ps_impl_hiring",
        "leadership_org_moves",
        "pr_funding_expansion",
        "events_thought_leadership",
        "reviews_case_studies",
    ]
    negative = [
        "internal_ai_overlap",
        "strategic_constraints",
        "ps_cs_contraction",
        "build_vs_buy_impl_auto",
        "ai_acquisition_impl",
    ]
    return positive, negative


def _extract_import_intelligence(row: dict[str, str]) -> dict[str, Any]:
    # Preserve both normalized analyst signals and the original uploaded values.
    # The normalized shape powers scoring, while the raw row is useful for export
    # and audit/debug scenarios.
    positive_cols, negative_cols = _analyst_signal_columns()

    analyst = {
        "region": _find(row, "region") or None,
        "headquarters": _find(row, "headquarters") or None,
        "category": _find(row, "category_label") or None,
        "core_focus": _find(row, "core_focus") or None,
        "revenue_funding": _find(row, "revenue_funding_label") or None,
        "classification": _find(row, "classification") or None,
        "fit_type": _find(row, "fit_type") or None,
        "confidence": (_find(row, "confidence") or "").lower() or None,
        "icp_why": _find(row, "icp_why") or None,
        "intent_why": _find(row, "intent_why") or None,
        "final_qual": _find(row, "final_qual") or None,
        "sdr": _find(row, "sdr") or None,
        "ae": _find(row, "ae") or None,
    }

    icp_score = _parse_score_0_10(_find(row, "analyst_icp_score"))
    if icp_score is not None:
        analyst["icp_fit_score"] = icp_score

    intent_score = _parse_score_0_10(_find(row, "analyst_intent_score"))
    if intent_score is not None:
        analyst["intent_score"] = intent_score

    positive_signals = []
    for key in positive_cols:
        value = _find(row, key)
        if _has_positive_signal_evidence(key, value):
            positive_signals.append({"key": key, "value": value})

    negative_signals = []
    for key in negative_cols:
        value = _find(row, key)
        if _has_negative_signal_evidence(key, value):
            negative_signals.append({"key": key, "value": value})

    raw_row = {key: value for key, value in row.items() if _has_nonempty_text(value)}

    return {
        "raw_row": raw_row,
        "analyst": {key: value for key, value in analyst.items() if value is not None},
        "positive_signals": positive_signals,
        "negative_signals": negative_signals,
    }


def _derive_uploaded_intent_signals(import_intelligence: dict[str, Any]) -> dict[str, Any]:
    # Convert narrative analyst columns into simple counters/flags that can be
    # merged with machine-generated intent signals later in the pipeline.
    analyst = import_intelligence.get("analyst", {})
    positive_signals = import_intelligence.get("positive_signals", [])
    negative_signals = import_intelligence.get("negative_signals", [])

    return {
        "hiring": 1 if any(signal.get("key") == "ps_impl_hiring" for signal in positive_signals) else 0,
        "funding": 1 if any(signal.get("key") == "pr_funding_expansion" for signal in positive_signals) else 0,
        "product": 1 if any(signal.get("key") in {"events_thought_leadership", "reviews_case_studies"} for signal in positive_signals) else 0,
        "uploaded_intent_score": analyst.get("intent_score"),
        "uploaded_fit_type": analyst.get("fit_type"),
        "uploaded_classification": analyst.get("classification"),
        "uploaded_confidence": analyst.get("confidence"),
        "positive_signal_count": len(positive_signals),
        "negative_signal_count": len(negative_signals),
        "uploaded_signals": {
            "positive": positive_signals,
            "negative": negative_signals,
        },
    }


def _extract_connector(row: dict[str, str], index: int) -> Optional[dict[str, Any]]:
    name = _find(row, f"angel_{index}_name")
    path = _find(row, f"angel_{index}_path")
    why = _find(row, f"angel_{index}_why")
    strength = _parse_strength_score(_find(row, f"angel_{index}_strength"))
    if not any([name, path, why, strength is not None]):
        return None
    payload = {
        "name": name or None,
        "strength": strength,
        "connection_path": path or None,
        "why_it_works": why or None,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _build_company_outreach_lane(
    *,
    connectors: list[dict[str, Any]],
    next_steps: str,
    recommended_strategy: str,
    ownership_stage: str,
    analyst: dict[str, Any],
) -> str:
    # This is a routing heuristic for downstream playbooks, not a perfect model.
    # It deliberately chooses one best lane even when the source data is partial.
    if any(int(connector.get("strength", 0) or 0) >= 3 for connector in connectors):
        return "warm_intro"

    event_text = " ".join(filter(None, [next_steps, recommended_strategy])).lower()
    if "event" in event_text or "invite" in event_text:
        return "event_follow_up"

    if "board" in event_text or "ceo" in event_text or "founder" in event_text:
        return "cold_strategic"

    ownership = (ownership_stage or "").lower()
    if any(keyword in ownership for keyword in ["pe", "vc", "public", "acquired"]):
        return "cold_operator"

    fit_type = str(analyst.get("fit_type") or "").lower()
    if fit_type in {"both", "complex implementation"}:
        return "cold_operator"

    return "cold_strategic"


def _role_focus_from_title(title: str) -> str:
    normalized = (title or "").strip().lower()
    if any(keyword in normalized for keyword in ["ceo", "founder", "chief", "president", "coo"]):
        return "executive sponsor"
    if any(keyword in normalized for keyword in ["vp", "head", "director"]):
        return "functional leader"
    if any(keyword in normalized for keyword in ["implementation", "delivery", "professional services", "onboarding"]):
        return "implementation owner"
    return "post-sales stakeholder"


async def load_workspace_sequence_schedule(session) -> list[dict[str, Any]] | None:
    """Fetch the workspace "Sequence Settings" as [{day_offset, channel}, ...]."""
    from app.models.settings import WorkspaceSettings  # local import avoids cycle
    ws = await session.get(WorkspaceSettings, 1)
    if not ws:
        return None
    return _workspace_step_schedule(ws.outreach_step_delays, ws.outreach_content_settings)


def _workspace_step_schedule(ws_step_delays, ws_content_settings) -> list[dict[str, Any]] | None:
    """Return [{day_offset, channel}, ...] from workspace settings, or None if not usable.

    Shape in the DB: `outreach_step_delays = [0, 3, 7]` and
    `outreach_content_settings.step_templates[i].channel in {email, call, linkedin}`.
    """
    if not isinstance(ws_step_delays, list) or not ws_step_delays:
        return None
    templates = []
    if isinstance(ws_content_settings, dict):
        raw = ws_content_settings.get("step_templates")
        if isinstance(raw, list):
            templates = raw

    schedule: list[dict[str, Any]] = []
    for idx, delay in enumerate(ws_step_delays):
        try:
            day = int(delay)
        except (TypeError, ValueError):
            day = 0
        channel = "email"
        if idx < len(templates) and isinstance(templates[idx], dict):
            raw_channel = str(templates[idx].get("channel") or "email").strip().lower()
            if raw_channel in {"email", "call", "linkedin"}:
                channel = raw_channel
        schedule.append({"day_offset": day, "channel": channel})
    return schedule


def _build_contact_sequence_plan(
    contact_fields: dict[str, Any],
    company_fields: dict[str, Any],
    workspace_schedule: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prospecting = (
        company_fields.get("prospecting_profile")
        if isinstance(company_fields.get("prospecting_profile"), dict)
        else {}
    )
    warm_path = (
        contact_fields.get("warm_intro_path")
        if isinstance(contact_fields.get("warm_intro_path"), dict)
        else {}
    )
    lane = (
        contact_fields.get("outreach_lane")
        or company_fields.get("recommended_outreach_lane")
        or "cold_operator"
    )
    title = str(contact_fields.get("title") or "").strip()
    role_focus = _role_focus_from_title(title)
    company_name = str(company_fields.get("name") or "the account").strip()
    conversation_starter = (
        str(contact_fields.get("conversation_starter") or "").strip()
        or str(prospecting.get("conversation_starter") or "").strip()
    )
    why_now = (
        str(contact_fields.get("personalization_notes") or "").strip()
        or str(company_fields.get("why_now") or "").strip()
        or str(prospecting.get("next_steps") or "").strip()
    )
    beacon_angle = (
        str(company_fields.get("beacon_angle") or "").strip()
        or str(prospecting.get("beacon_angle") or "").strip()
    )
    thesis = (
        str(company_fields.get("account_thesis") or "").strip()
        or str(prospecting.get("account_thesis") or "").strip()
    )
    strategy = str(prospecting.get("recommended_outreach_strategy") or "").strip()
    talking_points = [
        str(item).strip()
        for item in (contact_fields.get("talking_points") or [])
        if str(item).strip()
    ]
    hooks = []
    for item in [
        conversation_starter,
        why_now,
        beacon_angle,
        thesis,
        strategy,
        str(warm_path.get("why_it_works") or "").strip(),
    ] + talking_points:
        if item and item not in hooks:
            hooks.append(item)

    connector_name = str(warm_path.get("name") or "connector").strip()
    connector_path = str(warm_path.get("connection_path") or "").strip()

    if lane == "warm_intro":
        steps = [
            {
                "day_offset": 0,
                "channel": "connector_request",
                "objective": f"Ask {connector_name} to make a warm introduction into {company_name}.",
                "angle": connector_path or strategy or thesis,
                "cta": "Can you make a brief intro and frame why Beacon is relevant now?",
            },
            {
                "day_offset": 2,
                "channel": "connector_follow_up",
                "objective": "Nudge the connector with a short reminder and one-line context.",
                "angle": str(warm_path.get("why_it_works") or "").strip() or why_now or strategy,
                "cta": "If an intro is tough, can you share the best way to approach them directly?",
            },
            {
                "day_offset": 5,
                "channel": "email",
                "objective": "Send a short direct follow-up referencing the shared context and rollout angle.",
                "angle": conversation_starter or why_now or beacon_angle,
                "cta": "Worth comparing notes on implementation friction for 15 minutes?",
            },
            {
                "day_offset": 9,
                "channel": "email",
                "objective": "Close with the Beacon value thesis and a soft opt-out.",
                "angle": beacon_angle or thesis or strategy,
                "cta": "Should I close this out, or is there someone else who owns rollout efficiency?",
            },
        ]
        family = "Warm intro first"
        goal = "Land a connector-led intro before moving the prospect into a direct sequence."
    elif lane == "event_follow_up":
        steps = [
            {
                "day_offset": 1,
                "channel": "email",
                "objective": "Follow up on the event context or adjacent touchpoint while memory is fresh.",
                "angle": conversation_starter or strategy or why_now,
                "cta": "Open to a quick follow-up on the implementation angle we flagged?",
            },
            {
                "day_offset": 4,
                "channel": "email",
                "objective": "Share one company-specific implementation observation and make it actionable.",
                "angle": why_now or thesis or beacon_angle,
                "cta": "Would it be useful to compare how your team handles rollout complexity today?",
            },
            {
                "day_offset": 8,
                "channel": "email",
                "objective": "Ask a sharper operational question tied to their role.",
                "angle": beacon_angle or strategy or conversation_starter,
                "cta": "Is this something your team is actively trying to standardize this quarter?",
            },
            {
                "day_offset": 12,
                "channel": "email",
                "objective": "Send a respectful close-the-loop note with a low-friction next step.",
                "angle": thesis or why_now,
                "cta": "Happy to step back if timing is off. Worth revisiting later this quarter?",
            },
        ]
        family = "Event follow-up"
        goal = "Turn an existing event or community touchpoint into a real prospecting conversation."
    elif lane == "cold_strategic":
        steps = [
            {
                "day_offset": 0,
                "channel": "email",
                "objective": "Lead with the company-level why-now and the business risk of rollout inefficiency.",
                "angle": why_now or thesis or beacon_angle,
                "cta": "Would a short conversation on implementation leverage be timely?",
            },
            {
                "day_offset": 4,
                "channel": "email",
                "objective": "Anchor on executive outcomes: speed to value, margin, and operating discipline.",
                "angle": thesis or strategy or beacon_angle,
                "cta": "Is this worth a brief discussion with the leader who owns delivery outcomes?",
            },
            {
                "day_offset": 9,
                "channel": "email",
                "objective": "Use one sharp strategic question to qualify urgency.",
                "angle": conversation_starter or why_now or strategy,
                "cta": "How are you scaling implementation quality without adding equal delivery overhead?",
            },
            {
                "day_offset": 14,
                "channel": "email",
                "objective": "Send the final note with a redirect ask if they are not the owner.",
                "angle": beacon_angle or thesis,
                "cta": "Should I reach out to your implementation or services lead instead?",
            },
        ]
        family = "Executive strategic"
        goal = "Earn a strategic reply or redirect from an executive stakeholder."
    else:
        steps = [
            {
                "day_offset": 0,
                "channel": "email",
                "objective": "Lead with the operational pain this team likely feels during onboarding and rollout.",
                "angle": conversation_starter or why_now or strategy,
                "cta": "Open to comparing notes on where implementation work slows down today?",
            },
            {
                "day_offset": 3,
                "channel": "email",
                "objective": "Shift to workflow, configuration, migration, or rules-engine complexity.",
                "angle": beacon_angle or thesis or strategy,
                "cta": "Is this a problem your team owns directly, or does it sit with another delivery leader?",
            },
            {
                "day_offset": 7,
                "channel": "email",
                "objective": "Tie Beacon to services efficiency, repeatability, or partner scale.",
                "angle": why_now or strategy or thesis,
                "cta": "Worth a short look at how teams standardize this work without slowing delivery?",
            },
            {
                "day_offset": 12,
                "channel": "email",
                "objective": "Ask for a redirect if the persona is close but not perfect.",
                "angle": conversation_starter or beacon_angle,
                "cta": "If this is better owned by implementation, PS ops, or onboarding, who would you point me to?",
            },
            {
                "day_offset": 18,
                "channel": "email",
                "objective": "Close the loop cleanly while preserving a future path back in.",
                "angle": thesis or why_now,
                "cta": "Should I pause this for now, or is this worth a revisit after the current rollout cycle?",
            },
        ]
        family = "Implementation/operator"
        goal = "Start with the operator pain and convert it into an implementation efficiency conversation."

    # Workspace "Sequence Settings" override: if the workspace has configured
    # its own cadence (e.g. Email D0 -> LinkedIn D3 -> Call D7), reshape the
    # lane's steps to match. We keep the lane-specific objective/angle/cta
    # copy by index and only swap day_offset + channel so the prospect plan
    # still feels personalized.
    if workspace_schedule:
        reshaped: list[dict[str, Any]] = []
        for idx, slot in enumerate(workspace_schedule):
            base = steps[idx] if idx < len(steps) else dict(steps[-1])
            new_step = dict(base)
            new_step["day_offset"] = slot.get("day_offset", new_step.get("day_offset", 0))
            new_step["channel"] = slot.get("channel", new_step.get("channel", "email"))
            reshaped.append(new_step)
        steps = reshaped

    return {
        "lane": lane,
        "sequence_family": family,
        "goal": goal,
        "why_this_person": f"This contact looks like a strong {role_focus} based on the current title and company context.",
        "personalization_hooks": hooks[:6],
        "steps": steps,
    }


# Any of these sequence_status values (or a non-null instantly_campaign_id)
# means the contact has left the "ready / planned" phase — we must not
# overwrite their in-flight or terminal progress by regenerating the plan.
_LOCKED_SEQUENCE_STATUSES = frozenset({
    "queued_instantly",
    "sent",
    "launched",
    "completed",
    "replied",
    "meeting_booked",
    "interested",
    "not_interested",
    "unsubscribed",
    "bounced",
})


def _contact_has_started_sequence(contact: Contact) -> bool:
    if getattr(contact, "instantly_campaign_id", None):
        return True
    status = (getattr(contact, "sequence_status", None) or "").strip().lower()
    return status in _LOCKED_SEQUENCE_STATUSES


def refresh_contact_sequence_plan(
    contact: Contact,
    company: Company,
    *,
    workspace_schedule: list[dict[str, Any]] | None = None,
) -> Contact:
    # Never overwrite a plan for a contact whose sequence is already in flight
    # or has terminated — the workspace-settings change only shapes *future*
    # prospects' progress, not existing ones.
    if _contact_has_started_sequence(contact):
        return contact

    contact_fields: dict[str, Any] = {
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "title": contact.title,
        "email": contact.email,
        "outreach_lane": contact.outreach_lane or company.recommended_outreach_lane,
        "conversation_starter": contact.conversation_starter,
        "personalization_notes": contact.personalization_notes,
        "talking_points": contact.talking_points if isinstance(contact.talking_points, list) else [],
        "warm_intro_path": contact.warm_intro_path if isinstance(contact.warm_intro_path, dict) else {},
    }
    company_fields: dict[str, Any] = {
        "name": company.name,
        "recommended_outreach_lane": company.recommended_outreach_lane,
        "account_thesis": company.account_thesis,
        "why_now": company.why_now,
        "beacon_angle": company.beacon_angle,
        "prospecting_profile": company.prospecting_profile if isinstance(company.prospecting_profile, dict) else {},
    }
    sequence_plan = _build_contact_sequence_plan(
        contact_fields,
        company_fields,
        workspace_schedule=workspace_schedule,
    )
    enrichment = contact.enrichment_data if isinstance(contact.enrichment_data, dict) else {}
    contact.enrichment_data = {
        **enrichment,
        "sequence_plan": sequence_plan,
    }
    return contact


def _company_import_raw_row(company: Company) -> dict[str, Any]:
    import_block = company.enrichment_sources if isinstance(company.enrichment_sources, dict) else {}
    import_block = import_block.get("import") if isinstance(import_block.get("import"), dict) else {}
    raw_row = import_block.get("raw_row") if isinstance(import_block, dict) else {}
    return raw_row if isinstance(raw_row, dict) else {}


def _extract_prospecting_intelligence(row: dict[str, str], import_intelligence: dict[str, Any]) -> dict[str, Any]:
    analyst = import_intelligence.get("analyst", {})
    ownership_stage = _find(row, "ownership_stage") or None
    recommended_strategy = _find(row, "recommended_outreach_strategy") or None
    conversation_starter = _find(row, "conversation_starter") or None
    what_they_do = _find(row, "what_they_do") or None
    who_they_are = _find(row, "who_they_are") or None
    next_steps = _find(row, "next_steps") or None

    investors = {
        "pe": _split_semistructured_list(_find(row, "pe_investors")),
        "vc_growth": _split_semistructured_list(_find(row, "vc_growth_investors")),
        "strategic": _split_semistructured_list(_find(row, "strategic_investors")),
    }
    connectors = [connector for connector in (_extract_connector(row, idx) for idx in (1, 2, 3)) if connector]
    recommended_lane = _build_company_outreach_lane(
        connectors=connectors,
        next_steps=next_steps or "",
        recommended_strategy=recommended_strategy or "",
        ownership_stage=ownership_stage or "",
        analyst=analyst if isinstance(analyst, dict) else {},
    )

    thesis_parts = [
        analyst.get("icp_why") if isinstance(analyst, dict) else None,
        what_they_do,
        analyst.get("core_focus") if isinstance(analyst, dict) else None,
    ]
    thesis = next((str(part).strip() for part in thesis_parts if part), None)

    why_now_parts = [
        recommended_strategy,
        next_steps,
        analyst.get("intent_why") if isinstance(analyst, dict) else None,
        ownership_stage,
    ]
    why_now = next((str(part).strip() for part in why_now_parts if part), None)

    beacon_parts = [
        who_they_are,
        analyst.get("core_focus") if isinstance(analyst, dict) else None,
        recommended_strategy,
    ]
    beacon_angle = next((str(part).strip() for part in beacon_parts if part), None)

    return {
        "ownership_stage": ownership_stage,
        "recommended_outreach_strategy": recommended_strategy,
        "recommended_lane": recommended_lane,
        "conversation_starter": conversation_starter,
        "what_they_do": what_they_do,
        "who_they_are": who_they_are,
        "next_steps": next_steps,
        "investors": investors,
        "warm_paths": connectors,
        "account_thesis": thesis,
        "why_now": why_now,
        "beacon_angle": beacon_angle,
    }


def _split_contact_name(name: str) -> tuple[str, str]:
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    cleaned = re.sub(r",\s*(mba|phd|pmp|cpa|cissp|shrm-cp|shrm-scp|csap|md)\b.*$", "", cleaned, flags=re.IGNORECASE)
    if not cleaned:
        return "", ""
    parts = cleaned.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


_UPLOADED_TIMEZONE_TO_IANA: dict[str, str] = {
    "AEST": "Australia/Sydney",
    "CET": "Europe/Berlin",
    "COT": "America/Bogota",
    "CST": "America/Chicago",
    "EET": "Europe/Athens",
    "EST": "America/New_York",
    "GMT": "Europe/London",
    "GST": "Asia/Dubai",
    "IST": "Asia/Kolkata",
    "JST": "Asia/Tokyo",
    "MST": "America/Denver",
    "NZST": "Pacific/Auckland",
    "PST": "America/Los_Angeles",
    "SGT": "Asia/Singapore",
}


def _normalize_uploaded_timezone(value: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    if not cleaned:
        return None
    if "/" in cleaned and cleaned in set(_UPLOADED_TIMEZONE_TO_IANA.values()):
        return cleaned
    # Workbooks sometimes contain redundant forms like "EST / EST". Take the
    # first token because all supported abbreviations are single-zone for our
    # prospecting use case.
    token = re.split(r"[/,;|]", cleaned, maxsplit=1)[0].strip().upper()
    return _UPLOADED_TIMEZONE_TO_IANA.get(token)


def row_to_contact_fields(row: dict[str, str], company_fields: dict[str, Any]) -> Optional[dict[str, Any]]:
    contact_name = _find(row, "contact_name")
    first = _find(row, "contact_first_name")
    last = _find(row, "contact_last_name")
    title = _find(row, "contact_title")
    email = _clean_email(_find(row, "contact_email"))
    linkedin_url = _find(row, "linkedin_url") or _find_person_linkedin(row) or None
    # Prefer "Direct / Mobile (Personal)"; else generic contact phone; else HQ
    # direct line; else any column with 'phone' in its header (catches a 2nd
    # de-collided "Phone" column). Normalize to strip fused type-prefixes like
    # "m+1…"/"d+1…" that otherwise T9-corrupt the OS dialer.
    phone = _normalize_phone(
        _find(row, "direct_mobile")
        or _find(row, "contact_phone")
        or _find(row, "hq_direct_line")
        or _find_any_phone(row)
    )

    if not first and not last and contact_name:
        first, last = _split_contact_name(contact_name)

    if not any([first, last, title, email, linkedin_url]):
        return None
    # Hygiene filter is applied as a *warning* at the endpoint level — rows are
    # still imported so the rep can correct them in-app rather than losing data.

    import_block = company_fields.get("enrichment_sources", {}).get("import", {}) if isinstance(company_fields.get("enrichment_sources"), dict) else {}
    prospecting = company_fields.get("prospecting_profile") if isinstance(company_fields.get("prospecting_profile"), dict) else {}
    connectors = prospecting.get("warm_paths") if isinstance(prospecting, dict) else []
    best_connector = connectors[0] if isinstance(connectors, list) and connectors else {}

    # Rich intelligence columns from the Beacon "The 100" workbook.
    email_confidence = (_find(row, "email_confidence") or "").strip().upper()
    career_arc = _find(row, "career_arc") or None
    linkedin_activity = _find(row, "linkedin_activity") or None
    intent_signals = _find(row, "intent_signals") or None
    messaging_angle = _find(row, "primary_messaging_angle") or None
    personalization_hook = _find(row, "personalization_hook") or None
    research_confidence = _find(row, "research_confidence") or None
    outreach_priority = _find(row, "outreach_priority") or None
    contact_location = _find(row, "contact_location") or None
    tenure_role = _find(row, "tenure_role") or None
    tenure_company = _find(row, "tenure_company") or None
    tier = _find(row, "tier") or None
    contact_source = _find(row, "contact_source") or None
    apollo_id = _find(row, "apollo_id") or None

    # Pre-written email cadence — each step becomes the actual outreach body so
    # Instantly sends the hand-crafted copy instead of AI-generated placeholders.
    pre_written_emails = []
    for idx in (1, 2, 3):
        subject = _find(row, f"email{idx}_subject")
        body = _find(row, f"email{idx}_body")
        when = _find(row, f"email{idx}_when")
        if subject or body:
            pre_written_emails.append({
                "step": idx,
                "subject": subject or None,
                "body": body or None,
                "when_to_use": when or None,
            })

    # Talking points: prefer the workbook's messaging angle + hook over the
    # company-level defaults when both exist (per-prospect research wins).
    talking_points: list[str] = []
    for candidate in [
        messaging_angle,
        personalization_hook,
        prospecting.get("recommended_outreach_strategy") if isinstance(prospecting, dict) else None,
        prospecting.get("conversation_starter") if isinstance(prospecting, dict) else None,
        import_block.get("analyst", {}).get("intent_why") if isinstance(import_block, dict) and isinstance(import_block.get("analyst"), dict) else None,
    ]:
        if candidate:
            talking_points.append(str(candidate).strip())

    # Prefer the workbook's per-prospect hooks for conversation_starter +
    # personalization_notes so the Prospecting drawer surfaces them first.
    conversation_starter = (
        personalization_hook
        or messaging_angle
        or (prospecting.get("conversation_starter") if isinstance(prospecting, dict) else None)
    )
    personalization_notes = (
        career_arc
        or intent_signals
        or (prospecting.get("why_now") if isinstance(prospecting, dict) else None)
    )

    uploaded_timezone = _normalize_uploaded_timezone(_find(row, "contact_timezone"))
    inferred_timezone = uploaded_timezone
    try:
        from app.services.timezone_infer import infer_timezone

        if not inferred_timezone:
            inferred_timezone = infer_timezone(
                phone=phone,
                company_hq=contact_location or company_fields.get("headquarters"),
                company_region=company_fields.get("region"),
                company_name=company_fields.get("name"),
            )
    except Exception:
        inferred_timezone = uploaded_timezone

    base_fields = {
        "first_name": first[:120],
        "last_name": last[:160],
        "email": email or None,
        # If the workbook explicitly flags the email as HIGH confidence, seed
        # email_verified so the rep doesn't have to re-verify. Other values
        # (MEDIUM/LOW/blank) leave email_verified unset for normal verification.
        "email_verified": True if email and email_confidence == "HIGH" else None,
        "phone": phone,
        "title": title or None,
        "linkedin_url": linkedin_url,
        "assigned_rep_email": company_fields.get("assigned_rep_email"),
        "outreach_lane": company_fields.get("recommended_outreach_lane"),
        "sequence_status": "ready" if email else "research_needed",
        "instantly_status": "ready" if email else "missing_email",
        "warm_intro_strength": _parse_int(str(best_connector.get("strength") or "")) if best_connector else None,
        "warm_intro_path": best_connector or None,
        "conversation_starter": conversation_starter,
        "personalization_notes": personalization_notes,
        "talking_points": talking_points or None,
        "timezone": inferred_timezone,
        "enrichment_data": {
            "source": "upload",
            "raw_row": {key: value for key, value in row.items() if _has_nonempty_text(value)},
            # Structured per-prospect research from the workbook. Lives in
            # enrichment_data so the rep sees it in the drawer and so later
            # enrichment passes can layer on top without losing this content.
            "workbook": {
                "tier": tier,
                "email_confidence": email_confidence or None,
                "source": contact_source,
                "apollo_id": apollo_id,
                "location": contact_location,
                "tenure_role": tenure_role,
                "tenure_company": tenure_company,
                "career_arc": career_arc,
                "linkedin_activity": linkedin_activity,
                "intent_signals": intent_signals,
                "primary_messaging_angle": messaging_angle,
                "personalization_hook": personalization_hook,
                "research_confidence": research_confidence,
                "outreach_priority": outreach_priority,
                "icp_score": _find(row, "contact_icp_score") or None,
                "intent_score": _find(row, "contact_intent_score") or None,
            },
            # Hand-crafted outreach copy — the OutreachDrawer prefers these
            # over AI-generated text when present, and Instantly pushes them
            # verbatim on Launch.
            "pre_written_emails": pre_written_emails or None,
        },
    }
    enrichment_data = base_fields["enrichment_data"] if isinstance(base_fields.get("enrichment_data"), dict) else {}
    enrichment_data["sequence_plan"] = _build_contact_sequence_plan(base_fields, company_fields)
    base_fields["enrichment_data"] = enrichment_data
    return base_fields


def merge_company_from_upload(company: Company, fields: dict[str, Any]) -> Company:
    source_of_truth_fields = [
        "name",
        "domain",
        "industry",
        "vertical",
        "employee_count",
        "arr_estimate",
        "funding_stage",
        "region",
        "headquarters",
        "assigned_to_id",
        "assigned_rep",
        "assigned_rep_email",
        "assigned_rep_name",
        # NOTE: sdr_id / sdr_email / sdr_name are deliberately NOT here. SDR
        # assignment must flow through the role-validated assignment endpoints
        # (_validate_assignment_user), never an unvalidated CSV column — that
        # path was how AE ids historically leaked into sdr_id.
    ]
    for key in source_of_truth_fields:
        incoming = fields.get(key)
        if incoming not in (None, "", [], {}):
            setattr(company, key, incoming)

    simple_fields = [
        "industry",
        "vertical",
        "employee_count",
        "arr_estimate",
        "funding_stage",
        "region",
        "headquarters",
        "description",
        "account_thesis",
        "why_now",
        "beacon_angle",
        "recommended_outreach_lane",
        "instantly_campaign_id",
    ]
    for key in simple_fields:
        incoming = fields.get(key)
        current = getattr(company, key, None)
        if incoming and (not current):
            setattr(company, key, incoming)

    for json_field in ("enrichment_sources", "intent_signals", "prospecting_profile", "outreach_plan"):
        incoming = fields.get(json_field)
        current = getattr(company, json_field, None)
        if isinstance(current, dict) and isinstance(incoming, dict):
            merged = {**current, **incoming}
            setattr(company, json_field, merged)
        elif incoming and not current:
            setattr(company, json_field, incoming)

    company.updated_at = datetime.utcnow()
    return company


_COMMITTEE_ROLE_LABELS = {
    "economic_buyer": "Economic Buyer",
    "champion": "Champion",
    "technical_evaluator": "Technical Evaluator",
    "implementation_owner": "Implementation Owner",
}

_IMPLEMENTATION_OWNER_KEYWORDS = [
    "ops", "operations", "admin", "administrator", "systems", "enablement",
    "implementation", "program", "project", "revops", "hris", "people ops",
    "people operations", "it manager", "it director", "business systems",
]


def _canonical_persona(
    persona: Optional[str] = None,
    persona_type: Optional[str] = None,
) -> str:
    mapping = {
        "buyer": "economic_buyer",
        "economic_buyer": "economic_buyer",
        "champion": "champion",
        "evaluator": "technical_evaluator",
        "technical_evaluator": "technical_evaluator",
        "blocker": "unknown",
        "unknown": "unknown",
    }
    for candidate in (persona, persona_type):
        normalized = mapping.get((candidate or "").strip().lower())
        if normalized:
            return normalized
    return "unknown"


def _infer_committee_role(
    title: Optional[str],
    persona: Optional[str] = None,
    persona_type: Optional[str] = None,
) -> str:
    title_lower = (title or "").strip().lower()
    if any(keyword in title_lower for keyword in _IMPLEMENTATION_OWNER_KEYWORDS):
        return "implementation_owner"

    canonical_persona = _canonical_persona(persona, persona_type)
    if canonical_persona in _COMMITTEE_ROLE_LABELS:
        return canonical_persona

    return "unknown"


def _contact_priority_score(contact: Contact) -> int:
    score = 0
    role = _infer_committee_role(contact.title, contact.persona, contact.persona_type)
    if role == "economic_buyer":
        score += 40
    elif role == "champion":
        score += 34
    elif role == "technical_evaluator":
        score += 30
    elif role == "implementation_owner":
        score += 26

    seniority = (contact.seniority or "").lower()
    if seniority in {"c_suite", "csuite", "c-suite", "founder", "owner"}:
        score += 16
    elif seniority == "vp":
        score += 12
    elif seniority in {"director", "head"}:
        score += 9
    elif seniority == "manager":
        score += 5

    if contact.email:
        score += 5
    if contact.linkedin_url:
        score += 3

    return score


async def _build_committee_coverage(company: Company, session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        select(Contact)
        .where(Contact.company_id == company.id)
        .order_by(Contact.created_at.desc())
    )
    contacts = result.scalars().all()

    covered_roles: set[str] = set()
    persona_counts = {
        "economic_buyer": 0,
        "champion": 0,
        "technical_evaluator": 0,
        "implementation_owner": 0,
        "unknown": 0,
    }
    best_by_role: dict[str, dict[str, Any]] = {}

    for contact in contacts:
        persona = _canonical_persona(contact.persona, contact.persona_type)
        role = _infer_committee_role(contact.title, contact.persona, contact.persona_type)
        persona_counts[role if role in persona_counts else "unknown"] += 1

        if role in _COMMITTEE_ROLE_LABELS:
            covered_roles.add(role)
            candidate = {
                "contact_id": str(contact.id),
                "name": f"{contact.first_name} {contact.last_name}".strip(),
                "title": contact.title,
                "persona": persona,
                "role": role,
                "email": contact.email,
                "score": _contact_priority_score(contact),
            }
            current = best_by_role.get(role)
            if not current or candidate["score"] > current["score"]:
                best_by_role[role] = candidate
        elif persona == "unknown":
            persona_counts["unknown"] += 0

    missing_roles = [
        role for role in _COMMITTEE_ROLE_LABELS
        if role not in covered_roles
    ]

    coverage_score = round((len(covered_roles) / max(len(_COMMITTEE_ROLE_LABELS), 1)) * 100)
    recommended_next_roles = []
    for role in missing_roles:
        if role == "economic_buyer":
            why = "Find the budget owner so outreach can anchor on ROI and deployment risk."
        elif role == "champion":
            why = "Find the day-to-day operator who feels the rollout pain and can push internally."
        elif role == "technical_evaluator":
            why = "Find the technical reviewer who will validate integration, security, and feasibility."
        else:
            why = "Find the implementation owner who will own change management and rollout execution."
        recommended_next_roles.append({
            "role": role,
            "label": _COMMITTEE_ROLE_LABELS[role],
            "why": why,
        })

    best_contacts = [
        {
            "contact_id": value["contact_id"],
            "name": value["name"],
            "title": value["title"],
            "persona": value["persona"],
            "role": value["role"],
            "label": _COMMITTEE_ROLE_LABELS.get(value["role"], value["role"]),
            "email": value["email"],
        }
        for _, value in sorted(best_by_role.items(), key=lambda item: item[1]["score"], reverse=True)
    ]

    return {
        "total_contacts": len(contacts),
        "coverage_score": coverage_score,
        "covered_roles": [
            {"role": role, "label": _COMMITTEE_ROLE_LABELS[role]}
            for role in _COMMITTEE_ROLE_LABELS
            if role in covered_roles
        ],
        "missing_roles": recommended_next_roles,
        "persona_counts": persona_counts,
        "best_contacts": best_contacts,
    }


def _build_prospecting_priorities(
    company: Company,
    committee_coverage: dict[str, Any],
    intent: dict[str, Any],
) -> list[str]:
    priorities: list[str] = []

    if (intent or {}).get("funding"):
        priorities.append("Lead with fast time-to-value and rollout control while new budget is available.")
    if (intent or {}).get("hiring"):
        priorities.append("Position Beacon around change-management and onboarding capacity as the team scales.")
    if (intent or {}).get("product"):
        priorities.append("Tie outreach to recent launches or expansion and the need to operationalize adoption quickly.")

    missing_roles = [item.get("label") for item in committee_coverage.get("missing_roles", []) if item.get("label")]
    if missing_roles:
        priorities.append(f"Committee gap: find {', '.join(missing_roles[:3])} before pushing for a late-stage meeting.")

    if company.icp_tier in {"hot", "warm"} and committee_coverage.get("coverage_score", 0) < 75:
        priorities.append("This account fits the ICP, but committee coverage is still thin. Expand contact depth before sequencing heavily.")

    if not priorities:
        priorities.append("Use a role-based sequence: economic buyer, champion, then technical evaluator.")

    return priorities[:4]


def refresh_company_prospecting_fields(company: Company, contacts: Optional[list[Contact]] = None) -> Company:
    import_block = company.enrichment_sources.get("import") if isinstance(company.enrichment_sources, dict) else {}
    analyst = import_block.get("analyst") if isinstance(import_block, dict) and isinstance(import_block.get("analyst"), dict) else {}
    imported_signals = import_block.get("uploaded_signals") if isinstance(import_block, dict) and isinstance(import_block.get("uploaded_signals"), dict) else {}
    existing_profile = company.prospecting_profile if isinstance(company.prospecting_profile, dict) else {}
    cache = company.enrichment_cache if isinstance(company.enrichment_cache, dict) else {}
    ai_entry = cache.get("ai_summary")
    ai_data = ai_entry.get("data") if isinstance(ai_entry, dict) and isinstance(ai_entry.get("data"), dict) else {}
    committee_entry = cache.get("committee_coverage")
    committee_data = committee_entry.get("data") if isinstance(committee_entry, dict) and isinstance(committee_entry.get("data"), dict) else {}
    priorities_entry = cache.get("prospecting_priorities")
    priorities_data = priorities_entry.get("data") if isinstance(priorities_entry, dict) and isinstance(priorities_entry.get("data"), list) else []

    warm_paths = existing_profile.get("warm_paths") if isinstance(existing_profile.get("warm_paths"), list) else []
    recommended_lane = (
        existing_profile.get("recommended_lane")
        or company.recommended_outreach_lane
        or _build_company_outreach_lane(
            connectors=warm_paths if isinstance(warm_paths, list) else [],
            next_steps=str(existing_profile.get("next_steps") or ""),
            recommended_strategy=str(existing_profile.get("recommended_outreach_strategy") or ""),
            ownership_stage=str(existing_profile.get("ownership_stage") or ""),
            analyst=analyst if isinstance(analyst, dict) else {},
        )
    )

    account_thesis = (
        company.account_thesis
        or str(existing_profile.get("account_thesis") or "").strip()
        or str(analyst.get("icp_why") or "").strip()
        or str(ai_data.get("description") or "").strip()
        or company.description
    )
    why_now = (
        company.why_now
        or str(existing_profile.get("why_now") or "").strip()
        or str(existing_profile.get("recommended_outreach_strategy") or "").strip()
        or str(ai_data.get("intent_signals_summary") or "").strip()
        or str(analyst.get("intent_why") or "").strip()
    )
    beacon_angle = (
        company.beacon_angle
        or str(existing_profile.get("beacon_angle") or "").strip()
        or str(ai_data.get("recommended_approach") or "").strip()
        or str(analyst.get("core_focus") or "").strip()
    )

    best_contacts = []
    for contact in contacts or []:
        label = _infer_committee_role(contact.title, contact.persona, contact.persona_type)
        best_contacts.append({
            "name": f"{contact.first_name} {contact.last_name}".strip(),
            "title": contact.title,
            "label": _COMMITTEE_ROLE_LABELS.get(label, label.replace("_", " ").title()),
            "email": contact.email,
            "lane": contact.outreach_lane,
            "sequence_status": contact.sequence_status,
        })

    owner_name = company.assigned_rep_name or company.assigned_rep
    owner_email = company.assigned_rep_email
    if owner_email and not owner_name:
        owner_name = owner_email
    if owner_name and not company.assigned_rep:
        company.assigned_rep = owner_name

    company.account_thesis = account_thesis or None
    company.why_now = why_now or None
    company.beacon_angle = beacon_angle or None
    company.recommended_outreach_lane = recommended_lane or None

    company.prospecting_profile = {
        **existing_profile,
        "account_thesis": account_thesis or None,
        "why_now": why_now or None,
        "beacon_angle": beacon_angle or None,
        "recommended_lane": recommended_lane or None,
        "analyst_fit_type": analyst.get("fit_type") if isinstance(analyst, dict) else None,
        "analyst_classification": analyst.get("classification") if isinstance(analyst, dict) else None,
        "ownership_stage": existing_profile.get("ownership_stage"),
        "investors": existing_profile.get("investors"),
        "warm_paths": warm_paths,
        "conversation_starter": existing_profile.get("conversation_starter"),
        "recommended_outreach_strategy": existing_profile.get("recommended_outreach_strategy"),
        "next_steps": existing_profile.get("next_steps"),
        "committee_score": committee_data.get("coverage_score") if isinstance(committee_data, dict) else None,
        "best_contacts": best_contacts[:5],
        "uploaded_positive_signals": imported_signals.get("positive") if isinstance(imported_signals, dict) else [],
        "uploaded_negative_signals": imported_signals.get("negative") if isinstance(imported_signals, dict) else [],
        "priorities": priorities_data[:4] if isinstance(priorities_data, list) else [],
    }

    contact_count = len(contacts or [])
    instantly_ready = any((contact.email or "").strip() for contact in (contacts or []))
    warm_ready = any(int(path.get("strength", 0) or 0) >= 3 for path in warm_paths if isinstance(path, dict))
    next_best_action = (
        existing_profile.get("recommended_outreach_strategy")
        or (priorities_data[0] if priorities_data else None)
        or why_now
    )
    company.outreach_plan = {
        **(company.outreach_plan if isinstance(company.outreach_plan, dict) else {}),
        "owner_email": owner_email,
        "owner_name": owner_name,
        "assigned_company_owner": bool(owner_email),
        "recommended_lane": recommended_lane,
        "connector_first": warm_ready,
        "instantly_ready": instantly_ready,
        "contact_count": contact_count,
        "sequence_family": {
            "warm_intro": "Connector request before Instantly",
            "event_follow_up": "Event follow-up sequence",
            "cold_operator": "Implementation/operator sequence",
            "cold_strategic": "Executive strategic sequence",
        }.get(str(recommended_lane), "General outbound sequence"),
        "next_best_action": next_best_action,
    }
    return company


def account_priority_snapshot(company: Company) -> dict[str, Any]:
    cache = company.enrichment_cache if isinstance(company.enrichment_cache, dict) else {}
    committee_entry = cache.get("committee_coverage")
    committee_data = committee_entry.get("data") if isinstance(committee_entry, dict) else committee_entry
    committee = committee_data if isinstance(committee_data, dict) else {}

    committee_score = int(committee.get("coverage_score", 0) or 0)
    intent = company.intent_signals if isinstance(company.intent_signals, dict) else {}
    uploaded_intent_score = int(round(float(intent.get("uploaded_intent_score", 0) or 0) * 10))
    positive_signal_count = int(intent.get("positive_signal_count", 0) or 0)
    negative_signal_count = int(intent.get("negative_signal_count", 0) or 0)

    inferred_intent = min(
        100,
        uploaded_intent_score
        + (int(intent.get("hiring", 0) or 0) * 14)
        + (int(intent.get("funding", 0) or 0) * 18)
        + (int(intent.get("product", 0) or 0) * 10)
        + (positive_signal_count * 5),
    )
    inferred_intent = max(inferred_intent - (negative_signal_count * 8), 0)
    prospecting_profile = company.prospecting_profile if isinstance(company.prospecting_profile, dict) else {}
    warm_paths = prospecting_profile.get("warm_paths") if isinstance(prospecting_profile.get("warm_paths"), list) else []
    strongest_warm_path = max(
        [int(path.get("strength", 0) or 0) for path in warm_paths if isinstance(path, dict)] or [0]
    )
    outreach_leverage = min(100, strongest_warm_path * 22)
    if (company.recommended_outreach_lane or "") == "event_follow_up":
        outreach_leverage = max(outreach_leverage, 56)
    elif (company.recommended_outreach_lane or "") == "warm_intro":
        outreach_leverage = max(outreach_leverage, 72)

    disposition = (company.disposition or "").strip().lower()
    outreach_status = (company.outreach_status or "").strip().lower()

    interest_score = max(uploaded_intent_score, inferred_intent)
    if disposition == "interested":
        interest_score = 92
    elif disposition == "working":
        interest_score = max(interest_score, 68)
    elif disposition == "nurture":
        interest_score = min(max(interest_score, 42), 60)
    elif disposition == "not_interested":
        interest_score = 8
    elif disposition in {"bad_fit", "do_not_target"}:
        interest_score = 0

    if outreach_status == "meeting_booked":
        interest_score = max(interest_score, 90)
    elif outreach_status == "replied":
        interest_score = max(interest_score, 72)
    elif outreach_status == "contacted":
        interest_score = max(interest_score, 48)

    priority_score = round(
        (float(company.icp_score or 0) * 0.52)
        + (float(inferred_intent) * 0.20)
        + (float(committee_score) * 0.13)
        + (float(interest_score) * 0.10)
        + (float(outreach_leverage) * 0.05)
    )

    if disposition in {"not_interested", "bad_fit", "do_not_target"}:
        priority_score = min(priority_score, 20)
    elif disposition == "interested":
        priority_score = max(priority_score, 78)

    priority_score = max(min(priority_score, 100), 0)

    if priority_score >= 75:
        priority_band = "high"
    elif priority_score >= 50:
        priority_band = "medium"
    else:
        priority_band = "low"

    if interest_score >= 75:
        interest_level = "high"
    elif interest_score >= 45:
        interest_level = "medium"
    else:
        interest_level = "low"

    return {
        "priority_score": priority_score,
        "priority_band": priority_band,
        "interest_score": interest_score,
        "interest_level": interest_level,
        "committee_score": committee_score,
        "uploaded_intent_score": uploaded_intent_score,
        "outreach_leverage": outreach_leverage,
    }


_PAID_CACHE_TTL_HOURS = 24 * 14
_BATCH_PARALLELISM = 3

_PRIORITY_TITLE_HINTS = (
    "director of engineering",
    "engineering director",
    "vp engineering",
    "vice president of engineering",
    "head of engineering",
    "director of professional services",
    "vp professional services",
    "head of professional services",
    "professional services",
    "implementation consulting",
    "implementation consultant",
    "implementation director",
    "implementation lead",
    "implementation manager",
    "implementation",
    "solutions consulting",
    "solutions consultant",
    "customer implementation",
    "product management",
    "product manager",
    "director of product",
    "vp product",
    "head of product",
    "technical program",
    "data science",
    "director of data",
    "head of data",
    "director of technology",
    "head of technology",
    "cto",
    "chief technology officer",
    "platform engineering",
    "enterprise applications",
)

_PRIORITY_PERSONA_HINTS = {
    "buyer",
    "champion",
    "evaluator",
    "technical_evaluator",
    "implementation_owner",
}


def _cache_entry_is_fresh(cache: dict[str, Any], key: str, ttl_hours: int) -> bool:
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return False
    fetched_at = entry.get("fetched_at")
    if not isinstance(fetched_at, str):
        return False
    try:
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if fetched.tzinfo is not None:
        fetched = fetched.replace(tzinfo=None)
    return datetime.utcnow() - fetched <= timedelta(hours=ttl_hours)


def is_priority_stakeholder_candidate(candidate: Contact | dict[str, Any]) -> bool:
    """
    Keep sourced contacts focused on the small set of implementation / product /
    engineering personas Beacon cares about most.
    """
    if isinstance(candidate, Contact):
        title = candidate.title or ""
        persona = candidate.persona or ""
        persona_type = candidate.persona_type or ""
    else:
        title = str(candidate.get("title") or "")
        persona = str(candidate.get("persona") or "")
        persona_type = str(candidate.get("persona_type") or "")

    normalized_title = title.strip().lower()
    normalized_persona = (persona_type or persona).strip().lower()

    if normalized_persona in _PRIORITY_PERSONA_HINTS:
        return True

    if not normalized_title:
        return False

    if any(hint in normalized_title for hint in _PRIORITY_TITLE_HINTS):
        return True

    fallback_keywords = (
        "engineering",
        "implementation",
        "professional services",
        "product",
        "technology",
        "technical",
        "data",
    )
    senior_keywords = ("director", "head", "vp", "vice president", "chief", "lead")
    return any(keyword in normalized_title for keyword in fallback_keywords) and any(
        keyword in normalized_title for keyword in senior_keywords
    )


def append_company_activity_log(
    company: Company,
    *,
    action: str,
    actor_name: str | None = None,
    actor_email: str | None = None,
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Company:
    """
    Store lightweight account-sourcing audit entries directly on the company so
    the company detail page can explain what changed without another table.
    """
    cache = copy.deepcopy(company.enrichment_cache or {})
    existing = cache.get("activity_log")
    entries = list(existing) if isinstance(existing, list) else []
    entries.append(
        {
            "action": action,
            "message": message or action.replace("_", " ").title(),
            "actor_name": actor_name,
            "actor_email": actor_email,
            "at": datetime.utcnow().isoformat(),
            "metadata": metadata or {},
        }
    )
    cache["activity_log"] = entries[-40:]
    company.enrichment_cache = cache
    return company


def _should_run_paid_enrichment(company: Company, cache: dict[str, Any], force_paid_refresh: bool) -> tuple[bool, str]:
    if company.domain.endswith(".unknown"):
        return False, "unresolved_domain"

    if force_paid_refresh:
        return True, "forced_refresh"

    if _cache_entry_is_fresh(cache, "apollo_company", _PAID_CACHE_TTL_HOURS) and _cache_entry_is_fresh(cache, "apollo_contacts", _PAID_CACHE_TTL_HOURS):
        return False, "fresh_apollo_cache"

    intent = company.intent_signals if isinstance(company.intent_signals, dict) else {}
    uploaded_intent = float(intent.get("uploaded_intent_score", 0) or 0)
    positive_signals = int(intent.get("positive_signal_count", 0) or 0)
    analyst = {}
    if isinstance(company.enrichment_sources, dict):
        import_block = company.enrichment_sources.get("import")
        if isinstance(import_block, dict):
            analyst = import_block.get("analyst") if isinstance(import_block.get("analyst"), dict) else {}

    analyst_score = float(analyst.get("icp_fit_score", 0) or 0)
    classification = str(analyst.get("classification") or "").strip().lower()

    if classification == "non-target":
        return False, "uploaded_non_target"

    if classification == "target":
        return True, "uploaded_target"

    if (company.icp_score or 0) >= 70:
        return True, "high_icp_score"

    if uploaded_intent >= 6 or positive_signals >= 2:
        return True, "intent_or_signal_threshold"

    if analyst_score >= 6.5:
        return True, "uploaded_icp_threshold"

    if not company.industry or company.employee_count is None:
        return True, "missing_core_firmographics"

    return False, "below_paid_threshold"


async def _contact_coverage_snapshot(company_id: UUID, session: AsyncSession) -> dict[str, int]:
    result = await session.execute(select(Contact).where(Contact.company_id == company_id))
    contacts = result.scalars().all()
    return {
        "total": len(contacts),
        "with_email": sum(1 for contact in contacts if contact.email),
    }


def _should_run_hunter(
    company: Company,
    cache: dict[str, Any],
    contact_coverage: dict[str, int],
    force_paid_refresh: bool,
) -> tuple[bool, str]:
    if company.domain.endswith(".unknown"):
        return False, "unresolved_domain"

    if force_paid_refresh:
        return True, "forced_refresh"

    if _cache_entry_is_fresh(cache, "hunter_contacts", _PAID_CACHE_TTL_HOURS) and _cache_entry_is_fresh(cache, "hunter_company", _PAID_CACHE_TTL_HOURS):
        return False, "fresh_hunter_cache"

    if contact_coverage.get("with_email", 0) >= 3 or contact_coverage.get("total", 0) >= 5:
        return False, "apollo_coverage_sufficient"

    if company.icp_tier in {"hot", "warm"}:
        return True, "priority_account_contact_gap"

    intent = company.intent_signals if isinstance(company.intent_signals, dict) else {}
    uploaded_intent = float(intent.get("uploaded_intent_score", 0) or 0)
    if uploaded_intent >= 6:
        return True, "high_uploaded_intent"

    return False, "low_priority_contact_gap"


def _clean_company_name(raw: str) -> str:
    """
    Sanitize a company name from any external source (xlsx/csv import,
    manual create). Strips embedded newlines, tabs, trailing URLs, and
    parenthesized URL fragments — the kinds of garbage analysts paste
    by accident. Preserves legitimate aliases like "Plex (Rockwell)".
    """
    if not raw:
        return ""
    s = str(raw)
    # Collapse newlines/tabs/multiple spaces.
    s = re.sub(r"[\r\n\t]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Remove a trailing parenthesized URL: " (https://example.com/)" or " (www.x.com)".
    s = re.sub(r"\s*\(\s*(?:https?://|www\.)[^)]*\)\s*$", "", s, flags=re.IGNORECASE).strip()
    # Remove a trailing standalone URL with optional leading dash/em-dash.
    s = re.sub(r"\s*[-–—|]?\s*(?:https?://|www\.)\S+\s*$", "", s, flags=re.IGNORECASE).strip()
    # Drop dangling separator characters left behind after URL stripping.
    s = s.rstrip(" -–—|/.,")
    # Cap length at 200 chars — names beyond that are descriptions, not names.
    if len(s) > 200:
        s = s[:200].rstrip()
    return s


def row_to_company_fields(row: dict[str, str]) -> dict:
    """Map a CSV row to Company field dict."""
    raw_name = _find(row, "name") or ""
    name = _clean_company_name(raw_name) or "Unknown Company"
    domain_raw = _find(row, "domain")
    domain = _clean_domain(domain_raw)
    if not domain:
        domain = f"{_slugify(name)}.unknown"

    fields: dict = {"name": name, "domain": domain}
    import_intelligence = _extract_import_intelligence(row)
    prospecting_intelligence = _extract_prospecting_intelligence(row, import_intelligence)

    industry_raw = _find(row, "industry")
    if not industry_raw:
        industry_raw = _find(row, "category_label")
    if industry_raw:
        last_segment = industry_raw.split(">")[-1].split(",")[0].strip()
        fields["industry"] = last_segment[:120]
    elif category := _find(row, "category_label"):
        inferred_industry = _infer_industry_from_category(category)
        if inferred_industry:
            fields["industry"] = inferred_industry

    emp = _parse_employee_count(_find(row, "employee_count"))
    if emp is not None:
        fields["employee_count"] = emp

    stage = _find(row, "funding_stage")
    if stage:
        fields["funding_stage"] = stage

    funding = _parse_number(_find(row, "total_funding"))
    if funding:
        fields["arr_estimate"] = funding

    desc = _find(row, "description")
    if not desc:
        desc = _find(row, "core_focus")
    if not desc:
        desc = _find(row, "icp_why")
    if not desc:
        desc = _find(row, "what_they_do")
    if desc:
        fields["description"] = desc[:1000]

    extra: dict[str, Any] = {}
    for f in ("country", "city"):
        val = _find(row, f)
        if val:
            extra[f] = val[:500]
    for f in ("region", "headquarters", "category_label", "core_focus", "revenue_funding_label"):
        val = _find(row, f)
        if val:
            extra[f] = val[:500]
    region = _find(row, "region")
    if region:
        fields["region"] = region[:120]
    headquarters = _find(row, "headquarters")
    if headquarters:
        fields["headquarters"] = headquarters[:255]
    if import_intelligence["analyst"]:
        extra["analyst"] = import_intelligence["analyst"]
    if import_intelligence["positive_signals"] or import_intelligence["negative_signals"]:
        extra["uploaded_signals"] = {
            "positive": import_intelligence["positive_signals"],
            "negative": import_intelligence["negative_signals"],
        }
    if import_intelligence["raw_row"]:
        extra["raw_row"] = import_intelligence["raw_row"]
    if extra:
        fields["enrichment_sources"] = {"import": extra}

    uploaded_intent = _derive_uploaded_intent_signals(import_intelligence)
    if any(
        uploaded_intent.get(key)
        for key in ("hiring", "funding", "product", "uploaded_intent_score", "positive_signal_count", "negative_signal_count")
    ):
        fields["intent_signals"] = uploaded_intent
    assigned_ae = import_intelligence["analyst"].get("ae")
    if assigned_ae:
        cleaned_owner = str(assigned_ae).strip()
        fields["assigned_rep"] = cleaned_owner[:255]
        if "@" in cleaned_owner:
            fields["assigned_rep_email"] = _clean_email(cleaned_owner)
        else:
            fields["assigned_rep_name"] = cleaned_owner[:255]

    assigned_sdr = import_intelligence["analyst"].get("sdr")
    if assigned_sdr:
        cleaned_sdr = str(assigned_sdr).strip()
        if "@" in cleaned_sdr:
            fields["sdr_email"] = _clean_email(cleaned_sdr)
        else:
            fields["sdr_name"] = cleaned_sdr[:255]

    if prospecting_intelligence.get("account_thesis"):
        fields["account_thesis"] = str(prospecting_intelligence["account_thesis"])[:4000]
    if prospecting_intelligence.get("why_now"):
        fields["why_now"] = str(prospecting_intelligence["why_now"])[:4000]
    if prospecting_intelligence.get("beacon_angle"):
        fields["beacon_angle"] = str(prospecting_intelligence["beacon_angle"])[:4000]
    if prospecting_intelligence.get("recommended_lane"):
        fields["recommended_outreach_lane"] = str(prospecting_intelligence["recommended_lane"])[:120]

    cleaned_prospecting = {key: value for key, value in prospecting_intelligence.items() if value not in (None, "", [], {})}
    if cleaned_prospecting:
        fields["prospecting_profile"] = cleaned_prospecting
        fields["outreach_plan"] = {
            "recommended_lane": cleaned_prospecting.get("recommended_lane"),
            "owner_email": fields.get("assigned_rep_email"),
            "owner_name": fields.get("assigned_rep_name") or fields.get("assigned_rep"),
            "owner_should_run": "connector_first" if cleaned_prospecting.get("warm_paths") else "direct_outreach",
            "next_best_action": cleaned_prospecting.get("recommended_outreach_strategy") or cleaned_prospecting.get("why_now"),
            "instantly_ready": cleaned_prospecting.get("recommended_lane") in {"event_follow_up", "cold_strategic", "cold_operator"},
        }

    return fields


# ── Tiered Enrichment Pipeline ──────────────────────────────────────────────

_DOMAIN_RESOLUTION_TIMEOUT_SECONDS = 15
_FREE_ENRICHMENT_TIMEOUT_SECONDS = 20
_PAID_ENRICHMENT_TIMEOUT_SECONDS = 30
_AI_SUMMARY_TIMEOUT_SECONDS = 25


def _pipeline_stamp(cache: dict[str, Any], stage: str, status: str, detail: str | None = None) -> None:
    pipeline = cache.get("pipeline") if isinstance(cache.get("pipeline"), dict) else {}
    events = pipeline.get("events") if isinstance(pipeline.get("events"), list) else []
    events.append(
        {
            "stage": stage,
            "status": status,
            "detail": detail,
            "at": datetime.utcnow().isoformat(),
        }
    )
    pipeline["events"] = events[-20:]
    pipeline["current_stage"] = stage
    pipeline["status"] = status
    if detail:
        pipeline["detail"] = detail
    cache["pipeline"] = pipeline


def _set_pipeline_final_status(cache: dict[str, Any], status: str, detail: str | None = None) -> None:
    pipeline = cache.get("pipeline") if isinstance(cache.get("pipeline"), dict) else {}
    pipeline["status"] = status
    pipeline["finished_at"] = datetime.utcnow().isoformat()
    if detail:
        pipeline["detail"] = detail
    cache["pipeline"] = pipeline


def _pipeline_has_degraded_stage(cache: dict[str, Any]) -> bool:
    pipeline = cache.get("pipeline") if isinstance(cache.get("pipeline"), dict) else {}
    events = pipeline.get("events") if isinstance(pipeline.get("events"), list) else []
    degraded_statuses = {"fallback", "timeout", "failed"}
    return any(
        isinstance(event, dict) and str(event.get("status") or "").lower() in degraded_statuses
        for event in events
    )


def _summary_upload_context(company: Company) -> dict[str, Any]:
    import_block = company.enrichment_sources if isinstance(company.enrichment_sources, dict) else {}
    import_block = import_block.get("import") if isinstance(import_block.get("import"), dict) else {}
    analyst = import_block.get("analyst") if isinstance(import_block.get("analyst"), dict) else {}
    prospecting = company.prospecting_profile if isinstance(company.prospecting_profile, dict) else {}
    return {
        "industry": company.industry,
        "description": company.description,
        "core_focus": analyst.get("core_focus"),
        "category": analyst.get("category"),
        "icp_why": analyst.get("icp_why"),
        "intent_why": analyst.get("intent_why"),
        "account_thesis": company.account_thesis or prospecting.get("account_thesis"),
        "why_now": company.why_now or prospecting.get("why_now"),
        "beacon_angle": company.beacon_angle or prospecting.get("beacon_angle"),
        "recommended_outreach_strategy": prospecting.get("recommended_outreach_strategy"),
    }


def _research_quality_snapshot(
    *,
    company: Company,
    cache: dict[str, Any],
    scraped: dict[str, Any],
    intent: dict[str, Any],
    domain_available: bool,
    apollo_data: dict[str, Any] | None,
) -> dict[str, Any]:
    apollo_contacts_entry = cache.get("apollo_contacts") if isinstance(cache.get("apollo_contacts"), dict) else {}
    apollo_contacts = apollo_contacts_entry.get("data") if isinstance(apollo_contacts_entry, dict) else []
    hunter_contacts_entry = cache.get("hunter_contacts") if isinstance(cache.get("hunter_contacts"), dict) else {}
    hunter_contacts_data = hunter_contacts_entry.get("data") if isinstance(hunter_contacts_entry, dict) else {}
    hunter_contacts = hunter_contacts_data.get("contacts") if isinstance(hunter_contacts_data, dict) else []
    hunter_company_entry = cache.get("hunter_company") if isinstance(cache.get("hunter_company"), dict) else {}
    hunter_company = hunter_company_entry.get("data") if isinstance(hunter_company_entry, dict) else None

    website_pages = int(scraped.get("pages_scraped", 0) or 0)
    intent_hits = len(intent.get("raw_results", [])) if isinstance(intent.get("raw_results"), list) else 0
    apollo_contacts_count = len(apollo_contacts) if isinstance(apollo_contacts, list) else 0
    hunter_contacts_count = len(hunter_contacts) if isinstance(hunter_contacts, list) else 0

    evidence_score = 0
    evidence_score += 2 if domain_available else 0
    evidence_score += 2 if website_pages > 0 else 0
    evidence_score += 1 if intent_hits > 0 else 0
    evidence_score += 2 if isinstance(apollo_data, dict) and bool(apollo_data) else 0
    evidence_score += 1 if isinstance(hunter_company, dict) and bool(hunter_company) else 0
    evidence_score += 2 if apollo_contacts_count > 0 else 0
    evidence_score += 1 if hunter_contacts_count > 0 else 0

    if evidence_score >= 6:
        evidence_level = "strong"
    elif evidence_score >= 3:
        evidence_level = "partial"
    else:
        evidence_level = "thin"

    live_research_sources = [
        name
        for name, active in (
            ("website", website_pages > 0),
            ("web_search", intent_hits > 0),
            ("apollo_company", isinstance(apollo_data, dict) and bool(apollo_data)),
            ("apollo_contacts", apollo_contacts_count > 0),
            ("hunter_company", isinstance(hunter_company, dict) and bool(hunter_company)),
            ("hunter_contacts", hunter_contacts_count > 0),
        )
        if active
    ]

    return {
        "domain_available": domain_available,
        "website_pages_scraped": website_pages,
        "intent_result_count": intent_hits,
        "apollo_company_found": isinstance(apollo_data, dict) and bool(apollo_data),
        "apollo_contacts_found": apollo_contacts_count,
        "hunter_company_found": isinstance(hunter_company, dict) and bool(hunter_company),
        "hunter_contacts_found": hunter_contacts_count,
        "live_research_sources": live_research_sources,
        "evidence_score": evidence_score,
        "evidence_level": evidence_level,
        "allow_ai_summary": evidence_level != "thin",
        "summary_note": (
            "Live research evidence is thin; avoid treating AI summarization as grounded research."
            if evidence_level == "thin"
            else "Live research captured enough evidence for AI synthesis."
        ),
    }

async def enrich_company_tiered(
    company_id: UUID,
    session: AsyncSession,
    *,
    force_paid_refresh: bool = False,
) -> Company | None:
    """
    Run the full tiered enrichment pipeline for a single company.
    Free sources run in parallel; paid providers are gated by score, cache, and
    contact coverage so we keep throughput high without overspending.
    """
    company = await session.get(Company, company_id)
    if not company:
        logger.warning(f"enrich_company_tiered: company {company_id} not found")
        return None

    # JSONB is not mutation-tracked by default; always work on a deep copy so
    # assignment marks the field dirty and persists new cache content.
    cache: dict = copy.deepcopy(company.enrichment_cache or {})
    summary_context = _summary_upload_context(company)
    _pipeline_stamp(cache, "start", "started", "Beginning enrichment pipeline")
    domain_available = not company.domain.endswith(".unknown")

    # Resolve .unknown domain first, but do not block the whole company if it fails.
    if not domain_available:
        from app.services.domain_resolver import resolve_and_update_domain

        _pipeline_stamp(cache, "domain_resolution", "started", f"Resolving domain for {company.name}")
        resolved = False
        resolution_detail = None
        try:
            resolved = await asyncio.wait_for(
                resolve_and_update_domain(company, session),
                timeout=_DOMAIN_RESOLUTION_TIMEOUT_SECONDS,
            )
            if resolved:
                company = await session.get(Company, company_id)
                domain_available = bool(company and not company.domain.endswith(".unknown"))
                resolution_detail = f"Resolved domain to {company.domain}" if company else "Resolved domain"
            else:
                resolution_detail = "Domain unresolved; continuing with company-name-only enrichment"
        except asyncio.TimeoutError:
            resolution_detail = "Domain resolution timed out; continuing without a resolved domain"
            logger.warning(f"Domain resolution timed out for '{company.name}'")
        except Exception as exc:
            resolution_detail = f"Domain resolution failed: {safe_error_message(exc)}"
            logger.warning(
                "Domain resolution failed for '%s': %s",
                company.name,
                safe_error_message(exc),
            )

        _pipeline_stamp(
            cache,
            "domain_resolution",
            "resolved" if domain_available else "fallback",
            resolution_detail,
        )

    # The cache doubles as a pipeline log for the UI: each stage writes status and
    # fetched payloads so partial failures remain explainable to the user.

    # ── Tier 1: Free sources ────────────────────────────────────────────────
    from app.clients.web_search import WebSearchClient
    ws = WebSearchClient()
    scraped: dict[str, Any] = {"text": "", "pages_scraped": 0}
    intent: dict[str, Any] = {}

    free_research_mode = "website + company-name signals" if domain_available else "company-name signals only"
    _pipeline_stamp(cache, "free_research", "started", f"Collecting {free_research_mode}")
    try:
        scraped_result, intent_result = await asyncio.wait_for(
            asyncio.gather(
                ws.scrape_company_pages(company.domain) if domain_available else asyncio.sleep(0, result={"text": "", "pages_scraped": 0}),
                ws.search_intent_signals(company.name, company.domain),
                return_exceptions=True,
            ),
            timeout=_FREE_ENRICHMENT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        scraped_result = {"text": "", "pages_scraped": 0}
        intent_result = {}
        _pipeline_stamp(cache, "free_research", "timeout", "Free research timed out; continuing with uploaded context")
    else:
        _pipeline_stamp(cache, "free_research", "completed", f"Collected {free_research_mode}")

    if isinstance(scraped_result, Exception):
        logger.error(
            "Website scrape failed for %s: %s",
            company.domain,
            safe_error_message(scraped_result),
        )
    else:
        scraped = scraped_result
        cache["web_scrape"] = {"data": scraped, "fetched_at": datetime.utcnow().isoformat()}

    if isinstance(intent_result, Exception):
        logger.error(
            "Intent signal search failed for %s: %s",
            company.name,
            safe_error_message(intent_result),
        )
    else:
        intent = intent_result
        cache["intent_signals"] = {"data": intent, "fetched_at": datetime.utcnow().isoformat()}
        existing_intent = copy.deepcopy(company.intent_signals or {})
        company.intent_signals = {
            "hiring": max(int(existing_intent.get("hiring", 0) or 0), len(intent.get("hiring") or [])),
            "funding": max(int(existing_intent.get("funding", 0) or 0), len(intent.get("funding") or [])),
            "product": max(int(existing_intent.get("product", 0) or 0), len(intent.get("product") or [])),
            "uploaded_intent_score": existing_intent.get("uploaded_intent_score"),
            "uploaded_fit_type": existing_intent.get("uploaded_fit_type"),
            "uploaded_classification": existing_intent.get("uploaded_classification"),
            "uploaded_confidence": existing_intent.get("uploaded_confidence"),
            "positive_signal_count": existing_intent.get("positive_signal_count"),
            "negative_signal_count": existing_intent.get("negative_signal_count"),
            "uploaded_signals": existing_intent.get("uploaded_signals"),
            "details": intent,
        }

    company.icp_score, company.icp_tier = score_company(company)

    should_run_paid, paid_reason = _should_run_paid_enrichment(company, cache, force_paid_refresh)
    cache["cost_controls"] = {
        "data": {
            "domain_available": domain_available,
            "research_mode": free_research_mode,
            "paid_enrichment": "allowed" if should_run_paid else "skipped",
            "paid_reason": paid_reason,
        },
        "fetched_at": datetime.utcnow().isoformat(),
    }
    cache["contact_discovery"] = {
        "data": {
            "mode": "paused",
            "message": "Contact discovery during company research is temporarily paused. Upload prospects from the Prospecting page to attach stakeholders.",
        },
        "fetched_at": datetime.utcnow().isoformat(),
    }

    cached_apollo_entry = cache.get("apollo_company") if isinstance(cache.get("apollo_company"), dict) else None
    apollo_data = cached_apollo_entry.get("data") if isinstance(cached_apollo_entry, dict) else None

    # Paid enrichment is intentionally gated behind the free pass so domain
    # resolution and scraped context can improve match quality first.
    # ── Tier 2: Apollo (paid, gated) ────────────────────────────────────────
    if should_run_paid:
        _pipeline_stamp(cache, "paid_enrichment", "started", f"Paid enrichment allowed: {paid_reason}")
        from app.clients.apollo import ApolloClient
        apollo = ApolloClient()

        run_apollo_company = force_paid_refresh or not _cache_entry_is_fresh(cache, "apollo_company", _PAID_CACHE_TTL_HOURS)

        apollo_company_result = None
        if run_apollo_company:
            try:
                apollo_company_result = await asyncio.wait_for(
                    apollo.enrich_company(company.domain),
                    timeout=_PAID_ENRICHMENT_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                apollo_company_result = None
                logger.warning(f"Apollo enrichment timed out for {company.name} ({company.domain})")
                _pipeline_stamp(cache, "paid_enrichment", "timeout", "Apollo enrichment timed out; continuing")

        if isinstance(apollo_company_result, Exception):
            logger.error(
                "Apollo company enrichment failed for %s: %s",
                company.domain,
                safe_error_message(apollo_company_result),
            )
        elif apollo_company_result:
            apollo_data = apollo_company_result
            cache["apollo_company"] = {"data": apollo_data, "fetched_at": datetime.utcnow().isoformat()}
            _apply_apollo(company, apollo_data)
            logger.info(f"Apollo enriched company: {company.domain}")
        cache["apollo_contacts"] = {
            "data": [],
            "fetched_at": datetime.utcnow().isoformat(),
            "paused": True,
            "message": "Apollo contact discovery is temporarily paused during company enrichment.",
        }

        # ── Tier 3: Hunter.io (paid — fallback only) ───────────────────────
        contact_coverage = await _contact_coverage_snapshot(company.id, session)
        should_run_hunter, hunter_reason = _should_run_hunter(company, cache, contact_coverage, force_paid_refresh)
        cache["cost_controls"]["data"]["hunter"] = "allowed" if should_run_hunter else "skipped"
        cache["cost_controls"]["data"]["hunter_reason"] = hunter_reason

        if should_run_hunter:
            from app.clients.hunter import HunterClient
            hunter = HunterClient()

            run_hunter_company = force_paid_refresh or not _cache_entry_is_fresh(cache, "hunter_company", _PAID_CACHE_TTL_HOURS)

            hunter_company_result = None
            if run_hunter_company:
                try:
                    hunter_company_result = await asyncio.wait_for(
                        hunter.company_enrichment(company.domain),
                        timeout=_PAID_ENRICHMENT_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    hunter_company_result = None
                    logger.warning(f"Hunter enrichment timed out for {company.name} ({company.domain})")
                    _pipeline_stamp(cache, "paid_enrichment", "timeout", "Hunter enrichment timed out; continuing")

            cache["hunter_contacts"] = {
                "data": {"contacts": []},
                "fetched_at": datetime.utcnow().isoformat(),
                "paused": True,
                "message": "Hunter contact discovery is temporarily paused during company enrichment.",
            }

            if isinstance(hunter_company_result, Exception):
                logger.error(
                    "Hunter company enrichment failed for %s: %s",
                    company.domain,
                    safe_error_message(hunter_company_result),
                )
            elif hunter_company_result:
                cache["hunter_company"] = {"data": hunter_company_result, "fetched_at": datetime.utcnow().isoformat()}
                logger.info(f"Hunter enriched company: {company.domain}")
        _pipeline_stamp(cache, "paid_enrichment", "completed", "Finished paid enrichment stage")
    else:
        _pipeline_stamp(cache, "paid_enrichment", "skipped", paid_reason)

    # Email verification is capped to a few top contacts to add confidence
    # signals without turning every batch into an expensive verification run.
    # Gated behind should_run_paid so a low-priority / fresh-cache company that
    # skipped Apollo/Hunter doesn't still spend Hunter verify credits. A manual
    # force sets should_run_paid via _should_run_paid_enrichment(forced_refresh),
    # so the re-verify-everything path stays available on demand.
    # ── Email verification for top contacts (Hunter) ──────────────────────
    if should_run_paid:
        await _verify_top_contact_emails(company.id, session, cache, force=force_paid_refresh)
    else:
        _pipeline_stamp(cache, "email_verification", "skipped", paid_reason)

    research_quality = _research_quality_snapshot(
        company=company,
        cache=cache,
        scraped=scraped,
        intent=intent,
        domain_available=domain_available,
        apollo_data=apollo_data if isinstance(apollo_data, dict) else None,
    )
    cache["research_quality"] = {
        "data": research_quality,
        "fetched_at": datetime.utcnow().isoformat(),
    }

    # ── AI Tier: Claude summarization ───────────────────────────────────────
    from app.clients.claude_enrichment import summarize_company
    _pipeline_stamp(cache, "ai_summary", "started", "Generating AI summary")
    if not research_quality["allow_ai_summary"]:
        cache["ai_summary"] = {
            "data": {
                "_source": "skipped_thin_research",
                "confidence": 0,
                "description": company.description or "",
                "industry": company.industry or "Unknown",
                "research_quality": research_quality,
                "note": research_quality["summary_note"],
            },
            "fetched_at": datetime.utcnow().isoformat(),
        }
        _pipeline_stamp(cache, "ai_summary", "fallback", str(research_quality["summary_note"]))
    else:
        try:
            summary = await asyncio.wait_for(
                summarize_company(
                    scraped_data=scraped,
                    apollo_data=apollo_data,
                    search_results=intent,
                    company_name=company.name,
                    domain=company.domain,
                    upload_context=summary_context,
                ),
                timeout=_AI_SUMMARY_TIMEOUT_SECONDS,
            )
            if summary is None:
                logger.warning(f"AI summary unavailable for {company.name}")
                _pipeline_stamp(cache, "ai_summary", "unavailable", "AI summary returned None — no API key or analysis failed")
            else:
                summary_source = summary.get("_source", "unknown")
                summary["research_quality"] = research_quality

                if summary.get("description"):
                    company.description = summary["description"]
                if summary.get("industry") and summary["industry"] != "Unknown":
                    company.industry = summary["industry"]
                # Apply tech stack if discovered by AI
                if summary.get("tech_stack_signals") and not company.tech_stack:
                    company.tech_stack = summary["tech_stack_signals"]

                cache["ai_summary"] = {"data": summary, "fetched_at": datetime.utcnow().isoformat()}

                logger.info(f"AI summary generated for {company.name} from source={summary_source} with {len(summary)} fields")
                _pipeline_stamp(cache, "ai_summary", "completed", f"AI summary source={summary_source}")
        except asyncio.TimeoutError:
            logger.error(f"Claude summarization timed out for {company.name}")
            _pipeline_stamp(cache, "ai_summary", "timeout", "AI summary timed out")
        except Exception as e:
            logger.error("Claude summarization failed for %s: %s", company.name, safe_error_message(e))
            _pipeline_stamp(cache, "ai_summary", "failed", safe_error_message(e))

    # ── Committee coverage & prospecting priorities ─────────────────────────
    _pipeline_stamp(cache, "committee_analysis", "started", "Building committee coverage and priorities")
    try:
        committee_coverage = await _build_committee_coverage(company, session)
        cache["committee_coverage"] = {
            "data": committee_coverage,
            "fetched_at": datetime.utcnow().isoformat(),
        }
        cache["prospecting_priorities"] = {
            "data": _build_prospecting_priorities(company, committee_coverage, intent),
            "fetched_at": datetime.utcnow().isoformat(),
        }
        _pipeline_stamp(cache, "committee_analysis", "completed", "Built committee coverage and prospecting priorities")
    except Exception as e:
        logger.error(
            "Committee coverage analysis failed for %s: %s",
            company.name,
            safe_error_message(e),
        )
        _pipeline_stamp(cache, "committee_analysis", "failed", safe_error_message(e))

    # ── Persist ─────────────────────────────────────────────────────────────
    contacts = (
        await session.execute(select(Contact).where(Contact.company_id == company.id))
    ).scalars().all()
    refresh_company_prospecting_fields(company, contacts)
    for contact in contacts:
        refresh_contact_sequence_plan(contact, company)
        session.add(contact)
    final_status = "completed_partial" if _pipeline_has_degraded_stage(cache) else "completed"
    final_detail = "Completed with resolved domain" if domain_available else "Completed without resolved domain"
    _set_pipeline_final_status(cache, final_status, final_detail)
    company.enrichment_cache = cache
    company.enriched_at = datetime.utcnow()
    company.icp_score, company.icp_tier = score_company(company)
    company.updated_at = datetime.utcnow()

    # Force-write JSONB cache in case ORM dirty tracking misses nested JSON changes.
    # Nested dict/list edits do not always mark the ORM field dirty automatically.
    await session.execute(
        sa_update(Company)
        .where(Company.id == company.id)
        .values(enrichment_cache=cache)
    )

    company.enrichment_cache = cache
    session.add(company)
    await session.commit()
    await session.refresh(company)
    return company


async def re_enrich_company(company_id: UUID, session: AsyncSession) -> Company | None:
    """Re-run the standard tiered pipeline. Always executes (no cache check)."""
    return await enrich_company_tiered(company_id, session, force_paid_refresh=True)


async def re_enrich_contact_service(contact_id: UUID, session: AsyncSession) -> Contact | None:
    """Re-enrich a single contact via Apollo people/match."""
    contact = await session.get(Contact, contact_id)
    if not contact:
        return None

    from app.clients.apollo import ApolloClient
    apollo = ApolloClient()

    # Get company domain for enrichment
    domain = ""
    if contact.company_id:
        company = await session.get(Company, contact.company_id)
        if company:
            domain = company.domain

    try:
        person = await apollo.enrich_person(
            email=contact.email or "",
            first_name=contact.first_name,
            last_name=contact.last_name,
            domain=domain,
        )
        if person:
            if person.get("title"):
                contact.title = person["title"]
            if person.get("seniority"):
                contact.seniority = person["seniority"]
            if person.get("email"):
                contact.email = person["email"]
            if person.get("linkedin_url"):
                contact.linkedin_url = person["linkedin_url"]
            if person.get("phone"):
                contact.phone = person["phone"]
            existing_enrichment = contact.enrichment_data if isinstance(contact.enrichment_data, dict) else {}
            raw_row = existing_enrichment.get("raw_row") if isinstance(existing_enrichment.get("raw_row"), dict) else None
            contact.enrichment_data = {
                **existing_enrichment,
                **person,
                **({"raw_row": raw_row} if raw_row else {}),
            }
            contact.sequence_status = "ready" if contact.email else "research_needed"
            contact.instantly_status = "ready" if contact.email else "missing_email"
    except Exception as e:
        logger.error("Contact re-enrich failed for %s: %s", contact_id, safe_error_message(e))

    # Classify persona
    from app.clients.claude_enrichment import classify_contact_persona
    try:
        company_ctx = ""
        if contact.company_id:
            company = await session.get(Company, contact.company_id)
            if company:
                company_ctx = f"{company.name} - {company.industry or 'Unknown'}"
        contact.persona_type = await classify_contact_persona(
            contact.title or "", contact.seniority, company_ctx
        )
        contact.persona = _canonical_persona(contact.persona, contact.persona_type)
    except Exception as e:
        logger.error("Persona classification failed for %s: %s", contact_id, safe_error_message(e))

    if contact.company_id:
        company = await session.get(Company, contact.company_id)
        if company:
            refresh_contact_sequence_plan(contact, company)

    contact.enriched_at = datetime.utcnow()
    contact.updated_at = datetime.utcnow()
    session.add(contact)
    await session.commit()
    await session.refresh(contact)

    if contact.company_id:
        company = await session.get(Company, contact.company_id)
        if company:
            company_contacts = (
                await session.execute(select(Contact).where(Contact.company_id == company.id))
            ).scalars().all()
            refresh_company_prospecting_fields(company, company_contacts)
            company.updated_at = datetime.utcnow()
            session.add(company)
            await session.commit()
    return contact




async def process_batch(batch_id: UUID, session: AsyncSession) -> SourcingBatch | None:
    """Process all companies in a sourcing batch through tiered enrichment."""
    batch = await session.get(SourcingBatch, batch_id)
    if not batch:
        return None

    batch.status = "processing"
    batch.updated_at = datetime.utcnow()
    session.add(batch)
    await session.commit()

    # Get all companies in this batch
    result = await session.execute(
        select(Company).where(Company.sourcing_batch_id == batch_id)
    )
    companies = result.scalars().all()

    processed = 0
    failed = int(batch.failed_rows or 0)
    total_companies = len(companies)
    for index, company in enumerate(companies, start=1):
        try:
            # Give each company a fresh session so one failure or stale ORM state
            # does not poison the rest of the batch.
            logger.info(f"Batch {batch_id}: starting company {index}/{total_companies} -> {company.name} ({company.domain})")
            async with AsyncSessionLocal() as company_session:
                await enrich_company_tiered(company.id, company_session)
            logger.info(f"Batch {batch_id}: finished company {index}/{total_companies} -> {company.name}")
        except Exception as e:
            logger.error("Batch enrichment failed for %s: %s", company.name, safe_error_message(e))
            errors = batch.error_log or []
            errors.append({"company": company.name, "error": safe_error_message(e)})
            batch.error_log = errors
            failed += 1

        processed = index
        batch.processed_rows = processed
        batch.failed_rows = failed
        batch.updated_at = datetime.utcnow()
        session.add(batch)
        await session.commit()

    batch.status = "failed" if failed == total_companies and total_companies > 0 else "completed"
    batch.updated_at = datetime.utcnow()
    session.add(batch)
    await session.commit()
    await session.refresh(batch)
    return batch


# ── Helpers ────────────────────────────────────────────────────────────────────

def _apply_apollo(company: Company, data: dict) -> None:
    """Write Apollo fields onto the company only when the value is non-None."""
    scalar_fields = [
        "name", "industry", "vertical", "employee_count",
        "arr_estimate", "funding_stage", "has_dap", "dap_tool",
    ]
    for field in scalar_fields:
        value = data.get(field)
        if value is not None:
            setattr(company, field, value)


async def _create_contacts(company: Company, contacts_data: list[dict], session: AsyncSession) -> int:
    """Create contact records from Apollo search results, skipping duplicates.
    Returns count of newly created contacts."""
    from app.clients.claude_enrichment import classify_contact_persona

    created = 0
    for c in contacts_data:
        try:
            if not is_priority_stakeholder_candidate(c):
                continue
            company_warm_paths = (
                company.prospecting_profile.get("warm_paths")
                if isinstance(company.prospecting_profile, dict)
                and isinstance(company.prospecting_profile.get("warm_paths"), list)
                else []
            )
            best_warm_path = company_warm_paths[0] if company_warm_paths else None
            uploaded_company_row = _company_import_raw_row(company)
            email = (c.get("email") or "").strip() or None
            first = (c.get("first_name") or "").strip()
            last = (c.get("last_name") or "").strip()

            if not first and not last:
                continue

            # Email is the strongest dedupe key when present because it remains
            # stable across title changes and repeated imports.
            if email:
                existing = await session.execute(
                    select(Contact).where(func.lower(Contact.email) == email.strip().lower()).limit(1)
                )
                if existing.scalars().first():
                    continue

            # Fall back to name + company when the provider has not given us an
            # email address yet.
            if first and last:
                existing = await session.execute(
                    select(Contact).where(
                        Contact.company_id == company.id,
                        Contact.first_name == first,
                        Contact.last_name == last,
                    ).limit(1)
                )
                if existing.scalars().first():
                    continue

            contact = Contact(
                first_name=first,
                last_name=last,
                email=email,
                title=(c.get("title") or None),
                seniority=(c.get("seniority") or None),
                linkedin_url=(c.get("linkedin_url") or None),
                phone=(c.get("phone") or None),
                company_id=company.id,
                enriched_at=datetime.utcnow(),
                enrichment_data=c,
                assigned_rep_email=company.assigned_rep_email,
                outreach_lane=company.recommended_outreach_lane,
                sequence_status="ready" if email else "research_needed",
                instantly_status="ready" if email else "missing_email",
                warm_intro_strength=_parse_int(str(best_warm_path.get("strength") or "")) if isinstance(best_warm_path, dict) else None,
                warm_intro_path=best_warm_path if isinstance(best_warm_path, dict) else None,
                conversation_starter=company.prospecting_profile.get("conversation_starter") if isinstance(company.prospecting_profile, dict) else None,
                personalization_notes=company.why_now,
                talking_points=(company.outreach_plan or {}).get("next_best_action") and [str((company.outreach_plan or {}).get("next_best_action"))] or None,
            )
            contact.enrichment_data = {
                **(contact.enrichment_data if isinstance(contact.enrichment_data, dict) else {}),
                **({"raw_row": uploaded_company_row} if uploaded_company_row else {}),
                "source_company_context": {
                    "recommended_outreach_strategy": company.prospecting_profile.get("recommended_outreach_strategy") if isinstance(company.prospecting_profile, dict) else None,
                    "warm_paths": company_warm_paths,
                    "account_thesis": company.account_thesis,
                    "why_now": company.why_now,
                    "beacon_angle": company.beacon_angle,
                },
            }

            # Classify persona
            try:
                company_ctx = f"{company.name} - {company.industry or 'Unknown'}"
                contact.persona_type = await classify_contact_persona(
                    contact.title or "", contact.seniority, company_ctx
                )
                contact.persona = _canonical_persona(contact.persona, contact.persona_type)
            except Exception:
                from app.services.persona_classifier import classify_persona
                contact.persona = classify_persona(contact)

            refresh_contact_sequence_plan(contact, company)
            session.add(contact)
            created += 1

        except Exception as e:
            logger.warning("Skipping contact %s %s: %s", first, last, safe_error_message(e))
            continue

    if created:
        await session.commit()
    return created


_SENIORITY_RANK = {
    "c_suite": 1, "executive": 1, "vp": 2, "director": 3,
    "senior": 4, "manager": 5, "entry": 6,
}
_MAX_VERIFY_EMAILS = 5
# Re-verifying an email returns the same Hunter result for weeks but bills a
# credit each time. Skip any contact verified within this window regardless of
# outcome (deliverable OR undeliverable), so known-bad emails aren't re-checked
# forever. Mirrors the paid-cache TTL (14 days).
_EMAIL_VERIFY_TTL_HOURS = _PAID_CACHE_TTL_HOURS


def _email_verification_is_fresh(contact: Contact, ttl_hours: int) -> bool:
    """True when this contact's email was Hunter-verified within the TTL.

    Reads the 'checked_at' stamp this function writes into
    enrichment_data['email_verification']; absent on contacts last verified
    before this guard existed, so they get re-checked exactly once and then
    stamped going forward.
    """
    enrich = contact.enrichment_data if isinstance(contact.enrichment_data, dict) else {}
    verification = enrich.get("email_verification")
    if not isinstance(verification, dict):
        return False
    checked_at = verification.get("checked_at")
    if not isinstance(checked_at, str):
        return False
    try:
        checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if checked.tzinfo is not None:
        checked = checked.replace(tzinfo=None)
    return datetime.utcnow() - checked <= timedelta(hours=ttl_hours)


async def _verify_top_contact_emails(
    company_id: UUID, session: AsyncSession, cache: dict[str, Any], force: bool = False
) -> None:
    """
    Verify email deliverability for the top contacts (by seniority) at a company
    using Hunter.io's email-verifier. Updates contact.email_verified and
    enrichment_data.confidence. Capped at 5 contacts to control API costs.

    Each verification is stamped with 'checked_at'; contacts verified within
    _EMAIL_VERIFY_TTL_HOURS are skipped (even known-bad ones) unless force=True,
    so a company re-enrich doesn't re-bill Hunter for emails it just checked.
    """
    from app.clients.hunter import HunterClient
    hunter = HunterClient()
    if hunter.mock:
        return  # No API key — skip verification

    result = await session.execute(
        select(Contact)
        .where(Contact.company_id == company_id)
        .where(Contact.email.isnot(None))
        .where(Contact.email_verified == False)
    )
    contacts = list(result.scalars().all())
    if not contacts:
        return

    # Sort by seniority (C-suite first), then take top N
    contacts.sort(key=lambda c: _SENIORITY_RANK.get(c.seniority or "", 6))
    candidates = contacts[:_MAX_VERIFY_EMAILS]

    # Skip contacts already verified within the TTL (regardless of result) so
    # undeliverable addresses aren't re-billed on every re-enrich.
    if force:
        to_verify = candidates
        skipped = 0
    else:
        to_verify = [
            c for c in candidates
            if not _email_verification_is_fresh(c, _EMAIL_VERIFY_TTL_HOURS)
        ]
        skipped = len(candidates) - len(to_verify)

    if not to_verify:
        cache["email_verification"] = {
            "data": {
                "verified": 0,
                "checked": 0,
                "skipped_fresh": skipped,
                "total_contacts_with_email": len(contacts),
            },
            "fetched_at": datetime.utcnow().isoformat(),
        }
        return

    verified_count = 0
    for contact in to_verify:
        try:
            verification = await hunter.verify_email(contact.email)
            if not verification:
                continue

            is_deliverable = verification.get("result") == "deliverable"
            score = verification.get("score", 0)
            contact.email_verified = is_deliverable

            # Stamp when we checked so the TTL guard can skip this contact on the
            # next re-enrich whether or not the email turned out deliverable.
            verification["checked_at"] = datetime.utcnow().isoformat()

            # Store verification details in enrichment_data
            enrich = contact.enrichment_data if isinstance(contact.enrichment_data, dict) else {}
            enrich["email_verification"] = verification
            enrich["confidence"] = score
            contact.enrichment_data = enrich

            if is_deliverable:
                verified_count += 1
        except Exception as e:
            logger.warning(
                "Email verification failed for %s: %s",
                contact.email,
                safe_error_message(e),
            )
            continue

    await session.commit()
    cache["email_verification"] = {
        "data": {
            "verified": verified_count,
            "checked": len(to_verify),
            "skipped_fresh": skipped,
            "total_contacts_with_email": len(contacts),
        },
        "fetched_at": datetime.utcnow().isoformat(),
    }
    logger.info(
        f"Email verification: {verified_count}/{len(to_verify)} deliverable "
        f"({skipped} skipped fresh) for company {company_id}"
    )
