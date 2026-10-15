"""Reason note for manual prospect status changes.

Moving a prospect INTO not_the_right_prospect / reach_out_later / dnd from the
manual status control requires a written reason. Automated writers (call
disposition, LinkedIn status, sequence steps) send other fields alongside
account_status and derive their own status, so the rule only bites when the
request carries nothing but the status (+ reason) — the manual control.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

CONTACT_REASON_REQUIRED_STATUSES = ("not_the_right_prospect", "reach_out_later", "dnd")
CONTACT_STATUS_REASON_MIN_LEN = 5
_MANUAL_KEYS = {"account_status", "account_status_reason"}


def resolve_contact_status_reason(update_data: dict, previous_status: Optional[str]) -> Optional[str]:
    """Validate/normalise the reason in ``update_data`` (mutated in place).

    Returns the reason to log when a status change carried one, else None.
    """
    if "account_status" not in update_data:
        if "account_status_reason" in update_data:
            update_data["account_status_reason"] = (update_data.get("account_status_reason") or "").strip() or None
        return None
    new_status = update_data.get("account_status")
    raw = (update_data.get("account_status_reason") or "").strip()
    changed = new_status != previous_status
    if new_status in CONTACT_REASON_REQUIRED_STATUSES and changed:
        manual = set(update_data.keys()) <= _MANUAL_KEYS
        if manual and len(raw) < CONTACT_STATUS_REASON_MIN_LEN:
            raise HTTPException(
                status_code=422,
                detail=f"A reason (at least {CONTACT_STATUS_REASON_MIN_LEN} characters) is required to move a prospect to this status.",
            )
        update_data["account_status_reason"] = raw or None
        return raw or None
    if new_status not in CONTACT_REASON_REQUIRED_STATUSES:
        update_data["account_status_reason"] = None
        return None
    update_data["account_status_reason"] = raw or None
    return None


def status_reason_note(status: Optional[str], reason: str) -> str:
    return f"Status moved to {(status or '').replace('_', ' ')} — reason: {reason}"
