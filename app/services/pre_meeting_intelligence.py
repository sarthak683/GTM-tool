"""
Pre-Meeting Intelligence Service.

Orchestrates ALL available research sources for a sales meeting:
  1.  Company profile from DB (ICP, tech stack, funding stage, industry)
  2.  Company background (own website scraped + GPT-4o summary)
  3.  Deep website analysis (pricing, careers, customers pages)
  4.  DuckDuckGo recent news (funding rounds, launches, PR)
  5.  Company milestones (history, founding events)
  6.  Intent signals (hiring, funding, product/growth — via DuckDuckGo)
  7.  Google News RSS feed (latest headlines)
  8.  Hunter.io contact discovery + email pattern
  9.  Hunter.io company enrichment (firmographics)
  10. Competitive landscape (DuckDuckGo competitor search)
  11. Stakeholder profiles (contacts linked to company, enriched from DB)
  12. Relevant battlecards (matched by industry/vertical)
  13. GPT-4o executive briefing (synthesises all data into actionable prep)

Result is stored in meeting.research_data (JSONB) and meeting.demo_strategy (Text).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.log_safety import safe_error_message

logger = logging.getLogger(__name__)

_COMMITTEE_ROLE_LABELS = {
    "economic_buyer": "Economic Buyer",
    "champion": "Champion",
    "technical_evaluator": "Technical Evaluator",
    "implementation_owner": "Implementation Owner",
}

_ROLE_FOCUS = {
    "economic_buyer": "ROI, time-to-value, deployment risk, and executive visibility.",
    "champion": "Operational pain, internal adoption friction, and proof they can reuse internally.",
    "technical_evaluator": "Architecture fit, integrations, security, and rollout feasibility.",
    "implementation_owner": "Project effort, change management, and admin burden during rollout.",
    "unknown": "Role clarity, current process pain, and where Beacon would slot into the rollout.",
}

_ROLE_TALK_TRACK = {
    "economic_buyer": "Anchor the conversation on failed rollouts, slower time-to-value, and how Beacon reduces deployment drag.",
    "champion": "Give them a clear before/after story they can share internally and use to win sponsorship.",
    "technical_evaluator": "Show how Beacon works with the existing stack instead of forcing a rip-and-replace motion.",
    "implementation_owner": "Focus on workflow orchestration, ownership, approvals, and how Beacon reduces manual coordination.",
    "unknown": "Use discovery to understand where they sit in the buying process before jumping into product depth.",
}

_ROLE_QUESTION_BANK = {
    "economic_buyer": [
        "What rollout delays or adoption gaps are most expensive for the business right now?",
        "How do you measure time-to-value after a new SaaS deployment goes live?",
    ],
    "champion": [
        "Where does implementation coordination break down most often today?",
        "What internal friction do you hit when trying to drive adoption across teams?",
    ],
    "technical_evaluator": [
        "Which systems would Beacon need to orchestrate or read from first?",
        "What integration, security, or governance constraints matter most in evaluation?",
    ],
    "implementation_owner": [
        "Who owns the rollout plan today and where does work fall between teams?",
        "What tasks are still manual when a deployment moves from plan to execution?",
    ],
    "unknown": [
        "What does a successful deployment look like for your team this quarter?",
        "Where do projects tend to stall after purchase but before adoption is real?",
    ],
}

_IMPLEMENTATION_OWNER_KEYWORDS = [
    "ops", "operations", "admin", "administrator", "systems", "enablement",
    "implementation", "program", "project", "revops", "hris", "people ops",
    "people operations", "business systems",
]


def _canonical_persona(persona: str | None, persona_type: str | None = None) -> str:
    mapping = {
        "buyer": "economic_buyer",
        "economic_buyer": "economic_buyer",
        "champion": "champion",
        "evaluator": "technical_evaluator",
        "technical_evaluator": "technical_evaluator",
        "blocker": "unknown",
        "unknown": "unknown",
    }
    for candidate in (persona, persona_type):
        normalized = mapping.get((candidate or "").strip().lower())
        if normalized:
            return normalized
    return "unknown"


def _infer_committee_role(
    title: str | None,
    persona: str | None,
    persona_type: str | None = None,
) -> str:
    title_lower = (title or "").strip().lower()
    if any(keyword in title_lower for keyword in _IMPLEMENTATION_OWNER_KEYWORDS):
        return "implementation_owner"

    canonical = _canonical_persona(persona, persona_type)
    if canonical in _COMMITTEE_ROLE_LABELS:
        return canonical
    return "unknown"


def _stakeholder_priority_score(stakeholder: dict[str, Any]) -> int:
    role = _infer_committee_role(
        stakeholder.get("title"),
        stakeholder.get("persona"),
        stakeholder.get("persona_type"),
    )
    score = {
        "economic_buyer": 40,
        "champion": 35,
        "technical_evaluator": 32,
        "implementation_owner": 28,
        "unknown": 18,
    }.get(role, 18)

    seniority = (stakeholder.get("seniority") or "").lower()
    if seniority in {"c_suite", "csuite", "c-suite", "founder", "owner"}:
        score += 12
    elif seniority == "vp":
        score += 9
    elif seniority in {"director", "head"}:
        score += 6

    if stakeholder.get("email"):
        score += 3
    if stakeholder.get("linkedin_url"):
        score += 2

    return score


def _normalize_meeting_attendees(attendees: Any) -> list[dict[str, Any]]:
    if not isinstance(attendees, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in attendees:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "contact_id": str(item.get("contact_id")) if item.get("contact_id") else None,
            "name": (item.get("name") or item.get("full_name") or "").strip(),
            "title": (item.get("title") or "").strip(),
            "email": (item.get("email") or "").strip().lower(),
            "linkedin_url": (item.get("linkedin_url") or item.get("linkedin") or "").strip(),
        })
    return normalized


def _match_attendee_to_stakeholder(
    attendee: dict[str, Any],
    stakeholders: list[dict[str, Any]],
) -> dict[str, Any] | None:
    contact_id = attendee.get("contact_id")
    if contact_id:
        for stakeholder in stakeholders:
            if stakeholder.get("id") == contact_id:
                return stakeholder

    email = attendee.get("email")
    if email:
        for stakeholder in stakeholders:
            if (stakeholder.get("email") or "").strip().lower() == email:
                return stakeholder

    name = (attendee.get("name") or "").strip().lower()
    if name:
        for stakeholder in stakeholders:
            if (stakeholder.get("name") or "").strip().lower() == name:
                return stakeholder

    return None


def _build_stakeholder_card(
    *,
    stakeholder: dict[str, Any] | None,
    attendee: dict[str, Any] | None,
    status: str,
) -> dict[str, Any]:
    title = attendee.get("title") if attendee else None
    if stakeholder and stakeholder.get("title"):
        title = stakeholder.get("title")

    persona = _canonical_persona(
        (stakeholder or {}).get("persona"),
        (stakeholder or {}).get("persona_type"),
    )
    role = _infer_committee_role(title, persona, (stakeholder or {}).get("persona_type"))
    role_key = role if role in _ROLE_FOCUS else "unknown"
    question_bank = _ROLE_QUESTION_BANK[role_key]

    return {
        "contact_id": (stakeholder or {}).get("id") or (attendee or {}).get("contact_id"),
        "name": (
            (attendee or {}).get("name")
            or (stakeholder or {}).get("name")
            or "Unknown attendee"
        ),
        "title": title,
        "email": (attendee or {}).get("email") or (stakeholder or {}).get("email"),
        "linkedin_url": (stakeholder or {}).get("linkedin_url") or (attendee or {}).get("linkedin_url"),
        "persona": persona,
        "role": role,
        "role_label": _COMMITTEE_ROLE_LABELS.get(role, "Stakeholder"),
        "status": status,
        "priority": "high" if role in {"economic_buyer", "champion", "technical_evaluator"} else "medium",
        "likely_focus": _ROLE_FOCUS[role_key],
        "talk_track": _ROLE_TALK_TRACK[role_key],
        "questions_to_ask": question_bank[:2],
    }


def _build_committee_coverage(stakeholder_cards: list[dict[str, Any]]) -> dict[str, Any]:
    discovered_roles = {
        card["role"]
        for card in stakeholder_cards
        if card.get("role") in _COMMITTEE_ROLE_LABELS
    }
    attending_roles = {
        card["role"]
        for card in stakeholder_cards
        if card.get("status") == "attending" and card.get("role") in _COMMITTEE_ROLE_LABELS
    }
    missing_roles = [
        role for role in _COMMITTEE_ROLE_LABELS
        if role not in discovered_roles
    ]
    meeting_gaps = [
        role for role in _COMMITTEE_ROLE_LABELS
        if role not in attending_roles
    ]

    return {
        "coverage_score": round((len(discovered_roles) / max(len(_COMMITTEE_ROLE_LABELS), 1)) * 100),
        "discovered_roles": [
            {"role": role, "label": _COMMITTEE_ROLE_LABELS[role]}
            for role in _COMMITTEE_ROLE_LABELS
            if role in discovered_roles
        ],
        "attending_roles": [
            {"role": role, "label": _COMMITTEE_ROLE_LABELS[role]}
            for role in _COMMITTEE_ROLE_LABELS
            if role in attending_roles
        ],
        "missing_roles": [
            {"role": role, "label": _COMMITTEE_ROLE_LABELS[role]}
            for role in missing_roles
        ],
        "meeting_gaps": [
            {"role": role, "label": _COMMITTEE_ROLE_LABELS[role]}
            for role in meeting_gaps
        ],
    }


def _build_attendee_intelligence(
    meeting_attendees: Any,
    stakeholders: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_attendees = _normalize_meeting_attendees(meeting_attendees)
    stakeholder_cards: list[dict[str, Any]] = []
    used_contact_ids: set[str] = set()

    for attendee in normalized_attendees[:6]:
        matched = _match_attendee_to_stakeholder(attendee, stakeholders)
        if matched and matched.get("id"):
            used_contact_ids.add(str(matched["id"]))
        stakeholder_cards.append(
            _build_stakeholder_card(stakeholder=matched, attendee=attendee, status="attending")
        )

    if not stakeholder_cards:
        sorted_stakeholders = sorted(
            stakeholders,
            key=_stakeholder_priority_score,
            reverse=True,
        )
        covered_roles: set[str] = set()
        for stakeholder in sorted_stakeholders:
            card = _build_stakeholder_card(stakeholder=stakeholder, attendee=None, status="recommended")
            role = card.get("role")
            if role in _COMMITTEE_ROLE_LABELS and role not in covered_roles:
                covered_roles.add(role)
                stakeholder_cards.append(card)
            elif role == "unknown" and len(stakeholder_cards) < 4:
                stakeholder_cards.append(card)
            if len(stakeholder_cards) >= 4:
                break
    else:
        sorted_stakeholders = sorted(
            [stakeholder for stakeholder in stakeholders if stakeholder.get("id") not in used_contact_ids],
            key=_stakeholder_priority_score,
            reverse=True,
        )
        attendee_roles = {
            card["role"] for card in stakeholder_cards if card.get("role") in _COMMITTEE_ROLE_LABELS
        }
        for stakeholder in sorted_stakeholders:
            card = _build_stakeholder_card(stakeholder=stakeholder, attendee=None, status="recommended")
            role = card.get("role")
            if role in _COMMITTEE_ROLE_LABELS and role not in attendee_roles:
                stakeholder_cards.append(card)
                attendee_roles.add(role)
            if len(stakeholder_cards) >= 6:
                break

    return {
        "has_explicit_attendees": len(normalized_attendees) > 0,
        "stakeholder_cards": stakeholder_cards,
        "committee_coverage": _build_committee_coverage(stakeholder_cards),
    }


def _build_why_now_signals(
    company_profile: dict[str, Any],
    website_analysis: dict[str, Any] | None,
    recent_news: list[dict[str, Any]],
    intent_signals: dict[str, Any],
    google_news: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    why_now: list[dict[str, Any]] = []

    def _coerce_signal_item(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            title = str(value.get("title") or value.get("snippet") or "").strip()
            url = str(value.get("url") or "").strip() or None
            if not title:
                return None
            return {"detail": title, "url": url}
        if isinstance(value, str) and value.strip():
            return {"detail": value.strip(), "url": None}
        return None

    for key, label in (
        ("funding", "Funding / budget signal"),
        ("hiring", "Hiring / scaling signal"),
        ("product", "Launch / expansion signal"),
    ):
        items = (intent_signals or {}).get(key, [])
        if items:
            first = _coerce_signal_item(items[0])
            if first:
                why_now.append({
                    "title": label,
                    "detail": first["detail"],
                    "source": "web_search",
                    "url": first["url"],
                })

    for article in (recent_news or [])[:2]:
        why_now.append({
            "title": "Recent company momentum",
            "detail": article.get("title", ""),
            "source": "recent_news",
            "url": article.get("url"),
        })

    for item in (google_news or [])[:2]:
        why_now.append({
            "title": f"Headline from {item.get('source') or 'Google News'}",
            "detail": item.get("title", ""),
            "source": "google_news",
            "url": item.get("url"),
        })

    if website_analysis and website_analysis.get("hiring_signals"):
        why_now.append({
            "title": "Hiring signal from website",
            "detail": website_analysis["hiring_signals"],
            "source": "website_analysis",
        })

    if company_profile.get("icp_tier") in {"hot", "warm"}:
        why_now.append({
            "title": "Strong ICP fit",
            "detail": f"{company_profile.get('name', 'This account')} scores {company_profile.get('icp_score', 'n/a')} and sits in the {company_profile.get('icp_tier')} tier.",
            "source": "crm_profile",
        })

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in why_now:
        key = item.get("detail", "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return deduped[:6]


async def _collect_crm_signals(
    session: AsyncSession,
    *,
    deal_id: UUID | None,
    company_id: UUID | None,
    meeting_id: UUID,
) -> dict[str, Any]:
    """
    Collect first-party CRM signals that are richer than any web search:
      - Prior TLDV meeting transcripts / AI summaries
      - Email thread context (Zippy + personal sync)
      - Aircall call history (outcomes, duration, who picked up)
      - Instantly outreach activity (what messaging was sent / opened)
      - Deal stage history and current stage context
    """
    from sqlmodel import select as sm_select
    from app.models.activity import Activity
    from app.models.deal import Deal
    from app.models.meeting import Meeting

    crm_signals: dict[str, Any] = {
        "prior_meetings": [],
        "email_threads": [],
        "call_history": [],
        "outreach_activity": [],
        "deal_context": None,
        "meeting_number": 1,
    }

    try:
        # ── Prior TLDV meetings (same deal or company) ────────────────────────
        prior_meeting_filter = []
        if deal_id:
            prior_meeting_filter.append(Meeting.deal_id == deal_id)
        if company_id:
            prior_meeting_filter.append(Meeting.company_id == company_id)

        if prior_meeting_filter:
            from sqlalchemy import or_
            prior_meetings_result = await session.execute(
                sm_select(Meeting).where(
                    or_(*prior_meeting_filter),
                    Meeting.id != meeting_id,
                    Meeting.status.in_(["completed", "scheduled"]),
                ).order_by(Meeting.scheduled_at.desc()).limit(5)
            )
            prior_meetings = prior_meetings_result.scalars().all()
            crm_signals["meeting_number"] = len(prior_meetings) + 1

            for pm in prior_meetings:
                entry: dict[str, Any] = {
                    "title": pm.title,
                    "scheduled_at": pm.scheduled_at.isoformat() if pm.scheduled_at else None,
                    "meeting_type": pm.meeting_type,
                    "status": pm.status,
                    "ai_summary": pm.ai_summary,
                    "next_steps": pm.next_steps,
                    "what_went_right": pm.what_went_right,
                    "what_went_wrong": pm.what_went_wrong,
                }
                # Pull transcript from associated Activity if available
                if pm.id:
                    transcript_result = await session.execute(
                        sm_select(Activity).where(
                            Activity.type == "transcript",
                            Activity.external_source == "tldv",
                        ).where(
                            Activity.deal_id == deal_id if deal_id else Activity.id.isnot(None)
                        ).order_by(Activity.created_at.desc()).limit(1)
                    )
                    transcript_act = transcript_result.scalar_one_or_none()
                    if transcript_act:
                        entry["transcript_summary"] = transcript_act.ai_summary
                        entry["transcript_content"] = (transcript_act.content or "")[:800]

                crm_signals["prior_meetings"].append(entry)

        # ── Email thread context ──────────────────────────────────────────────
        if deal_id:
            email_result = await session.execute(
                sm_select(Activity).where(
                    Activity.deal_id == deal_id,
                    Activity.type == "email",
                ).order_by(Activity.created_at.desc()).limit(12)
            )
            email_acts = email_result.scalars().all()
            for act in email_acts:
                crm_signals["email_threads"].append({
                    "subject": act.email_subject,
                    "from_addr": act.email_from,
                    "ai_summary": act.ai_summary,
                    "source": act.source,
                    "snippet": (act.content or "")[:300],
                    "date": act.created_at.isoformat() if act.created_at else None,
                })

        # ── Aircall call history ──────────────────────────────────────────────
        if deal_id:
            call_result = await session.execute(
                sm_select(Activity).where(
                    Activity.deal_id == deal_id,
                    Activity.type == "call",
                ).order_by(Activity.created_at.desc()).limit(10)
            )
            call_acts = call_result.scalars().all()
            for act in call_acts:
                crm_signals["call_history"].append({
                    "outcome": act.call_outcome,
                    "duration_seconds": act.call_duration,
                    "agent": act.aircall_user_name,
                    "ai_summary": act.ai_summary,
                    "date": act.created_at.isoformat() if act.created_at else None,
                })

        # ── Instantly outreach activity ───────────────────────────────────────
        if deal_id or company_id:
            outreach_filter = []
            if deal_id:
                outreach_filter.append(Activity.deal_id == deal_id)
            outreach_result = await session.execute(
                sm_select(Activity).where(
                    Activity.source == "instantly",
                    *outreach_filter,
                ).order_by(Activity.created_at.desc()).limit(8)
            )
            outreach_acts = outreach_result.scalars().all()
            for act in outreach_acts:
                crm_signals["outreach_activity"].append({
                    "subject": act.email_subject,
                    "outcome": act.ai_summary,
                    "date": act.created_at.isoformat() if act.created_at else None,
                })

        # ── Deal context (stage, health, days in stage) ───────────────────────
        if deal_id:
            deal = await session.get(Deal, deal_id)
            if deal:
                crm_signals["deal_context"] = {
                    "name": deal.name,
                    "stage": deal.stage,
                    "health": deal.health,
                    "days_in_stage": deal.days_in_stage,
                    "priority": deal.priority,
                    "last_activity_at": deal.last_activity_at.isoformat() if deal.last_activity_at else None,
                }

    except Exception as exc:
        logger.warning("CRM signal collection failed: %s", exc)

    return crm_signals


async def _auto_link_attendees_to_deal(
    session: AsyncSession,
    *,
    meeting: Any,
    deal_id: UUID | None,
    company_id: UUID | None,
) -> int:
    """
    Auto-link meeting attendees to deal contacts.
    Creates Contact + DealContact rows for any attendee email not already in CRM.
    Returns count of new links created.
    """
    from sqlmodel import select as sm_select
    from app.models.contact import Contact
    from app.models.deal import DealContact

    if not deal_id:
        return 0

    attendees = meeting.attendees if isinstance(meeting.attendees, list) else []
    if not attendees:
        return 0

    linked = 0
    attendees_changed = False
    for attendee in attendees:
        if not isinstance(attendee, dict):
            continue
        email = (attendee.get("email") or "").strip().lower()
        name = (attendee.get("name") or "").strip()
        title = (attendee.get("title") or "").strip()
        contact_id = attendee.get("contact_id")

        contact: Contact | None = None

        # Try by contact_id first
        if contact_id:
            contact = await session.get(Contact, contact_id)

        # Try by email
        if not contact and email:
            result = await session.execute(
                sm_select(Contact).where(Contact.email == email)
            )
            contact = result.scalar_one_or_none()

        # Do NOT auto-create stub contacts for unknown attendees. That spawned
        # low-value "<email-prefix> Attendee" rows on every account (note-takers,
        # internal folks, one-off guests). We only LINK attendees who already
        # exist in the CRM; real prospects are added via the Prospecting CSV
        # upload and then match here by email.
        if not contact:
            continue

        next_contact_id = str(contact.id) if contact.id else attendee.get("contact_id")
        next_name = f"{contact.first_name} {contact.last_name}".strip() or name
        if attendee.get("contact_id") != next_contact_id:
            attendee["contact_id"] = next_contact_id
            attendees_changed = True
        if next_name and attendee.get("name") != next_name:
            attendee["name"] = next_name
            attendees_changed = True
        if contact.title:
            if attendee.get("title") != contact.title:
                attendee["title"] = contact.title
                attendees_changed = True
        elif title:
            contact.title = title
            session.add(contact)
            attendees_changed = True
        if contact.linkedin_url:
            if attendee.get("linkedin_url") != contact.linkedin_url:
                attendee["linkedin_url"] = contact.linkedin_url
                attendees_changed = True
        if attendee.get("matched") is not True:
            attendee["matched"] = True
            attendees_changed = True

        # Check if already linked
        existing_link = await session.execute(
            sm_select(DealContact).where(
                DealContact.deal_id == deal_id,
                DealContact.contact_id == contact.id,
            )
        )
        if not existing_link.scalar_one_or_none():
            session.add(DealContact(deal_id=deal_id, contact_id=contact.id))
            await session.flush()
            linked += 1

    if linked or attendees_changed:
        meeting.attendees = attendees
        session.add(meeting)
        await session.commit()
        logger.info("pre_meeting_intel: refreshed attendee CRM links for deal %s (new_links=%d)", deal_id, linked)

    return linked


async def run_pre_meeting_intelligence(
    meeting_id: UUID, session: AsyncSession
) -> dict[str, Any]:
    """
    Run the full pre-meeting intelligence pipeline and persist results to the meeting.
    Returns the full intel dict.
    """
    from app.models.meeting import Meeting
    from app.models.company import Company
    from app.repositories.contact import ContactRepository
    from app.repositories.battlecard import BattlecardRepository
    from app.clients.web_search import WebSearchClient
    from app.clients.claude import ClaudeClient
    from app.clients.hunter import HunterClient

    meeting = await session.get(Meeting, meeting_id)
    if not meeting:
        return {"error": "Meeting not found"}

    company: Company | None = None
    if meeting.company_id:
        company = await session.get(Company, meeting.company_id)

    web = WebSearchClient()
    ai = ClaudeClient()
    hunter = HunterClient()

    domain = (company.domain or "") if company else ""
    name = (company.name or "") if company else ""

    # ── 1. Company profile ────────────────────────────────────────────────────
    company_profile: dict = {}
    if company:
        company_profile = {
            "name": name,
            "domain": domain,
            "industry": company.industry,
            "vertical": company.vertical,
            "employee_count": company.employee_count,
            "funding_stage": company.funding_stage,
            "arr_estimate": company.arr_estimate,
            "icp_score": company.icp_score,
            "icp_tier": company.icp_tier,
            "has_dap": company.has_dap,
            "dap_tool": company.dap_tool,
            "tech_stack": company.tech_stack or {},
        }

    # ── 2-7. Parallel web research — seed from enrichment_cache first ───────────
    # If ICP research was already run for this company, reuse the cached data
    # instead of re-scraping everything. Only fetch what's missing.
    ec = (company.enrichment_cache or {}) if (company and isinstance(company.enrichment_cache, dict)) else {}

    def _unwrap(key):
        """Return the .data value from a cache entry, or None."""
        entry = ec.get(key)
        if isinstance(entry, dict):
            return entry.get("data")
        return None

    # Seed from cache — these are skipped in the parallel fetch below if populated
    _cached_web_scrape = _unwrap("web_scrape")  # may be dict or str
    _cached_intent = _unwrap("intent_signals")
    _cached_hunter_contacts_raw = _unwrap("hunter_contacts")
    _cached_ai_summary = _unwrap("ai_summary") if isinstance(_unwrap("ai_summary"), dict) else {}
    _cached_icp_entry = _unwrap("icp_analysis")
    # icp_analysis.data may be nested one more level
    _cached_icp = _cached_icp_entry.get("data") if isinstance(_cached_icp_entry, dict) else (
        _cached_icp_entry if isinstance(_cached_icp_entry, dict) else None
    )
    _cached_competitive_raw = ec.get("competitive_landscape_v2")

    company_background: dict | None = None
    website_pages: dict | None = None
    recent_news: list[dict] = []
    milestones: list[dict] = []
    google_news: list[dict] = []
    hunter_company: dict | None = None
    competitive_landscape: list[dict] = []

    # Pre-populate from cache where available
    intent_signals: dict = {}
    if isinstance(_cached_intent, dict):
        hiring_signals = _cached_intent.get("ps_hiring") or []
        funding_signals = _cached_intent.get("funding") or []
        product_signals = _cached_intent.get("product") or _cached_intent.get("growth") or []
        intent_signals = {
            "hiring": [
                {"title": s.get("title", ""), "snippet": s.get("snippet", ""), "url": s.get("url", "")}
                for s in hiring_signals
                if isinstance(s, dict) and s.get("title")
            ][:10],
            "funding": [
                {"title": s.get("title", ""), "snippet": s.get("snippet", ""), "url": s.get("url", "")}
                for s in funding_signals
                if isinstance(s, dict) and (s.get("title") or s.get("snippet"))
            ][:5],
            "product": [
                {"title": s.get("title", ""), "snippet": s.get("snippet", ""), "url": s.get("url", "")}
                for s in product_signals
                if isinstance(s, dict) and (s.get("title") or s.get("snippet"))
            ][:5],
        }

    hunter_contacts: dict | None = None
    if isinstance(_cached_hunter_contacts_raw, list) and _cached_hunter_contacts_raw:
        hunter_contacts = {"contacts": [c for c in _cached_hunter_contacts_raw if isinstance(c, dict)]}

    if isinstance(_cached_competitive_raw, dict) and "data" in _cached_competitive_raw:
        _cached_competitive_raw = _cached_competitive_raw.get("data")

    if isinstance(_cached_competitive_raw, list) and _cached_competitive_raw:
        competitive_landscape = [
            {
                "name": c.get("name") or c.get("competitor") or c.get("title", ""),
                "title": c.get("title") or c.get("name") or c.get("competitor", ""),
                "summary": c.get("summary") or c.get("snippet", ""),
                "snippet": c.get("snippet") or c.get("summary", ""),
                "url": c.get("url") or c.get("link") or "",
            }
            for c in _cached_competitive_raw
            if isinstance(c, dict)
        ]

    async def _website_summary():
        if _cached_web_scrape:
            # _cached_web_scrape may be a dict with page text or a raw string
            if isinstance(_cached_web_scrape, dict):
                text = _cached_web_scrape.get("homepage_text") or _cached_web_scrape.get("text") or ""
            else:
                text = str(_cached_web_scrape)
            if text:
                desc = _cached_ai_summary.get("description") if _cached_ai_summary else None
                return {"extract": text[:1500], "description": desc}
        if not domain or domain.endswith(".unknown"):
            return None
        return await web.company_website_summary(domain, name, ai)

    async def _website_pages():
        if _cached_web_scrape:
            return None  # Already have scraped content in cache
        if not domain or domain.endswith(".unknown"):
            return None
        return await web.scrape_company_pages(domain)

    async def _recent_news():
        if not name:
            return []
        return await web.recent_news(name, domain)

    async def _milestones():
        if not name:
            return []
        return await web.company_milestones(name, domain)

    async def _intent_signals_fetch():
        if intent_signals:
            return intent_signals  # Already seeded from cache
        if not name:
            return {}
        return await web.search_intent_signals(name, domain)

    async def _google_news():
        if not name:
            return []
        return await _fetch_google_news_rss(name, domain)

    async def _hunter_search():
        if hunter_contacts:
            return hunter_contacts  # Already seeded from cache
        if not domain or domain.endswith(".unknown"):
            return None
        return await hunter.domain_search(domain)

    async def _hunter_enrich():
        if not domain or domain.endswith(".unknown"):
            return None
        return await hunter.company_enrichment(domain)

    async def _competitors():
        if competitive_landscape:
            return competitive_landscape  # Already seeded from cache
        if not name:
            return []
        return await web.search(
            f'"{name}" competitors alternatives AP automation invoice lifecycle management Coupa Tipalti SAP Concur Medius {datetime.utcnow().year}',
            max_results=5,
        )

    # Fire all web research in parallel (cached tasks return instantly)
    tasks = {
        "bg": _website_summary(),
        "pages": _website_pages(),
        "news": _recent_news(),
        "milestones": _milestones(),
        "intent": _intent_signals_fetch(),
        "gnews": _google_news(),
        "hunter": _hunter_search(),
        "hunter_co": _hunter_enrich(),
        "competitors": _competitors(),
    }
    results = await asyncio.gather(
        *tasks.values(), return_exceptions=True
    )
    task_map = dict(zip(tasks.keys(), results))

    def _safe(key, default=None):
        val = task_map.get(key)
        if isinstance(val, Exception):
            logger.warning("Pre-meeting intel task '%s' failed: %s", key, safe_error_message(val))
            return default
        return val if val is not None else default

    company_background = _safe("bg")
    website_pages = _safe("pages")
    recent_news = _safe("news", [])
    milestones = _safe("milestones", [])
    intent_signals = _safe("intent", intent_signals)  # fall back to cache-seeded value
    google_news = _safe("gnews", [])
    hunter_contacts = _safe("hunter", hunter_contacts)  # fall back to cache-seeded value
    hunter_company = _safe("hunter_co")
    competitive_landscape = _safe("competitors", competitive_landscape)  # fall back to cache-seeded value

    # ── 8. Deep website analysis (extract pricing/careers/customers insights) ─
    website_analysis: dict | None = None
    if website_pages and website_pages.get("pages_scraped", 0) > 0:
        website_analysis = await _analyse_website_pages(ai, name, website_pages.get("text", ""))

    # ── 9. Auto-link attendees first, so stakeholder cards use CRM title and LinkedIn
    # instead of letting the AI infer designation from a bare calendar attendee.
    await _auto_link_attendees_to_deal(
        session,
        meeting=meeting,
        deal_id=meeting.deal_id,
        company_id=meeting.company_id,
    )

    # ── 9b. Stakeholder profiles ──────────────────────────────────────────────
    stakeholders: list[dict] = []
    if company:
        contact_repo = ContactRepository(session)
        contacts, _ = await contact_repo.list_with_company_name(
            company_id=company.id, skip=0, limit=20
        )
        for c in contacts:
            stakeholders.append({
                "id": str(c.id),
                "name": f"{c.first_name} {c.last_name}".strip(),
                "title": c.title,
                "email": c.email,
                "persona": _canonical_persona(c.persona, c.persona_type),
                "persona_type": c.persona_type,
                "seniority": c.seniority,
                "linkedin_url": c.linkedin_url,
                "email_verified": c.email_verified,
            })

    # ── 9c. First-party CRM signals (transcripts, emails, calls, outreach) ───
    crm_signals = await _collect_crm_signals(
        session,
        deal_id=meeting.deal_id,
        company_id=meeting.company_id,
        meeting_id=meeting_id,
    )

    attendee_intelligence = _build_attendee_intelligence(meeting.attendees, stakeholders)
    why_now_signals = _build_why_now_signals(
        company_profile=company_profile,
        website_analysis=website_analysis,
        recent_news=recent_news,
        intent_signals=intent_signals,
        google_news=google_news,
    )

    # ── 10. Relevant battlecards ──────────────────────────────────────────────
    relevant_battlecards: list[dict] = []
    try:
        bc_repo = BattlecardRepository(session)
        search_term = (
            company.vertical or company.industry or "enterprise"
        ) if company else "enterprise"
        bcs = await bc_repo.search(search_term)
        for bc in bcs[:4]:
            relevant_battlecards.append({
                "id": str(bc.id),
                "title": bc.title,
                "category": bc.category,
                "summary": (bc.content or "")[:300],
            })
    except Exception as e:
        logger.warning("Battlecard search failed: %s", safe_error_message(e))

    # ── 11. Knowledge base context ─────────────────────────────────────────────
    from app.services.knowledge_context import get_knowledge_context
    kb_query = " ".join(filter(None, [
        (company_profile or {}).get("name"),
        (company_profile or {}).get("industry"),
        (company_profile or {}).get("vertical"),
        meeting.meeting_type,
        (company_profile or {}).get("dap_tool"),
    ]))
    kb_context = await get_knowledge_context(
        session, "pre_meeting", query=kb_query, limit=5
    )

    # ── 12. GPT-4o executive briefing ─────────────────────────────────────────
    executive_briefing: str | None = None
    if company_profile:
        executive_briefing = await _generate_executive_briefing(
            ai=ai,
            company_profile=company_profile,
            company_background=company_background,
            website_analysis=website_analysis,
            recent_news=recent_news,
            intent_signals=intent_signals,
            google_news=google_news,
            hunter_company=hunter_company,
            competitive_landscape=competitive_landscape,
            stakeholders=stakeholders,
            attendee_intelligence=attendee_intelligence,
            why_now_signals=why_now_signals,
            meeting_type=meeting.meeting_type,
            kb_context=kb_context,
            crm_signals=crm_signals,
        )

    # Game plan: 3-4 specific actions distilled by AI from the briefing + CRM
    # signals (replaces the old name-in-a-template boilerplate). Empty if there's
    # no real signal — we'd rather show nothing than generic advice.
    meeting_recommendations = await _generate_meeting_game_plan(
        ai,
        executive_briefing=executive_briefing,
        meeting_type=meeting.meeting_type,
        crm_signals=crm_signals,
        attendee_intelligence=attendee_intelligence,
    )

    # ── Enrich company_snapshot from ICP cache ────────────────────────────────
    # Pull pain points, talking points, and Beacon angle from existing ICP
    # analysis so the meeting brief shows this without re-running everything.
    company_snapshot: dict = {}
    if _cached_icp and isinstance(_cached_icp, dict):
        icp_data = _cached_icp  # already unwrapped via _unwrap() above
        company_snapshot = {
            "icp_score": company.icp_score if company else None,
            "icp_tier": company.icp_tier if company else None,
            "industry": company.industry if company else None,
            "employee_count": company.employee_count if company else None,
            "funding_stage": company.funding_stage if company else None,
            "pain_points": icp_data.get("pain_points") or (_cached_ai_summary.get("pain_points") if _cached_ai_summary else []),
            "talking_points": (_cached_ai_summary.get("talking_points") if _cached_ai_summary else []),
            "beacon_angle": icp_data.get("beacon_angle"),
            "conversation_starter": icp_data.get("conversation_starter"),
            "why_now_summary": icp_data.get("why_now"),
            "recommended_approach": icp_data.get("recommended_outreach_strategy"),
        }
    elif company:
        company_snapshot = {
            "icp_score": company.icp_score,
            "icp_tier": company.icp_tier,
            "industry": company.industry,
            "employee_count": company.employee_count,
            "funding_stage": company.funding_stage,
        }

    # ── Assemble & persist ────────────────────────────────────────────────────
    research_data = {
        "company_profile": company_profile,
        "company_snapshot": company_snapshot,
        "company_background": company_background,
        "website_analysis": website_analysis,
        "recent_news": recent_news[:8],
        "milestones": milestones[:5],
        "intent_signals": intent_signals,
        "google_news": google_news[:6],
        "hunter_contacts": hunter_contacts,
        "hunter_company": hunter_company,
        "competitive_landscape": competitive_landscape[:5],
        "stakeholders": stakeholders,
        "attendee_intelligence": attendee_intelligence,
        "why_now_signals": why_now_signals,
        "meeting_recommendations": meeting_recommendations,
        "relevant_battlecards": relevant_battlecards,
        "executive_briefing": executive_briefing,
        "crm_signals": crm_signals,
    }

    meeting.research_data = research_data
    meeting.updated_at = datetime.utcnow()
    session.add(meeting)
    await session.commit()
    await session.refresh(meeting)

    return {
        "meeting_id": str(meeting_id),
        "research_data": research_data,
    }


async def generate_meeting_demo_strategy(
    meeting_id: UUID, session: AsyncSession
) -> dict[str, Any]:
    """
    Generate (or re-generate) the GPT-4o demo strategy for a meeting.
    Reads existing research_data from the meeting if available; falls back to
    fetching company profile from DB.
    """
    from app.models.meeting import Meeting
    from app.models.company import Company
    from app.clients.claude import ClaudeClient

    meeting = await session.get(Meeting, meeting_id)
    if not meeting:
        return {"error": "Meeting not found"}

    ai = ClaudeClient()

    # Prefer cached research_data so we don't re-run web searches
    research: dict = (meeting.research_data or {}) if isinstance(meeting.research_data, dict) else {}
    company_profile = research.get("company_profile", {})
    company_background = research.get("company_background")
    recent_news = research.get("recent_news", [])
    stakeholders = research.get("stakeholders", [])
    attendee_intelligence = research.get("attendee_intelligence", {})
    why_now_signals = research.get("why_now_signals", [])

    # If no cached profile, pull from DB
    if not company_profile and meeting.company_id:
        company: Company | None = await session.get(Company, meeting.company_id)
        if company:
            company_profile = {
                "name": company.name,
                "domain": company.domain,
                "industry": company.industry,
                "vertical": company.vertical,
                "employee_count": company.employee_count,
                "funding_stage": company.funding_stage,
                "arr_estimate": company.arr_estimate,
                "icp_score": company.icp_score,
                "icp_tier": company.icp_tier,
                "has_dap": company.has_dap,
                "dap_tool": company.dap_tool,
                "tech_stack": company.tech_stack or {},
            }

    from app.services.knowledge_context import get_knowledge_context as _get_kb
    demo_query = " ".join(filter(None, [
        (company_profile or {}).get("name"),
        (company_profile or {}).get("industry"),
        (company_profile or {}).get("dap_tool"),
        meeting.meeting_type,
    ]))
    kb_context = await _get_kb(
        session, "demo_strategy", query=demo_query, limit=4, max_total_chars=2000
    )

    demo_strategy = await _generate_demo_strategy(
        ai=ai,
        company_profile=company_profile,
        company_background=company_background,
        recent_news=recent_news,
        stakeholders=stakeholders,
        attendee_intelligence=attendee_intelligence,
        why_now_signals=why_now_signals,
        meeting_type=meeting.meeting_type,
        kb_context=kb_context,
    )

    meeting.demo_strategy = demo_strategy
    meeting.updated_at = datetime.utcnow()
    session.add(meeting)
    await session.commit()
    await session.refresh(meeting)

    return {
        "meeting_id": str(meeting_id),
        "demo_strategy": demo_strategy,
    }


async def _generate_demo_strategy(
    ai,
    company_profile: dict,
    company_background: dict | None,
    recent_news: list[dict],
    stakeholders: list[dict],
    attendee_intelligence: dict | None,
    why_now_signals: list[dict],
    meeting_type: str,
    kb_context: str = "",
) -> str:
    """Ask GPT-4o to build a demo story lineup tailored to this prospect."""
    if not company_profile:
        return ""

    # Build context for the prompt
    name = company_profile.get("name", "the company")
    industry = company_profile.get("industry", "")
    vertical = company_profile.get("vertical", "")
    employees = company_profile.get("employee_count")
    funding = company_profile.get("funding_stage", "")
    has_dap = company_profile.get("has_dap")
    dap_tool = company_profile.get("dap_tool")
    tech = company_profile.get("tech_stack") or {}
    icp_tier = company_profile.get("icp_tier", "")

    bg_extract = company_background.get("extract", "") if company_background else ""
    news_titles = "\n".join(f"- {n['title']}" for n in recent_news[:3]) or "No recent news found."

    persona_summary = ""
    stakeholder_cards = (attendee_intelligence or {}).get("stakeholder_cards", [])
    if stakeholder_cards:
        personas = [
            f"{s.get('name')} ({s.get('title') or s.get('role_label') or 'unknown'})"
            for s in stakeholder_cards[:4]
        ]
        persona_summary = ", ".join(personas)
    elif stakeholders:
        personas = [f"{s['name']} ({s['title'] or s['persona'] or 'unknown'})" for s in stakeholders[:4]]
        persona_summary = ", ".join(personas)

    dap_context = ""
    if has_dap and dap_tool:
        dap_context = f"They currently use {dap_tool} as their DAP/adoption tool — this is a direct displacement opportunity."
    elif has_dap:
        dap_context = "They have a DAP tool in use — position Beacon as the AI-native replacement."
    else:
        dap_context = "No DAP detected — position Beacon as a greenfield AI deployment automation opportunity."

    timing_context = "\n".join(
        f"- {signal.get('detail', '')}"
        for signal in (why_now_signals or [])[:3]
        if signal.get("detail")
    ) or "No strong timing signals found."

    system = (
        "You are a senior enterprise sales strategist for Beacon.li — an AI implementation "
        "orchestration platform that automates enterprise SaaS deployments (onboarding, adoption, ROI). "
        "You help AEs prepare for demos by creating a tailored story lineup. "
        "Be specific, practical, and concise. Format as numbered bullet points."
    )

    user = f"""
