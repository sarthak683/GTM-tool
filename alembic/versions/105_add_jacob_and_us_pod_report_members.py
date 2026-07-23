"""Add Jacob and US pod report members

Revision ID: 105
Revises: 104_multi_gmail_connections
Create Date: 2026-07-23
"""

from __future__ import annotations

import json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "105"
down_revision = "104_multi_gmail_connections"
branch_labels = None
depends_on = None

JACOB_EMAIL = "jacob@beacon.li"
JACOB_NAME = "Jacob"
US_POD_DEFAULT_RECIPIENTS = [
    "awinja@beacon.li",
    "jacob@beacon.li",
    "sehar@beacon.li",
    "rakesh@beacon.li",
    "shahruk@beacon.li",
    "pravalika@beacon.li",
    "mahesh@beacon.li",
    "pulkit@beacon.li",
    "sarthak@beacon.li",
    "maithili@beacon.li",
    "manognya@beacon.li",
]
US_POD_REPORT_RECIPIENTS = ["jacob@beacon.li", "awinja@beacon.li"]


def _append_unique_emails(existing: object, emails: list[str]) -> list[str]:
    values = existing if isinstance(existing, list) else []
    cleaned: list[str] = []
    for item in [*values, *emails]:
        email = str(item or "").strip().lower()
        if email and "@" in email and email not in cleaned:
            cleaned.append(email)
    return cleaned


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO users (id, email, name, google_id, role, is_active, created_at, updated_at)
            VALUES (:id, :email, :name, :google_id, 'sdr', true, NOW(), NOW())
            ON CONFLICT (email) DO UPDATE
            SET
                role = 'sdr',
                is_active = true,
                name = CASE
                    WHEN users.name IS NULL OR users.name = '' THEN EXCLUDED.name
                    ELSE users.name
                END,
                updated_at = NOW()
            """
        ),
        {
            "id": str(uuid4()),
            "email": JACOB_EMAIL,
            "name": JACOB_NAME,
            "google_id": f"seed_{JACOB_EMAIL}",
        },
    )

    rows = bind.execute(
        sa.text("SELECT id, sync_schedule_settings FROM workspace_settings")
    ).mappings()
    for row in rows:
        settings = row["sync_schedule_settings"] or {}
        if isinstance(settings, str):
            settings = json.loads(settings)
        if not isinstance(settings, dict):
            settings = {}
        sales_report = settings.get("sales_report")
        if not isinstance(sales_report, dict):
            sales_report = {}
        fallback = US_POD_DEFAULT_RECIPIENTS if not sales_report.get("recipients") else US_POD_REPORT_RECIPIENTS
        sales_report["recipients"] = _append_unique_emails(sales_report.get("recipients"), fallback)
        settings["sales_report"] = sales_report
        bind.execute(
            sa.text(
                "UPDATE workspace_settings SET sync_schedule_settings = CAST(:settings AS JSON) WHERE id = :id"
            ),
            {"id": row["id"], "settings": json.dumps(settings)},
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, sync_schedule_settings FROM workspace_settings")
    ).mappings()
    for row in rows:
        settings = row["sync_schedule_settings"] or {}
        if isinstance(settings, str):
            settings = json.loads(settings)
        if not isinstance(settings, dict):
            continue
        sales_report = settings.get("sales_report")
        if not isinstance(sales_report, dict):
            continue
        recipients = sales_report.get("recipients")
        if isinstance(recipients, list):
            remove = set(US_POD_REPORT_RECIPIENTS)
            sales_report["recipients"] = [
                str(item).strip().lower()
                for item in recipients
                if str(item or "").strip().lower() not in remove
            ]
            settings["sales_report"] = sales_report
            bind.execute(
                sa.text(
                    "UPDATE workspace_settings SET sync_schedule_settings = CAST(:settings AS JSON) WHERE id = :id"
                ),
                {"id": row["id"], "settings": json.dumps(settings)},
            )

    bind.execute(sa.text("DELETE FROM users WHERE email = :email AND google_id = :google_id"), {
        "email": JACOB_EMAIL,
        "google_id": f"seed_{JACOB_EMAIL}",
    })
