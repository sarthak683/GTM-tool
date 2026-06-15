from __future__ import annotations

from dataclasses import dataclass

INACTIVE_STAGE_MOVE_BLOCKS = frozenset(
    {
        "poc_agreed",
        "poc_done",
        "commercial_negotiation",
        "msa_review",
        "workshop",
        "closed_won",
    }
)


@dataclass(frozen=True)
class StagePlaybook:
    stage: str
    objective: str
    blocked_system_keys: frozenset[str]
    blocked_stage_moves: frozenset[str] = frozenset()


PLAYBOOKS: dict[str, StagePlaybook] = {
    "reprospect": StagePlaybook(
        stage="reprospect",
        objective="Re-open a cold or previously-worked account and earn a fresh reply.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_pricing_pushback",
                "deal_competitor_risk",
            }
        ),
    ),
    "demo_scheduled": StagePlaybook(
        stage="demo_scheduled",
        objective="Protect the demo and make sure the right people show up prepared.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_follow_up",
                "deal_pricing_pushback",
                "deal_competitor_risk",
            }
        ),
        blocked_stage_moves=frozenset(
            {
                "commercial_negotiation",
                "msa_review",
                "workshop",
            }
        ),
    ),
    "demo_done": StagePlaybook(
        stage="demo_done",
        objective="Turn the demo into clear qualification and a crisp next step.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_follow_up",
                "deal_pricing_pushback",
                "deal_competitor_risk",
            }
        ),
        blocked_stage_moves=frozenset(
            {
                "commercial_negotiation",
                "msa_review",
                "workshop",
            }
        ),
    ),
    "qualified_lead": StagePlaybook(
        stage="qualified_lead",
        objective="Build stakeholder, technical, and commercial confidence before a POC is agreed.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_follow_up",
                "deal_pricing_pushback",
            }
        ),
        blocked_stage_moves=frozenset(
            {
                "commercial_negotiation",
                "msa_review",
                "workshop",
            }
        ),
    ),
    "poc_agreed": StagePlaybook(
        stage="poc_agreed",
        objective="Turn the verbal POC yes into a signed NDA and a tightly scoped kickoff.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_follow_up",
                "deal_pricing_pushback",
            }
        ),
        blocked_stage_moves=frozenset(
            {
                "commercial_negotiation",
                "msa_review",
                "workshop",
            }
        ),
    ),
    "poc_wip": StagePlaybook(
        stage="poc_wip",
        objective="Keep the POC moving, remove blockers fast, and prove value against clear criteria.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_follow_up",
                "deal_pricing_pushback",
            }
        ),
        blocked_stage_moves=frozenset(
            {
                "commercial_negotiation",
                "msa_review",
                "workshop",
            }
        ),
    ),
    "poc_done": StagePlaybook(
        stage="poc_done",
        objective="Turn proven POC value into a fast commercial readout and next-step commitment.",
        blocked_system_keys=frozenset(
            {
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_follow_up",
            }
        ),
        blocked_stage_moves=frozenset(
            {
                "msa_review",
                "workshop",
            }
        ),
    ),
    "commercial_negotiation": StagePlaybook(
        stage="commercial_negotiation",
        objective="Close scope and commercials, then move cleanly into legal and implementation planning.",
        blocked_system_keys=frozenset(
            {
                "deal_follow_up",
                "deal_pricing_pushback",
            }
        ),
        blocked_stage_moves=frozenset({"msa_review"}),
    ),
    "msa_review": StagePlaybook(
        stage="msa_review",
        objective="Turn workshop and legal review into signed paper without reopening scope or pricing.",
        blocked_system_keys=frozenset({"deal_send_pricing"}),
    ),
    "workshop": StagePlaybook(
        stage="workshop",
        objective="Align implementation motion and close legal without reopening the deal commercially.",
        blocked_system_keys=frozenset({"deal_send_pricing"}),
    ),
    "closed_won": StagePlaybook(
        stage="closed_won",
        objective="Drive a clean internal handoff, kickoff, and invoicing path after signature.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_follow_up",
                "deal_pricing_pushback",
                "deal_competitor_risk",
            }
        ),
    ),
    "churned": StagePlaybook(
        stage="churned",
        objective="Capture the churn learnings cleanly and reopen only when there is real future-fit evidence.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_follow_up",
                "deal_pricing_pushback",
                "deal_competitor_risk",
            }
        ),
        blocked_stage_moves=INACTIVE_STAGE_MOVE_BLOCKS,
    ),
    "not_a_fit": StagePlaybook(
        stage="not_a_fit",
        objective="Close out the disqualification cleanly without clogging the pipeline.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_follow_up",
                "deal_pricing_pushback",
                "deal_competitor_risk",
            }
        ),
        blocked_stage_moves=INACTIVE_STAGE_MOVE_BLOCKS,
    ),
    "cold": StagePlaybook(
        stage="cold",
        objective="Keep the account warm at low cadence and only reopen on a real trigger or engagement.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_pricing_pushback",
                "deal_competitor_risk",
            }
        ),
        blocked_stage_moves=INACTIVE_STAGE_MOVE_BLOCKS,
    ),
    "closed_lost": StagePlaybook(
        stage="closed_lost",
        objective="Capture the honest loss reason and set up a disciplined future revisit if it still fits.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_follow_up",
                "deal_pricing_pushback",
                "deal_competitor_risk",
            }
        ),
        blocked_stage_moves=INACTIVE_STAGE_MOVE_BLOCKS,
    ),
    "on_hold": StagePlaybook(
        stage="on_hold",
        objective="Keep the deal paused with a clear revisit date and only reopen on timing or trigger evidence.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_pricing_pushback",
                "deal_competitor_risk",
            }
        ),
        blocked_stage_moves=INACTIVE_STAGE_MOVE_BLOCKS,
    ),
    "nurture": StagePlaybook(
        stage="nurture",
        objective="Stay useful over time and reopen only when timing or engagement materially improves.",
        blocked_system_keys=frozenset(
            {
                "deal_send_pricing",
                "deal_book_workshop",
                "deal_add_security_contact",
                "deal_follow_up",
                "deal_pricing_pushback",
                "deal_competitor_risk",
            }
        ),
        blocked_stage_moves=INACTIVE_STAGE_MOVE_BLOCKS,
    ),
}


def get_stage_playbook(stage: str | None) -> StagePlaybook | None:
    if not stage:
        return None
    return PLAYBOOKS.get(stage)


def stage_allows_system_key(stage: str | None, system_key: str) -> bool:
    playbook = get_stage_playbook(stage)
    if playbook is None:
        return True
    return system_key not in playbook.blocked_system_keys


def stage_allows_stage_move(stage: str | None, target_stage: str) -> bool:
    playbook = get_stage_playbook(stage)
    if playbook is None:
        return True
    return target_stage not in playbook.blocked_stage_moves