Prospect: {name}
Industry: {industry} / {vertical}
Size: {employees} employees | Stage: {funding} | ICP tier: {icp_tier}
Meeting type: {meeting_type}
Attendees: {persona_summary or 'Unknown'}
Tech context: {dap_context}
Tech stack: {', '.join(f'{k}: {v}' for k, v in tech.items()) if isinstance(tech, dict) else ', '.join(str(t) for t in tech) if isinstance(tech, list) else 'Unknown'}
Company background: {bg_extract[:300] if bg_extract else 'Not available'}
Recent signals:
{news_titles}
Why now:
{timing_context}
{kb_context}
Generate a Demo Strategy with:
1. Opening hook (30-second value frame specific to their situation)
2. Discovery question to lead with (uncover the pain)
3. Story lineup — 3 demo chapters in order (what to show, why it matters to them)
4. Key differentiation point vs competitors to land
5. Likely objection and how to handle it
6. Suggested next step / call to action
""".strip()

    result = await ai.complete(system, user, max_tokens=600)

    return result


# ── Research More: gap-filling enrichment ────────────────────────────────────

async def run_research_more(
    meeting_id: UUID, session: AsyncSession
) -> dict[str, Any]:
    """
    Targeted gap-filling for a meeting's company enrichment_cache.
    Detects what's missing or stale and only fetches those pieces:
      - hunter_company (firmographics) if missing
      - hunter_contacts with seniority+department if empty/paused
      - google_news if missing or older than 7 days
      - web_scrape if missing
      - competitive_landscape_v2 if missing
      - conversation_starter + why_now if missing from icp_analysis
    Returns a summary of what was filled.
    """
    from datetime import timedelta
    from app.models.meeting import Meeting
    from app.models.company import Company
    from app.clients.hunter import HunterClient
    from app.clients.web_search import WebSearchClient
    from app.clients.claude import ClaudeClient

    meeting = await session.get(Meeting, meeting_id)
    if not meeting:
        return {"error": "Meeting not found"}

    if not meeting.company_id:
        return {"error": "Meeting has no linked company"}

    company: Company | None = await session.get(Company, meeting.company_id)
    if not company:
        return {"error": "Company not found"}

    ec: dict = company.enrichment_cache if isinstance(company.enrichment_cache, dict) else {}
    domain = company.domain or ""
    raw_name = company.name or ""
    # Strip CRM suffixes like " - Impl", " - Skilljar" to get the real company name
    import re as _re
    name = _re.sub(r'\s*-\s*(Impl|Skilljar|CS|Pilot|Trial|POC|Demo|Test)\s*$', '', raw_name, flags=_re.IGNORECASE).strip() or raw_name
    now = datetime.utcnow()
    filled: list[str] = []
    gaps: list[str] = []

    def _unwrap(key):
        entry = ec.get(key)
        if isinstance(entry, dict):
            return entry.get("data")
        return entry

    def _age_days(key) -> float:
        entry = ec.get(key)
        if not isinstance(entry, dict):
            return 9999
        fetched_at = entry.get("fetched_at")
        if not fetched_at:
            return 9999
        try:
            dt = datetime.fromisoformat(fetched_at)
            return (now - dt).total_seconds() / 86400
        except Exception:
            return 9999

    def _has_contacts(key) -> bool:
        """Check if hunter_contacts has real contacts — handles both flat array and {contacts:[]} shapes."""
        data = _unwrap(key)
        if isinstance(data, list):
            return len(data) > 0
        if isinstance(data, dict):
            return len(data.get("contacts", [])) > 0
        return False

    hunter = HunterClient()
    web = WebSearchClient()
    ai = ClaudeClient()

    tasks_to_run: list[tuple[str, Any]] = []

    # ── Gap: hunter_company firmographics ──
    if not _unwrap("hunter_company") and domain and not domain.endswith(".unknown"):
        gaps.append("hunter_company")
        tasks_to_run.append(("hunter_company", hunter.company_enrichment(domain)))

    # ── Gap: hunter_contacts (empty or paused) ──
    hc_entry = ec.get("hunter_contacts")
    hc_paused = isinstance(hc_entry, dict) and hc_entry.get("paused")
    if (not _has_contacts("hunter_contacts") or hc_paused) and domain and not domain.endswith(".unknown"):
        gaps.append("hunter_contacts")
        tasks_to_run.append(("hunter_contacts", hunter.domain_search_rich(domain)))

    # ── Gap: google_news missing or >7 days old ──
    if not _unwrap("google_news") or _age_days("google_news") > 7:
        if name:
            gaps.append("google_news")
            tasks_to_run.append(("google_news", _fetch_google_news_rss(name, domain)))

    # ── Gap: web_scrape missing ──
    if not _unwrap("web_scrape") and domain and not domain.endswith(".unknown"):
        gaps.append("web_scrape")
        tasks_to_run.append(("web_scrape", web.company_website_summary(domain, name, ai)))

    # ── Gap: competitive_landscape missing ──
    if not _unwrap("competitive_landscape_v2") and name:
        gaps.append("competitive_landscape")
        tasks_to_run.append(("competitive_landscape_v2", web.search(
            f'"{name}" competitors alternatives AP automation invoice lifecycle management Coupa Tipalti SAP Concur Medius {now.year}', max_results=5
        )))

    # ── Gap: conversation_starter / why_now missing from icp_analysis ──
    icp_raw = _unwrap("icp_analysis")
    icp_data = icp_raw.get("data") if isinstance(icp_raw, dict) and "data" in icp_raw else icp_raw
    missing_icp_fields = []
    if isinstance(icp_data, dict):
        if not icp_data.get("conversation_starter"):
            missing_icp_fields.append("conversation_starter")
        if not icp_data.get("why_now"):
            missing_icp_fields.append("why_now")

    if missing_icp_fields and name:
        gaps.append(f"icp_fields ({', '.join(missing_icp_fields)})")
        ai_summary = _unwrap("ai_summary") or {}
        pain_points = (icp_data or {}).get("pain_points") or (ai_summary.get("pain_points") if isinstance(ai_summary, dict) else []) or []
        beacon_angle = (icp_data or {}).get("beacon_angle") or company.beacon_angle or ""
        why_now = company.why_now or ""
        tasks_to_run.append(("icp_fields", _generate_missing_icp_fields(
            ai=ai,
            company_name=name,
            industry=company.industry or "",
            pain_points=pain_points,
            beacon_angle=beacon_angle,
            why_now=why_now,
            missing=missing_icp_fields,
        )))

    if not tasks_to_run:
        return {"filled": [], "gaps_detected": [], "message": "All data already up to date"}

    # Run all gap-filling tasks in parallel
    results = await asyncio.gather(*[t[1] for t in tasks_to_run], return_exceptions=True)
    task_results = {tasks_to_run[i][0]: results[i] for i in range(len(tasks_to_run))}

    for key, result in task_results.items():
        if isinstance(result, Exception):
            logger.warning("Research-more task '%s' failed: %s", key, safe_error_message(result))
            continue
        if result is None:
            continue

        if key == "hunter_company" and isinstance(result, dict):
            ec["hunter_company"] = {"data": result, "fetched_at": now.isoformat()}
            filled.append("hunter_company")

        elif key == "hunter_contacts" and isinstance(result, dict):
            ec["hunter_contacts"] = {"data": result, "fetched_at": now.isoformat()}
            filled.append("hunter_contacts")

        elif key == "google_news" and isinstance(result, list):
            ec["google_news"] = {"data": result, "fetched_at": now.isoformat()}
            filled.append("google_news")

        elif key == "web_scrape":
            ec["web_scrape"] = {"data": result, "fetched_at": now.isoformat()}
            filled.append("web_scrape")

        elif key == "competitive_landscape_v2" and isinstance(result, list) and result:
            structured = [
                {
                    "name": r.get("title", ""),
                    "title": r.get("title", ""),
                    "summary": r.get("snippet", ""),
                    "snippet": r.get("snippet", ""),
                    "url": r.get("url", ""),
                }
                for r in result if isinstance(r, dict)
            ]
            ec["competitive_landscape_v2"] = {"data": structured, "fetched_at": now.isoformat()}
            filled.append("competitive_landscape")

        elif key == "icp_fields" and isinstance(result, dict):
            if not isinstance(icp_data, dict):
                icp_data = {}
            for field in missing_icp_fields:
                if result.get(field):
                    icp_data[field] = result[field]
            # Write back into the cache entry
            if isinstance(icp_raw, dict) and "data" in icp_raw:
                icp_raw["data"] = icp_data
                ec["icp_analysis"] = {"data": icp_raw, "fetched_at": now.isoformat()}
            else:
                ec["icp_analysis"] = {"data": icp_data, "fetched_at": now.isoformat()}
            filled.append(f"icp_fields ({', '.join(missing_icp_fields)})")

    # Persist updated cache
    from sqlalchemy import text as sa_text
    from sqlalchemy import update
    from app.models.company import Company as CompanyModel
    import json as _json

    stmt = (
        update(CompanyModel)
        .where(CompanyModel.id == company.id)
        .values(enrichment_cache=ec)
    )
    await session.execute(stmt)
    await session.commit()

    return {
        "filled": filled,
        "gaps_detected": gaps,
        "message": f"Filled {len(filled)} of {len(gaps)} gaps",
    }


async def _generate_missing_icp_fields(
    ai,
    company_name: str,
    industry: str,
    pain_points: list,
    beacon_angle: str,
    why_now: str,
    missing: list[str],
) -> dict:
    """Use GPT-4o to generate missing ICP fields (conversation_starter, why_now)."""
    system = (
        "You are a B2B sales coach. Given company context, generate the requested fields. "
        "Return ONLY valid JSON with the requested keys. Be specific and actionable, not generic."
    )
    pain_str = "; ".join(pain_points[:3]) if pain_points else "not specified"
    user = (
        f"Company: {company_name}\nIndustry: {industry}\n"
        f"Pain points: {pain_str}\nBeacon angle: {beacon_angle}\nWhy now hint: {why_now}\n\n"
        f"Generate ONLY these fields as JSON: {missing}\n"
        "conversation_starter: A single punchy opening sentence the rep can say verbatim (max 30 words).\n"
        "why_now: 1-2 sentences on why this company needs this NOW based on context."
    )
    try:
        result = await ai.complete(system, user, max_tokens=300)
        import json as _json
        if result:
            cleaned = result.strip().strip("```json").strip("```").strip()
            return _json.loads(cleaned)
    except Exception as e:
        logger.warning("Missing ICP fields generation failed: %s", safe_error_message(e))
    return {}


# ── Helper: Google News RSS ──────────────────────────────────────────────────

async def _fetch_google_news_rss(company_name: str, domain: str = "") -> list[dict]:
    """Fetch latest headlines from Google News RSS for the company.
    Uses exact-match quotes + domain to avoid false positives for common words."""
    import xml.etree.ElementTree as ET
    from urllib.parse import quote_plus

    # Use exact phrase + domain to disambiguate (e.g., "Rippling" rippling.com)
    domain_hint = f" {domain}" if domain and not domain.endswith(".unknown") else ""
    query = quote_plus(f'"{company_name}"{domain_hint} company')
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []

        root = ET.fromstring(resp.text)
        items = root.findall(".//item")
        results = []
        name_lower = company_name.lower()
        for item in items[:15]:  # fetch more, then filter
            title_el = item.find("title")
            link_el = item.find("link")
            pub_el = item.find("pubDate")
            source_el = item.find("source")
            title = title_el.text if title_el is not None else ""

            # Relevance filter: title must contain the company name as a
            # standalone word/brand, not just as a common English verb/adjective
            if name_lower not in title.lower():
                continue

            results.append({
                "title": title,
                "url": link_el.text if link_el is not None else "",
                "published": pub_el.text if pub_el is not None else "",
                "source": source_el.text if source_el is not None else "",
            })
            if len(results) >= 6:
                break
        return results
    except Exception as e:
        logger.warning("Google News RSS failed for '%s': %s", company_name, safe_error_message(e))
        return []


# ── Helper: Deep website page analysis ───────────────────────────────────────

async def _analyse_website_pages(ai, company_name: str, raw_text: str) -> dict | None:
    """Use GPT-4o to extract structured insights from scraped website pages."""
    if not raw_text or len(raw_text) < 100:
        return None

    system = (
        "You are a B2B sales intelligence analyst. Given raw text scraped from a company's "
        "website (homepage, about, pricing, careers, customers pages), extract actionable "
        "sales intelligence. Respond in this exact format:\n"
        "PRODUCTS: <comma-separated list of main products/services>\n"
        "TARGET_MARKET: <who they sell to — industry, company size, roles>\n"
        "PRICING_MODEL: <pricing approach if visible — freemium, per-seat, enterprise, etc.>\n"
        "KEY_CUSTOMERS: <notable customer names if mentioned>\n"
        "HIRING_SIGNALS: <what roles they're hiring for, what it implies about growth>\n"
        "TECH_CLUES: <any technology/platform mentions relevant to a sales conversation>\n"
        "PAIN_POINTS: <business challenges they claim to solve for their customers>\n"
        "If a field cannot be determined, write 'Not available' after the colon."
    )
    user = f"Company: {company_name}\n\nScraped content:\n{raw_text[:5000]}"

    try:
        response = await ai.complete(system, user, max_tokens=500)
        if not response:
            return None
        # Parse KEY: value lines
        parsed = {}
        for line in response.strip().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip().lower().replace(" ", "_")
                value = value.strip()
                if value and value.lower() != "not available":
                    parsed[key] = value
        return parsed if parsed else None
    except Exception as e:
        logger.warning("Website analysis failed for %s: %s", company_name, safe_error_message(e))
        return None


# ── Helper: Executive briefing ───────────────────────────────────────────────

async def _generate_executive_briefing(
    ai,
    company_profile: dict,
    company_background: dict | None,
    website_analysis: dict | None,
    recent_news: list[dict],
    intent_signals: dict,
    google_news: list[dict],
    hunter_company: dict | None,
    competitive_landscape: list[dict],
    stakeholders: list[dict],
    attendee_intelligence: dict[str, Any],
    why_now_signals: list[dict[str, Any]],
    meeting_type: str,
    kb_context: str = "",
    crm_signals: dict[str, Any] | None = None,
) -> str | None:
    """
    GPT-4o synthesises ALL collected intelligence — web research AND first-party
    CRM signals — into a focused, actionable pre-meeting briefing.
    """
    if crm_signals is None:
        crm_signals = {}

    name = company_profile.get("name", "the company")
    domain = company_profile.get("domain", "")
    industry = company_profile.get("industry", "")
    vertical = company_profile.get("vertical", "")
    employees = company_profile.get("employee_count", "")
    funding = company_profile.get("funding_stage", "")
    icp_tier = company_profile.get("icp_tier", "")
    icp_score = company_profile.get("icp_score", "")
    has_dap = company_profile.get("has_dap", False)
    dap_tool = company_profile.get("dap_tool", "")
    tech_stack = company_profile.get("tech_stack") or {}
    meeting_number = crm_signals.get("meeting_number", 1)

    bg = (company_background or {}).get("extract", "") if company_background else ""

    # ── Website analysis ──────────────────────────────────────────────────────
    wa_lines = ""
    if website_analysis:
        for k, v in website_analysis.items():
            if v and str(v).strip():
                wa_lines += f"  {k}: {str(v)[:200]}\n"

    # ── Market signals ────────────────────────────────────────────────────────
    news_lines = "\n".join(
        f"- {n.get('title', '')}" for n in (recent_news or [])[:4]
    ) or "None found."

    gnews_lines = "\n".join(
        f"- [{n.get('source', '')}] {n.get('title', '')}" for n in (google_news or [])[:3]
    ) or "None."

    intent_lines = ""
    for category in ("hiring", "funding", "product"):
        items = (intent_signals or {}).get(category, [])
        if items:
            intent_lines += f"\n  {category.title()}:"
            for s in items[:2]:
                intent_lines += f"\n    - {s.get('title', s.get('snippet', ''))}"
    intent_lines = intent_lines or "\n  No strong buying signals detected."

    why_now_lines = "\n".join(
        f"- {item.get('detail', '')}"
        for item in (why_now_signals or [])[:4]
        if item.get("detail")
    ) or "None."

    comp_lines = "\n".join(
        f"- {c.get('title') or c.get('name') or c.get('competitor', '')}: {c.get('snippet') or c.get('summary', '')}"
        for c in (competitive_landscape or [])[:5]
        if c.get("title") or c.get("name") or c.get("competitor")
    ) or "No competitor mentions found."

    # ── Stakeholders ──────────────────────────────────────────────────────────
    stakeholder_cards = (attendee_intelligence or {}).get("stakeholder_cards", [])
    stakeholder_lines = ""
    for card in stakeholder_cards[:6]:
        stakeholder_lines += (
            f"\n  - {card.get('name')} | {card.get('title') or 'Unknown'} | "
            f"{card.get('role_label', 'Stakeholder')} [{card.get('status')}]\n"
            f"    Likely focus: {card.get('likely_focus', '')}\n"
            f"    Talk track: {card.get('talk_track', '')}"
        )
    stakeholder_lines = stakeholder_lines or "  No attendee data. Use discovered contacts below."

    all_contacts_lines = "\n".join(
        f"  - {s.get('name')} | {s.get('title', 'Unknown')} | {s.get('persona', 'unknown')} | "
        f"{'email verified' if s.get('email_verified') else 'email unverified'}"
        for s in (stakeholders or [])[:8]
    ) or "  No contacts mapped yet."

    committee = (attendee_intelligence or {}).get("committee_coverage", {})
    missing_roles = ", ".join(
        item.get("label", "") for item in (committee or {}).get("meeting_gaps", [])
    ) or "None"

    # ── CRM first-party signals ───────────────────────────────────────────────
    prior_meetings = crm_signals.get("prior_meetings", [])
    prior_meeting_lines = ""
    for pm in prior_meetings[:3]:
        scheduled_at = pm.get("scheduled_at")
        scheduled_label = scheduled_at[:10] if isinstance(scheduled_at, str) and scheduled_at else "unknown date"
        prior_meeting_lines += f"\n  [{pm.get('meeting_type', '?').upper()}] {pm.get('title', 'Meeting')} ({scheduled_label})"
        if pm.get("ai_summary"):
            prior_meeting_lines += f"\n    Summary: {pm['ai_summary'][:200]}"
        if pm.get("transcript_summary"):
            prior_meeting_lines += f"\n    Transcript: {pm['transcript_summary'][:300]}"
        if pm.get("next_steps"):
            prior_meeting_lines += f"\n    Agreed next steps: {pm['next_steps'][:150]}"
        if pm.get("what_went_wrong"):
            prior_meeting_lines += f"\n    What went wrong: {pm['what_went_wrong'][:150]}"
    prior_meeting_lines = prior_meeting_lines or "  None — this is the first meeting."

    email_threads = crm_signals.get("email_threads", [])
    email_lines = ""
    for et in email_threads[:6]:
        summary = et.get("ai_summary") or et.get("snippet", "")[:100]
        if summary:
            email_lines += f"\n  [{et.get('source', 'email')}] {et.get('subject', '(no subject)')}: {summary}"
    email_lines = email_lines or "  No email activity found."

    call_history = crm_signals.get("call_history", [])
    call_lines = ""
    answered = sum(1 for c in call_history if c.get("outcome") == "answered")
    missed = sum(1 for c in call_history if c.get("outcome") in ("missed", "voicemail"))
    if call_history:
        call_lines = f"\n  {len(call_history)} calls total | {answered} answered | {missed} missed/voicemail"
        for c in call_history[:3]:
            dur = f"{c['duration_seconds'] // 60}m" if c.get("duration_seconds") else "?"
            summary = c.get("ai_summary") or ""
            call_lines += f"\n  - {c.get('outcome', '?')} | {dur} | {summary[:120]}"
    call_lines = call_lines or "  No call history."

    outreach = crm_signals.get("outreach_activity", [])
    outreach_lines = "\n".join(
        f"  - {o.get('subject', '?')}: {o.get('outcome', '')}"
        for o in outreach[:4]
    ) or "  No Instantly outreach found."

    deal_ctx = crm_signals.get("deal_context") or {}
    deal_lines = ""
    if deal_ctx:
        deal_lines = (
            f"\n  Stage: {deal_ctx.get('stage', '?')} | "
            f"Health: {deal_ctx.get('health', '?')} | "
            f"Days in stage: {deal_ctx.get('days_in_stage', '?')} | "
            f"Priority: {deal_ctx.get('priority', '?')}"
        )
        if deal_ctx.get("last_activity_at"):
            deal_lines += f"\n  Last activity: {deal_ctx['last_activity_at'][:10]}"

    tech_stack_str = ", ".join(f"{k}: {v}" for k, v in (tech_stack or {}).items()) or "None detected"

    system = (
        "You are a senior enterprise AE at Beacon.li preparing for a sales meeting. "
        "Beacon automates enterprise SaaS deployments — it removes implementation drag, manual coordination, and rollout risk.\n\n"
        "Write a FOCUSED pre-meeting intelligence brief using ONLY the information provided. "
        "Omit any section where you have no signal. Be specific, direct, and actionable. "
        "Every bullet should answer 'so what?' for the rep.\n\n"
        "HARD RULES:\n"
        "- Never assert that the company LACKS a tool, platform, or process (no 'no X detected', "
        "'they have no Y', 'likely manual'). You cannot verify absence. If a capability gap matters, "
        "raise it as a question to confirm live — never as a stated fact.\n"
        "- Only discuss people who are ATTENDING this meeting. Do NOT mention, infer, or invent "
        "anyone who is not in the meeting — no 'not in the room', 'also critical', or economic-buyer "
        "guesses.\n\n"
        "Use exactly these sections (skip a section only if you have zero signal for it):\n\n"
        "## Account Snapshot\n"
        "Who they are, what domain they play in, their scale, and the core problem Beacon solves for them (1-2 sentences).\n\n"
        "## Problem Statement & Fit\n"
        "What pain does this company have that Beacon directly solves? Reference their industry, scale, "
        "and product/implementation complexity — only facts you can verify; never claim they lack a tool.\n\n"
        "## Why Now\n"
        "Bullet the strongest timing signals — funding, hiring surges, product launches, recent news, deal urgency.\n\n"
        "## What We Know From Past Conversations\n"
        "Synthesise what was said in prior meetings, email threads, and calls. What did they care about? What was agreed? "
        "What was left unresolved? (Skip if no prior touchpoints.)\n\n"
        "## Their People\n"
        "ONLY the people attending this meeting (see 'STAKEHOLDERS IN THIS MEETING'). For each attendee: "
        "name, role, what they likely care about, and how to tailor your message. Do NOT add a 'not in "
        "the room' or 'also critical' subsection, and do not invent attendees.\n\n"
        "## Deal Momentum & Engagement\n"
        "Call pickup rate, email response patterns, Instantly engagement. Are they warm or cold? Any signs of multi-threading?\n\n"
        "## Competitive Framing\n"
        "Name the relevant alternatives likely in the account. Explain how Beacon should be framed against them in one practical sales angle.\n\n"
        "## Risks & Objections to Prepare For\n"
        "What could stall or kill this deal? How to address each.\n\n"
        "## Meeting Strategy\n"
        "What to open with, what to cover, what NOT to bring up. Meeting-type specific guidance.\n\n"
        "## Questions to Ask\n"
        "4-5 specific, non-generic discovery questions for THIS company and meeting number.\n\n"
        "No generic advice. No filler. If you don't have signal for something, omit it."
    )

    user = f"""
