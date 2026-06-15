"""Merge legacy OPEN deal stage into REPROSPECT

Revision ID: 038_merge_open_into_reprospect
Revises: 037_deal_stage_settings
Create Date: 2026-04-04 00:30:00.000000
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "038_merge_open_into_reprospect"
down_revision = "037_deal_stage_settings"
branch_labels = None
depends_on = None


def _normalize_stage_settings(raw_value):
    if not isinstance(raw_value, list):
        return raw_value

    normalized = []
    seen = set()
    for item in raw_value:
        if not isinstance(item, dict):
            continue
        stage_id = str(item.get("id") or "").strip()
        if stage_id == "open":
            stage_id = "reprospect"
        if not stage_id or stage_id in seen:
            continue
        seen.add(stage_id)
        next_item = dict(item)
        next_item["id"] = stage_id
        if stage_id == "reprospect" and (next_item.get("label") or "").strip().upper() == "OPEN":
            next_item["label"] = "REPROSPECT"
        normalized.append(next_item)
    return normalized


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE deals SET stage = 'reprospect' WHERE stage = 'open'"))

    rows = bind.execute(sa.text("SELECT id, deal_stage_settings FROM workspace_settings")).fetchall()
    for row_id, raw_settings in rows:
        normalized = _normalize_stage_settings(raw_settings)
        if normalized == raw_settings:
            continue
        bind.execute(
            sa.text("UPDATE workspace_settings SET deal_stage_settings = :settings WHERE id = :row_id"),
            {"settings": json.dumps(normalized), "row_id": row_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE deals SET stage = 'open' WHERE stage = 'reprospect'"))
