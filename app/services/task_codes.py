"""The six canonical AI task codes.

The emitter is bounded: if a signal doesn't fit one of these, the AI stays
silent. Five of the six are LLM-proposed CRM-hygiene updates; T-CRITICAL is the
only "do something" code and it's produced by deterministic rules, not the LLM.

This file used to hold the six codes THREE times over — a `TaskCode` Literal, a
`TASK_CODES` frozenset, and `CODE_TO_ACTION` — of which only `CODE_TO_ACTION`
and `LLM_CODES` were ever read. The other two were hand-maintained copies with
nothing keeping them in step, so they are gone and the per-code notes now live
on the map that is actually used.
"""
from __future__ import annotations


# Code → Task.recommended_action (the value persisted into the DB and
# dispatched by apply_task_action). Kept as a separate map so the LLM never
# writes an action name directly — it writes a code and we translate.
CODE_TO_ACTION: dict[str, str] = {
    "T-STAGE": "t_stage_apply",        # Move deal to a new stage
    "T-AMOUNT": "t_amount_apply",      # Update deal value
    "T-CLOSE": "t_close_apply",        # Re-anchor expected close date
    "T-MEDPICC": "t_medpicc_apply",    # Fill a specific MEDDPICC field
    "T-CONTACT": "t_contact_apply",    # Add / update a stakeholder
    "T-CRITICAL": "t_critical_apply",  # High-stakes action genuinely overdue
}

# The five the LLM may propose. Derived from CODE_TO_ACTION rather than
# restated, so adding a code above cannot leave this behind.
LLM_CODES: frozenset[str] = frozenset(CODE_TO_ACTION) - {"T-CRITICAL"}


def track_for_code(code: str) -> str:
    """Which queue this task belongs to — critical is its own band."""
    if code == "T-CRITICAL":
        return "critical"
    if code in LLM_CODES:
        return "sales_ai"
    return "hygiene"
