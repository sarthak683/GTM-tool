from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.models.deal import MEDDPICC_FIELDS

MEDDPICC_CHANGE_REASONS = frozenset({"empty_field", "material_refinement", "contradiction"})
MEDDPICC_DIRECT_COMPETITOR_TAGS = frozenset({"direct_competitor", "competitor_named"})


def get_meddpicc_snapshot(qualification: Any) -> dict[str, int]:
    qualification_dict = qualification if isinstance(qualification, dict) else {}
    meddpicc = qualification_dict.get("meddpicc") if isinstance(qualification_dict.get("meddpicc"), dict) else {}
    return {field: int(meddpicc.get(field, 0) or 0) for field in MEDDPICC_FIELDS}


def get_meddpicc_details(qualification: Any) -> dict[str, dict[str, Any]]:
    qualification_dict = qualification if isinstance(qualification, dict) else {}
    details = qualification_dict.get("meddpicc_details")
    if not isinstance(details, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in details.items():
        if key in MEDDPICC_FIELDS and isinstance(value, dict):
            normalized[key] = dict(value)
    return normalized


def get_meddpicc_detail(qualification: Any, field: str) -> dict[str, Any]:
    return dict(get_meddpicc_details(qualification).get(field) or {})


def detail_has_capture(detail: dict[str, Any] | None) -> bool:
    if not isinstance(detail, dict):
        return False
    if isinstance(detail.get("summary"), str) and detail["summary"].strip():
        return True
    contact = detail.get("contact")
    if isinstance(contact, dict):
        if isinstance(contact.get("name"), str) and contact["name"].strip():
            return True
        if isinstance(contact.get("email"), str) and contact["email"].strip():
            return True
    for key in ("tags", "entities"):
        value = detail.get(key)
        if isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value):
            return True
    return False


def detail_updated_at(detail: dict[str, Any] | None) -> datetime | None:
    if not isinstance(detail, dict):
        return None
    raw = detail.get("updated_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def field_has_capture(qualification: Any, field: str) -> bool:
    snapshot = get_meddpicc_snapshot(qualification)
    if snapshot.get(field, 0) > 0:
        return True
    return detail_has_capture(get_meddpicc_detail(qualification, field))


def field_updated_within_days(
    qualification: Any,
    field: str,
    days: int,
    now: datetime | None = None,
) -> bool:
    updated_at = detail_updated_at(get_meddpicc_detail(qualification, field))
    if updated_at is None:
        return False
    now = now or datetime.utcnow()
    return updated_at >= now - timedelta(days=days)


def competition_has_direct_competitor(qualification: Any) -> bool:
    detail = get_meddpicc_detail(qualification, "competition")
    tags = detail.get("tags")
    if not isinstance(tags, list):
        return False
    normalized = {
        str(tag).strip().lower()
        for tag in tags
        if isinstance(tag, str) and tag.strip()
    }
    return bool(normalized & MEDDPICC_DIRECT_COMPETITOR_TAGS)
