"""T-CRITICAL rule engine.

The one task code the LLM is *not* allowed to produce. These are
deterministic circuit breakers: a real deadline has been missed.

Threshold tuning lives in CRITICAL_RULES — change the numbers here without
touching any other file. Each rule is a pure function
(deal, activities, contacts, now) -> CriticalFinding | None, so adding a
rule is one entry in the list.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable

from app.models.activity import Activity
from app.models.contact import Contact
from app.models.deal import DEAL_STAGES, Deal
from app.services.meddpicc_updates import competition_has_direct_competitor, field_has_capture


# ── Tuneable thresholds (days) ──────────────────────────────────────────────
# Change a number here and the next refresh uses it. No migration, no redeploy
# of the LLM prompt. Keep rule_ids stable — they're persisted in system_key.

THRESHOLDS: dict[str, int] = {
    "nda_unsigned_days": 5,
    "proposal_unanswered_days": 5,
    "no_workshop_after_commercials_days": 7,
    "champion_silent_in_poc_days": 10,
    "stalled_in_stage_days": 21,         # deal sitting in any active stage with no activity
    "msa_review_no_legal_contact_days": 4,
}

STAGE_INDEX = {stage: idx for idx, stage in enumerate(DEAL_STAGES)}


@dataclass(frozen=True)
class CriticalFinding:
    rule_id: str
    severity: str            # "high" | "medium"
    title: str
    description: str
    deadline_missed_at: datetime
    evidence_activity_id: str | None = None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _contains(text: str, terms: Iterable[str]) -> bool:
    lowered = (text or "").lower()
    return any(term in lowered for term in terms)


def _latest(activities: list[Activity], predicate: Callable[[Activity], bool]) -> Activity | None:
    for activity in activities:  # activities are pre-sorted newest-first
        if predicate(activity):
            return activity
    return None


def _age_days(moment: datetime | None, now: datetime) -> float | None:
    if moment is None:
        return None
    return (now - moment).total_seconds() / 86400.0


def _stage_at_or_beyond(current_stage: str | None, target_stage: str) -> bool:
    if not current_stage:
        return False
    return STAGE_INDEX.get(current_stage, -1) >= STAGE_INDEX.get(target_stage, 999)


# ── Individual rule evaluators ──────────────────────────────────────────────

def _rule_nda_unsigned(deal, activities, contacts, now) -> CriticalFinding | None:
    nda_sent = _latest(
        activities,
        lambda a: a.type == "email" and _contains(
            f"{a.email_subject or ''} {a.content or ''}",
            ["nda", "non-disclosure", "non disclosure"],
        )
        and _contains(
            f"{a.email_subject or ''} {a.content or ''}",
            ["sent", "attached", "please sign", "for signature", "docusign"],
        ),
    )
    if not nda_sent:
        return None
    age = _age_days(nda_sent.created_at, now)
    if age is None or age < THRESHOLDS["nda_unsigned_days"]:
        return None
    signed = _latest(
        activities,
        lambda a: a.created_at >= nda_sent.created_at
        and _contains(f"{a.email_subject or ''} {a.content or ''}", ["signed", "executed", "countersigned"]),
    )
    if signed:
        return None
    return CriticalFinding(
        rule_id="nda_unsigned",
        severity="high",
        title="NDA unsigned — overdue",
        description=(
            f"NDA was sent {int(age)} days ago and no signed/executed confirmation has come back. "
            "Chase the buyer directly or escalate through legal."
        ),
        deadline_missed_at=nda_sent.created_at + timedelta(days=THRESHOLDS["nda_unsigned_days"]),
        evidence_activity_id=str(nda_sent.id) if nda_sent.id else None,
    )


def _rule_proposal_unanswered(deal, activities, contacts, now) -> CriticalFinding | None:
    proposal = _latest(
        activities,
        lambda a: a.type == "email" and _contains(
            f"{a.email_subject or ''} {a.content or ''}",
            ["proposal", "pricing", "quote", "commercial terms"],
        )
        and _contains(
            f"{a.email_subject or ''} {a.content or ''}",
            ["attached", "sent", "please find", "see attached", "for your review"],
        ),
    )
    if not proposal:
        return None
    age = _age_days(proposal.created_at, now)
    if age is None or age < THRESHOLDS["proposal_unanswered_days"]:
        return None
    reply = _latest(
        activities,
        lambda a: a.created_at > proposal.created_at and a.type in {"email", "call", "meeting"},
    )
    if reply:
        return None
    return CriticalFinding(
        rule_id="proposal_unanswered",
        severity="high",
        title="Proposal unanswered — overdue",
        description=(
            f"Proposal/pricing was shared {int(age)} days ago with no buyer reply, call, or meeting since. "
            "Drive a response before the deal cools."
        ),
        deadline_missed_at=proposal.created_at + timedelta(days=THRESHOLDS["proposal_unanswered_days"]),
        evidence_activity_id=str(proposal.id) if proposal.id else None,
    )


def _rule_no_workshop_after_commercials(deal, activities, contacts, now) -> CriticalFinding | None:
    if deal.stage != "commercial_negotiation":
        return None
    commercials_entered = deal.stage_entered_at
    age = _age_days(commercials_entered, now)
    if age is None or age < THRESHOLDS["no_workshop_after_commercials_days"]:
        return None
    workshop_signal = _latest(
        activities,
        lambda a: a.created_at >= commercials_entered
        and _contains(
            f"{a.email_subject or ''} {a.content or ''}",
            ["workshop", "working session", "technical deep dive", "implementation session"],
        ),
    )
    if workshop_signal:
        return None
    return CriticalFinding(
        rule_id="no_workshop_after_commercials",
        severity="high",
        title="No workshop scheduled after commercials agreed",
        description=(
            f"Deal has been in commercial negotiation for {int(age)} days and there's no workshop motion "
            "on the thread. Lock in the technical session before momentum stalls."
        ),
        deadline_missed_at=commercials_entered + timedelta(days=THRESHOLDS["no_workshop_after_commercials_days"]),
    )


def _rule_champion_silent_in_poc(deal, activities, contacts, now) -> CriticalFinding | None:
    if deal.stage not in {"poc_agreed", "poc_wip"}:
        return None
    champion_emails = {
        (c.email or "").lower().strip()
        for c in contacts
        if (c.persona_type or "").lower() == "champion" and c.email
    }
    if not champion_emails:
        return None
    champion_inbound = _latest(
        activities,
        lambda a: a.type == "email"
        and (a.email_from or "").lower().strip() in champion_emails,
    )
    last_contact = champion_inbound.created_at if champion_inbound else deal.stage_entered_at
    age = _age_days(last_contact, now)
    if age is None or age < THRESHOLDS["champion_silent_in_poc_days"]:
        return None
    return CriticalFinding(
        rule_id="champion_silent_in_poc",
        severity="high",
        title="Champion silent during POC",
        description=(
            f"Champion hasn't engaged on email for {int(age)} days while the deal is in POC. "
            "Re-engage directly or broaden the stakeholder map before the POC drifts."
        ),
        deadline_missed_at=(last_contact or now) + timedelta(days=THRESHOLDS["champion_silent_in_poc_days"]),
    )


def _rule_stalled_in_stage(deal, activities, contacts, now) -> CriticalFinding | None:
    active_stages = {
        "demo_scheduled", "demo_done", "qualified_lead", "poc_agreed",
        "poc_wip", "poc_done", "commercial_negotiation", "msa_review", "workshop",
    }
    if deal.stage not in active_stages:
        return None
    last_touch = deal.last_activity_at or deal.stage_entered_at
    age = _age_days(last_touch, now)
    if age is None or age < THRESHOLDS["stalled_in_stage_days"]:
        return None
    return CriticalFinding(
        rule_id="stalled_in_stage",
        severity="medium",
        title=f"Deal stalled in {deal.stage.replace('_', ' ')}",
        description=(
            f"No activity on this deal for {int(age)} days while sitting in an active stage. "
            "Either move it forward, park it, or mark it not-a-fit — don't let it rot on the board."
        ),
        deadline_missed_at=(last_touch or now) + timedelta(days=THRESHOLDS["stalled_in_stage_days"]),
    )


def _rule_msa_review_no_legal_contact(deal, activities, contacts, now) -> CriticalFinding | None:
    if deal.stage != "msa_review":
        return None
    age = _age_days(deal.stage_entered_at, now)
    if age is None or age < THRESHOLDS["msa_review_no_legal_contact_days"]:
        return None
    legal_present = any(
        _contains(
            " ".join(filter(None, [c.title, c.persona, c.persona_type])),
            ["legal", "counsel", "procurement", "compliance", "security"],
        )
        for c in contacts
    )
    if legal_present:
        return None
    return CriticalFinding(
        rule_id="msa_review_no_legal_contact",
        severity="high",
        title="MSA review without legal/procurement stakeholder",
        description=(
            f"Deal entered MSA review {int(age)} days ago with no legal, procurement, or security contact attached. "
            "Map the right stakeholder before the paper process stalls."
        ),
        deadline_missed_at=(deal.stage_entered_at or now) + timedelta(days=THRESHOLDS["msa_review_no_legal_contact_days"]),
    )


def _rule_missing_paper_process(deal, activities, contacts, now) -> CriticalFinding | None:
    if deal.stage not in {"commercial_negotiation", "msa_review", "workshop"}:
        return None
    if field_has_capture(deal.qualification, "paper_process"):
        return None
    return CriticalFinding(
        rule_id="missing_paper_process",
        severity="high",
        title="Commercial deal missing paper process",
        description=(
            "The deal is already in commercial/legal motion but Beacon has no paper-process detail captured yet. "
            "Map procurement, legal, security, or onboarding steps before the deal gets stuck late."
        ),
        deadline_missed_at=deal.stage_entered_at or now,
    )


def _rule_direct_competitor_live(deal, activities, contacts, now) -> CriticalFinding | None:
    if not _stage_at_or_beyond(deal.stage, "qualified_lead"):
        return None
    if deal.stage in {"closed_won", "closed_lost", "not_a_fit", "cold", "on_hold", "nurture", "churned", "closed"}:
        return None
    if not competition_has_direct_competitor(deal.qualification):
        return None
    return CriticalFinding(
        rule_id="direct_competitor_live",
        severity="high",
        title="Direct competitor active in the deal",
        description=(
            "Beacon captured a named direct competitor on an active qualified deal. "
            "Tighten positioning and confirm why Beacon wins before the buyer frame hardens."
        ),
        deadline_missed_at=deal.stage_entered_at or now,
    )


CRITICAL_RULES: list[Callable[..., CriticalFinding | None]] = [
    _rule_nda_unsigned,
    _rule_proposal_unanswered,
    _rule_no_workshop_after_commercials,
    _rule_champion_silent_in_poc,
    _rule_stalled_in_stage,
    _rule_msa_review_no_legal_contact,
    _rule_missing_paper_process,
    _rule_direct_competitor_live,
]


def evaluate_critical_rules(
    deal: Deal,
    activities: list[Activity],
    contacts: list[Contact],
    now: datetime | None = None,
) -> list[CriticalFinding]:
    now = now or datetime.utcnow()
    findings: list[CriticalFinding] = []
    for rule in CRITICAL_RULES:
        try:
            result = rule(deal, activities, contacts, now)
        except Exception:
            result = None
        if result is not None:
            findings.append(result)
    return findings