ACCOUNT: {name} ({domain})
Industry: {industry} | Vertical: {vertical} | Employees: {employees} | Funding: {funding}
ICP Score: {icp_score} | ICP Tier: {icp_tier}
{f'Adoption tooling in use: {dap_tool}' if (has_dap and dap_tool) else ''}
Tech Stack: {tech_stack_str}
Meeting #{meeting_number} | Type: {meeting_type}

BACKGROUND (website):
{bg[:400] if bg else 'Not available'}

WEBSITE ANALYSIS:
{wa_lines or '  Not available'}

RECENT NEWS & SIGNALS:
{news_lines}

GOOGLE NEWS:
{gnews_lines}

INTENT SIGNALS:{intent_lines}

WHY NOW:
{why_now_lines}

COMPETITIVE LANDSCAPE:
{comp_lines}

STAKEHOLDERS IN THIS MEETING (the ONLY people to profile under "Their People"):
{stakeholder_lines}

PRIOR MEETINGS:
{prior_meeting_lines}

EMAIL THREAD HISTORY:
{email_lines}

CALL HISTORY:
{call_lines}

OUTREACH (Instantly):
{outreach_lines}

DEAL STATUS:{deal_lines or '  No deal linked.'}
{kb_context}
""".strip()

    try:
        result = await ai.complete(system, user, max_tokens=1200)
        return result
    except Exception as e:
        logger.warning("Executive briefing generation failed: %s", safe_error_message(e))
        return None


def _parse_game_plan(raw: str | None) -> list[str]:
    """Parse the model's game-plan output into a clean list of action strings.
    Tolerant of code fences and stray bullets; dedups and caps at 4."""
    if not raw:
        return []
    import json as _json
    text = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    text = re.sub(r"\s*```$", "", text)
    items: list[str] = []
    try:
        parsed = _json.loads(text)
        if isinstance(parsed, list):
            items = [str(x).strip() for x in parsed if str(x).strip()]
    except Exception:
        for line in text.splitlines():
            cleaned = line.strip().lstrip("-*•").strip()
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", cleaned)
            if cleaned:
                items.append(cleaned)
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it[:200])
        if len(out) >= 4:
            break
    return out


async def _generate_meeting_game_plan(
    ai,
    *,
    executive_briefing: str | None,
    meeting_type: str,
    crm_signals: dict[str, Any] | None,
    attendee_intelligence: dict[str, Any] | None,
) -> list[str]:
    """Distil the briefing into 3-4 SPECIFIC actions for THIS meeting.

    Replaces the old templated recommendations, which slotted a (often mangled)
    attendee name into the same canned sentences for every meeting — "Start
    discovery around <name>'s likely focus…", "Committee gap: bring in Economic
    Buyer…" — identical across accounts and useless. This grounds the plan in the
    briefing + real CRM signals and returns [] rather than padding with filler.
    """
    if not executive_briefing or not executive_briefing.strip():
        return []
    crm_signals = crm_signals or {}
    attendee_intelligence = attendee_intelligence or {}

    deal_ctx = crm_signals.get("deal_context") or {}
    meeting_number = crm_signals.get("meeting_number", 1)
    stakeholder_cards = attendee_intelligence.get("stakeholder_cards", [])
    people = "; ".join(
        f"{c.get('name')} ({c.get('title') or c.get('role_label') or 'unknown role'})"
        for c in stakeholder_cards[:5]
        if c.get("name") and c.get("name") != "Unknown attendee"
    ) or "not known"
    prior = crm_signals.get("prior_meetings", [])
    last_next_steps = ""
    for pm in prior[:2]:
        if pm.get("next_steps"):
            last_next_steps += f"- {str(pm['next_steps'])[:160]}\n"

    system = (
        "You are a senior enterprise AE at Beacon.li. From the pre-meeting brief below, "
        "write the 3-4 MOST IMPORTANT, concrete actions for THIS specific meeting — a tight game plan.\n\n"
        "Hard rules:\n"
        "- Every bullet MUST reference a specific detail from the brief: a person by name, the deal "
        "stage, a prior commitment/next step, a named competitor, or a concrete signal.\n"
        "- NO generic sales advice. Ban phrases like 'build rapport', 'validate pain', 'bring in the "
        "economic buyer', 'land Beacon as orchestration' unless tied to a specific named gap or person.\n"
        "- Ignore irrelevant or negative news (e.g. an executive resigning) — never suggest opening with it.\n"
        "- Each bullet must start with a verb, be <= 22 words, and be something the rep DOES or ASKS.\n"
        "- If the brief lacks signal for a real, specific action, return FEWER bullets. Never pad.\n"
        "- Return ONLY a JSON array of strings (0-4 items). No prose, no keys."
    )
    user = (
        f"MEETING: type={meeting_type}, meeting #{meeting_number}\n"
        f"DEAL: stage={deal_ctx.get('stage', '?')}, health={deal_ctx.get('health', '?')}, "
        f"days_in_stage={deal_ctx.get('days_in_stage', '?')}\n"
        f"PEOPLE IN THE ROOM: {people}\n"
        f"PRIOR AGREED NEXT STEPS:\n{last_next_steps or '  none recorded'}\n\n"
        f"BRIEF:\n{executive_briefing[:4000]}"
    )
    try:
        raw = await ai.complete(system, user, max_tokens=400)
    except Exception as e:
        logger.warning("Meeting game plan generation failed: %s", safe_error_message(e))
        return []
    return _parse_game_plan(raw)
