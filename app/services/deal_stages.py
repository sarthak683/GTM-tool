from __future__ import annotations

from typing import Any

from sqlmodel import select

from app.models.settings import WorkspaceSettings


DEFAULT_DEAL_STAGE_SETTINGS: list[dict[str, Any]] = [
    {"id": "reprospect", "label": "REPROSPECT", "group": "active", "color": "#8b5cf6"},
    {"id": "demo_scheduled", "label": "4.DEMO SCHEDULED", "group": "active", "color": "#4f6ddf"},
    {"id": "demo_done", "label": "5.DEMO DONE", "group": "active", "color": "#1d4ed8"},
    {"id": "qualified_lead", "label": "6.QUALIFIED LEAD", "group": "active", "color": "#6d5efc"},
    {"id": "poc_agreed", "label": "7.POC AGREED", "group": "active", "color": "#0ea5e9"},
    {"id": "poc_wip", "label": "8.POC WIP", "group": "active", "color": "#06b6d4"},
    {"id": "poc_done", "label": "9.POC DONE", "group": "active", "color": "#14b8a6"},
    {"id": "commercial_negotiation", "label": "10.COMMERCIAL NEGOTIATION", "group": "active", "color": "#f59e0b"},
    {"id": "msa_review", "label": "11.WORKSHOP/MSA", "group": "active", "color": "#a855f7"},
    {"id": "closed_won", "label": "12.CLOSED WON", "group": "closed", "color": "#22c55e"},
    {"id": "churned", "label": "CHURNED", "group": "closed", "color": "#ef4444"},
    {"id": "not_a_fit", "label": "NOT FIT", "group": "closed", "color": "#9ca3af"},
    {"id": "cold", "label": "COLD", "group": "closed", "color": "#94a3b8"},
    {"id": "closed_lost", "label": "CLOSED LOST", "group": "closed", "color": "#7c8da4"},
    {"id": "on_hold", "label": "ON HOLD - REVISIT LATER", "group": "closed", "color": "#7c3aed"},
    {"id": "nurture", "label": "NURTURE - FUTURE FIT", "group": "closed", "color": "#2dd4bf"},
    {"id": "closed", "label": "CLOSED", "group": "closed", "color": "#64748b"},
]


def normalize_deal_stage_settings(value: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        return [dict(item) for item in DEFAULT_DEAL_STAGE_SETTINGS]

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        stage_id = str(raw.get("id") or "").strip()
        if stage_id == "open":
            stage_id = "reprospect"
        if not stage_id or stage_id in seen:
            continue
        seen.add(stage_id)
        group = str(raw.get("group") or "active").strip().lower()
        normalized.append(
            {
                "id": stage_id,
                "label": str(raw.get("label") or stage_id).strip() or stage_id,
                "group": "closed" if group == "closed" else "active",
                "color": str(raw.get("color") or "#94a3b8").strip() or "#94a3b8",
            }
        )

    if not normalized:
        return [dict(item) for item in DEFAULT_DEAL_STAGE_SETTINGS]
    return normalized


async def get_configured_deal_stages(session) -> list[dict[str, Any]]:
    row = await session.get(WorkspaceSettings, 1)
    if row is None:
        return [dict(item) for item in DEFAULT_DEAL_STAGE_SETTINGS]
    return normalize_deal_stage_settings(row.deal_stage_settings)


async def get_configured_deal_stage_ids(session) -> list[str]:
    return [stage["id"] for stage in await get_configured_deal_stages(session)]


async def get_configured_default_deal_stage(session) -> str:
    stages = await get_configured_deal_stages(session)
    first_active = next((stage["id"] for stage in stages if stage["group"] == "active"), None)
    return first_active or "reprospect"


async def get_or_create_workspace_settings_row(session):
    row = await session.get(WorkspaceSettings, 1)
    if row is None:
        row = WorkspaceSettings(id=1, deal_stage_settings=[dict(item) for item in DEFAULT_DEAL_STAGE_SETTINGS])
        session.add(row)
        await session.commit()
        await session.refresh(row)
    elif not row.deal_stage_settings:
        row.deal_stage_settings = [dict(item) for item in DEFAULT_DEAL_STAGE_SETTINGS]
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


def filter_funnel_config_to_stage_ids(config: dict[str, list[str]] | None, allowed_stage_ids: list[str], fallback: dict[str, list[str]]) -> dict[str, list[str]]:
    allowed = set(allowed_stage_ids)
    return {
        "active": [stage for stage in (config or {}).get("active", fallback.get("active", [])) if stage in allowed] or [stage for stage in fallback.get("active", []) if stage in allowed],
        "inactive": [stage for stage in (config or {}).get("inactive", fallback.get("inactive", [])) if stage in allowed] or [stage for stage in fallback.get("inactive", []) if stage in allowed],
        "tofu": [stage for stage in (config or {}).get("tofu", fallback.get("tofu", [])) if stage in allowed] or [stage for stage in fallback.get("tofu", []) if stage in allowed],
        "mofu": [stage for stage in (config or {}).get("mofu", fallback.get("mofu", [])) if stage in allowed] or [stage for stage in fallback.get("mofu", []) if stage in allowed],
        "bofu": [stage for stage in (config or {}).get("bofu", fallback.get("bofu", [])) if stage in allowed] or [stage for stage in fallback.get("bofu", []) if stage in allowed],
        "visible_cards": list((config or {}).get("visible_cards") or fallback.get("visible_cards", [])),
    }
