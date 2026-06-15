"""The six canonical AI task codes.

The emitter is bounded: if a signal doesn't fit one of these, the AI stays
silent. Five of the six are LLM-proposed CRM-hygiene updates; T-CRITICAL
is the only "do something" code and it's produced by deterministic rules,
not the LLM.
"""
from __future__ import annotations

from typing import Literal


TaskCode = Literal[
    "T-STAGE",     # Move deal to a new stage
    "T-AMOUNT",    # Update deal value
    "T-CLOSE",     # Re-anchor expected close date
    "T-MEDPICC",   # Fill a specific MEDDPICC field
    "T-CONTACT",   # Add / update a stakeholder
    "T-CRITICAL",  # High-stakes action genuinely overdue
]

TASK_CODES: frozenset[str] = frozenset(
    ["T-STAGE", "T-AMOUNT", "T-CLOSE", "T-MEDPICC", "T-CONTACT", "T-CRITICAL"]
)

LLM_CODES: frozenset[str] = frozenset(
    ["T-STAGE", "T-AMOUNT", "T-CLOSE", "T-MEDPICC", "T-CONTACT"]
)


# Code → Task.recommended_action (the value persisted into the DB and
# dispatched by apply_task_action). Kept as a separate map so the LLM never
# writes an action name directly — it writes a code and we translate.
CODE_TO_ACTION: dict[str, str] = {
    "T-STAGE": "t_stage_apply",
    "T-AMOUNT": "t_amount_apply",
    "T-CLOSE": "t_close_apply",
    "T-MEDPICC": "t_medpicc_apply",
    "T-CONTACT": "t_contact_apply",
    "T-CRITICAL": "t_critical_apply",
}

ACTION_TO_CODE: dict[str, str] = {v: k for k, v in CODE_TO_ACTION.items()}


def track_for_code(code: str) -> str:
    """Which queue this task belongs to — critical is its own band."""
    if code == "T-CRITICAL":
        return "critical"
    if code in LLM_CODES:
        return "sales_ai"
    return "hygiene"
