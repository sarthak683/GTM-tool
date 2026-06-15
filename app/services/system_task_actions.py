from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ExecutionMode = Literal["accept_only"]


@dataclass(frozen=True)
class SystemTaskActionSpec:
    action: str
    category: str
    execution_mode: ExecutionMode = "accept_only"


_ACTION_SPECS = [
    SystemTaskActionSpec("move_deal_stage", "deal_progress"),
    SystemTaskActionSpec("convert_contact_to_deal", "deal_progress"),
    SystemTaskActionSpec("attach_contact_to_deal", "crm_hygiene"),
    SystemTaskActionSpec("create_contact_and_attach_to_deal", "crm_hygiene"),
    SystemTaskActionSpec("re_enrich_company", "enrichment"),
    SystemTaskActionSpec("refresh_icp_research", "enrichment"),
    SystemTaskActionSpec("re_enrich_contact", "enrichment"),
    SystemTaskActionSpec("send_pricing_package", "deal_follow_up"),
    SystemTaskActionSpec("book_workshop_session", "deal_follow_up"),
    SystemTaskActionSpec("retry_deal_call", "deal_follow_up"),
    SystemTaskActionSpec("follow_up_deal_voicemail", "deal_follow_up"),
    SystemTaskActionSpec("send_deal_call_recap", "deal_follow_up"),
    SystemTaskActionSpec("send_meeting_follow_up", "deal_follow_up"),
    SystemTaskActionSpec("follow_up_buyer_thread", "deal_follow_up"),
    SystemTaskActionSpec("retry_contact_call", "prospect_follow_up"),
    SystemTaskActionSpec("follow_up_voicemail", "prospect_follow_up"),
    SystemTaskActionSpec("send_contact_call_recap", "prospect_follow_up"),
    SystemTaskActionSpec("draft_reply_follow_up", "prospect_follow_up"),
    SystemTaskActionSpec("draft_open_follow_up", "prospect_follow_up"),
    SystemTaskActionSpec("book_call_from_interest", "prospect_follow_up"),
    SystemTaskActionSpec("mark_contact_unsubscribed", "prospect_hygiene"),
    SystemTaskActionSpec("close_not_interested_contact", "prospect_hygiene"),
    # AI task emitter (T-STAGE, T-AMOUNT, T-CLOSE, T-MEDPICC, T-CONTACT, T-CRITICAL).
    SystemTaskActionSpec("t_stage_apply", "sales_ai"),
    SystemTaskActionSpec("t_amount_apply", "sales_ai"),
    SystemTaskActionSpec("t_close_apply", "sales_ai"),
    SystemTaskActionSpec("t_medpicc_apply", "sales_ai"),
    SystemTaskActionSpec("t_contact_apply", "sales_ai"),
    SystemTaskActionSpec("t_critical_apply", "critical"),
]

SYSTEM_TASK_ACTION_SPECS = {spec.action: spec for spec in _ACTION_SPECS}


def get_system_task_action_spec(action: str | None) -> SystemTaskActionSpec | None:
    if not action:
        return None
    return SYSTEM_TASK_ACTION_SPECS.get(action)
