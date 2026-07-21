"""
Account Sourcing — CSV upload, tiered enrichment, re-enrich.

Endpoints:
  POST /upload              Upload CSV → create batch + companies → queue enrichment
  GET  /batches/{id}        Poll batch status
  GET  /batches/{id}/companies  Companies in a batch with enrichment data
  GET  /companies           All sourced companies (across batches)
  PUT  /companies/{id}      Update sourcing owner / feedback fields
  GET  /export              Export sourced companies to CSV
  POST /companies/{id}/re-enrich     Re-run standard pipeline
  GET  /companies/{id}/contacts      Contacts discovered for a company
  POST /contacts/{id}/re-enrich      Re-enrich a single contact
  POST /companies/{id}/push-instantly  Push contacts to Instantly (placeholder)
"""
import csv
import io
import logging
import re
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import load_only
from sqlmodel import select

from app.core.dependencies import AdminUser, CurrentUser, DBSession, Pagination
from app.models.angel import AngelInvestor, AngelMapping
from app.models.company import Company, CompanyRead, CompanySourcingSummary, CompanyUpdate
from app.models.contact import Contact, ContactRead, ContactUpdate
from app.models.deal import Deal
from app.models.sourcing_batch import SourcingBatch, SourcingBatchRead
from app.models.user import User
from app.repositories.company import CompanyRepository, company_visibility_filter
from app.repositories.contact import visible_contact_restriction
from app.schemas.common import PaginatedResponse
from app.services.account_sourcing import (
    _clean_company_name,
    account_priority_snapshot,
    append_company_activity_log,
    is_priority_stakeholder_candidate,
    merge_company_from_upload,
    parse_tabular_file,
    refresh_contact_sequence_plan,
    refresh_company_prospecting_fields,
    row_to_company_fields,
    row_to_contact_fields,
)
from app.services.data_reset import (
    reset_account_sourcing_data,
    reset_prospecting_data,
    reset_workspace_data,
)
from app.services.contact_tracking import apply_contact_tracking, to_contact_read
from app.services.icp_scorer import score_company
from app.models.recotap import RECOTAP_ENGAGEMENT_LEVELS, RECOTAP_JOURNEY_STAGES, RecotapAccount
from app.services.recotap import (
    normalize_domain as recotap_domain,
    pull_into_db as recotap_pull,
    push_crm_status as recotap_push_status,
    seed_mock_signals as recotap_seed,
    signals_by_domain as recotap_signals,
    sync_crm_journey as recotap_crm_sync,
)

router = APIRouter(prefix="/account-sourcing", tags=["account-sourcing"])

logger = logging.getLogger(__name__)


class ManualCompanyCreate(BaseModel):
    name: str
    domain: str | None = None


class BatchConfirmPayload(BaseModel):
    force: bool = False


def _parse_multi_query(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _apply_text_multi_filter(stmt, column, raw_value: str | None):
    values = _parse_multi_query(raw_value)
    if not values:
        return stmt

    include_empty = "__empty__" in values
    filtered_values = [value for value in values if value != "__empty__"]
    clauses = []
    if filtered_values:
        clauses.append(column.in_(filtered_values))
    if include_empty:
        clauses.append(or_(column.is_(None), column == ""))
    return stmt.where(or_(*clauses)) if clauses else stmt


def _account_sourcing_visibility_filter():
    hidden_clickup_import = Company.enrichment_sources.contains(
        {"clickup_import": {"hidden_from_account_sourcing": True}}
    )
    return and_(
        ~hidden_clickup_import,
        or_(
            Company.sourcing_batch_id.isnot(None),
            Company.enrichment_sources.contains({"prospect_import_placeholder": {}}),
            select(Deal.id).where(Deal.company_id == Company.id).exists(),
        ),
    )


def _can_see_company(company: Company, user) -> bool:
    """Python mirror of ``company_visibility_filter`` for single-object guards.

    Admins see every company (including ``not_a_fit``); a non-admin only sees a
    company they own (AE or SDR) that is not flagged ``not_a_fit``. Use on
    single-company detail/update routes to 404 a company the caller can't see
    (so existence isn't leaked).
    """
    if user.role == "admin":
        return True
    owns = company.assigned_to_id == user.id or company.sdr_id == user.id
    return owns and company.account_status != "not_a_fit"


async def _auto_create_angel_records(
    session,
    company: Company,
    contact: Contact,
) -> int:
    """
    Read warm_paths and investor data from company.prospecting_profile,
    get-or-create AngelInvestor records, and create AngelMapping links.
    Also populates the company's investor text columns.
    Returns the number of mappings created.
    """
    # `prospecting_profile` stores the generated warm-intro/investor intelligence
    # as JSON. This helper turns the useful parts into relational records so the
    # rest of the app can query and edit them normally.
    profile = company.prospecting_profile if isinstance(company.prospecting_profile, dict) else {}
    warm_paths = profile.get("warm_paths") if isinstance(profile.get("warm_paths"), list) else []
    investors = profile.get("investors") if isinstance(profile.get("investors"), dict) else {}

    # ── Populate company investor text columns ──────────────────────────
    ownership = profile.get("ownership_stage")
    if ownership and not company.ownership_stage:
        company.ownership_stage = str(ownership)[:500]

    pe_list = investors.get("pe") if isinstance(investors.get("pe"), list) else []
    if pe_list and not company.pe_investors:
        company.pe_investors = "; ".join(str(item).strip() for item in pe_list if str(item).strip())

    vc_list = investors.get("vc_growth") if isinstance(investors.get("vc_growth"), list) else []
    if vc_list and not company.vc_investors:
        company.vc_investors = "; ".join(str(item).strip() for item in vc_list if str(item).strip())

    strategic_list = investors.get("strategic") if isinstance(investors.get("strategic"), list) else []
    if strategic_list and not company.strategic_investors:
        company.strategic_investors = "; ".join(str(item).strip() for item in strategic_list if str(item).strip())

    if any([pe_list, vc_list, strategic_list, ownership]):
        session.add(company)

    # ── Create angel investor + mapping records ─────────────────────────
    mappings_created = 0
    for rank, connector in enumerate(warm_paths, start=1):
        if not isinstance(connector, dict):
            continue
        angel_name = str(connector.get("name") or "").strip()
        if not angel_name:
            continue

        # Get or create angel investor (case-insensitive match)
        existing_angel = (
            await session.execute(
                select(AngelInvestor).where(
                    func.lower(AngelInvestor.name) == angel_name.lower()
                ).limit(1)
            )
        ).scalars().first()

        if existing_angel:
            angel = existing_angel
        else:
            angel = AngelInvestor(name=angel_name)
            session.add(angel)
            await session.flush()

        # Check for duplicate mapping (same contact + angel)
        existing_mapping = (
            await session.execute(
                select(AngelMapping).where(
                    AngelMapping.contact_id == contact.id,
                    AngelMapping.angel_investor_id == angel.id,
                ).limit(1)
            )
        ).scalars().first()

        if existing_mapping:
            continue

        strength_raw = connector.get("strength")
        strength = int(strength_raw) if strength_raw is not None else 3
        strength = max(1, min(5, strength))

        mapping = AngelMapping(
            contact_id=contact.id,
            company_id=company.id,
            angel_investor_id=angel.id,
            strength=strength,
            rank=min(rank, 10),
            connection_path=connector.get("connection_path"),
            why_it_works=connector.get("why_it_works"),
        )
        session.add(mapping)
        mappings_created += 1

    return mappings_created


@router.post("/reset/{scope}")
async def reset_sourcing_data(scope: str, _admin: AdminUser, session: DBSession = None):
    # These reset scopes intentionally target different slices of the GTM app so
    # admins can clear one workflow without wiping unrelated work.
    normalized = (scope or "").strip().lower()
    if normalized == "account-sourcing":
        summary = await reset_account_sourcing_data(session)
    elif normalized == "prospecting":
        summary = await reset_prospecting_data(session)
    elif normalized == "workspace":
        summary = await reset_workspace_data(session)
    else:
        raise HTTPException(status_code=400, detail="Unsupported reset scope")
    return {"scope": normalized, "summary": summary}


def _joined_signal_values(items: object) -> str:
    if not isinstance(items, list):
        return ""
    values: list[str] = []
    for item in items:
        if isinstance(item, dict):
            value = str(item.get("value") or item.get("key") or "").strip()
        else:
            value = str(item).strip()
        if value:
            values.append(value)
    return " | ".join(values)


def _icp_analysis(company: Company) -> dict:
    cache = company.enrichment_cache if isinstance(company.enrichment_cache, dict) else {}
    entry = cache.get("icp_analysis") if isinstance(cache.get("icp_analysis"), dict) else {}
    data = entry.get("data") if isinstance(entry.get("data"), dict) else None
    return data if isinstance(data, dict) else entry


def _company_export_row(company: Company) -> dict[str, str]:
    # Prefer uploaded analyst values when they exist, but fall back to generated
    # research so exports stay usable for both manual and AI-enriched batches.
    import_block = company.enrichment_sources.get("import") if isinstance(company.enrichment_sources, dict) else {}
    raw_row = import_block.get("raw_row") if isinstance(import_block, dict) and isinstance(import_block.get("raw_row"), dict) else {}
    uploaded_analyst = import_block.get("uploaded_analyst") if isinstance(import_block, dict) and isinstance(import_block.get("uploaded_analyst"), dict) else {}
    analyst = uploaded_analyst or (import_block.get("analyst") if isinstance(import_block, dict) and isinstance(import_block.get("analyst"), dict) else {})
    generated_analyst = import_block.get("generated_analyst") if isinstance(import_block, dict) and isinstance(import_block.get("generated_analyst"), dict) else {}
    uploaded_signals = import_block.get("uploaded_signals") if isinstance(import_block, dict) and isinstance(import_block.get("uploaded_signals"), dict) else {}
    generated_signals = import_block.get("generated_signals") if isinstance(import_block, dict) and isinstance(import_block.get("generated_signals"), dict) else {}
    profile = company.prospecting_profile if isinstance(company.prospecting_profile, dict) else {}
    outreach_plan = company.outreach_plan if isinstance(company.outreach_plan, dict) else {}
    cache = company.enrichment_cache if isinstance(company.enrichment_cache, dict) else {}
    icp_entry = cache.get("icp_analysis") if isinstance(cache.get("icp_analysis"), dict) else {}
    icp = icp_entry.get("data") if isinstance(icp_entry, dict) and isinstance(icp_entry.get("data"), dict) else {}
    research_quality_entry = cache.get("research_quality") if isinstance(cache.get("research_quality"), dict) else {}
    research_quality = research_quality_entry.get("data") if isinstance(research_quality_entry, dict) and isinstance(research_quality_entry.get("data"), dict) else {}
    priority = account_priority_snapshot(company)

    row = {
        "company_id": str(company.id),
        "name": company.name,
        "domain": company.domain,
        "industry": company.industry or "",
        "employee_count": str(company.employee_count or ""),
        "funding_stage": company.funding_stage or "",
        "arr_estimate": str(company.arr_estimate or ""),
        "icp_score": str(company.icp_score or ""),
        "icp_tier": company.icp_tier or "",
        "assigned_rep": company.assigned_rep or "",
        "assigned_rep_email": company.assigned_rep_email or "",
        "assigned_rep_name": company.assigned_rep_name or "",
        "outreach_status": company.outreach_status or "",
        "disposition": company.disposition or "",
        "rep_feedback": company.rep_feedback or "",
        "recommended_outreach_lane": company.recommended_outreach_lane or "",
        "instantly_campaign_id": company.instantly_campaign_id or "",
        "account_thesis": company.account_thesis or "",
        "why_now": company.why_now or "",
        "beacon_angle": company.beacon_angle or "",
        "prospecting_recommended_strategy": str(profile.get("recommended_outreach_strategy") or ""),
        "prospecting_conversation_starter": str(profile.get("conversation_starter") or ""),
        "prospecting_warm_path_count": str(len(profile.get("warm_paths") or []) if isinstance(profile.get("warm_paths"), list) else 0),
        "outreach_owner_email": str(outreach_plan.get("owner_email") or ""),
        "outreach_sequence_family": str(outreach_plan.get("sequence_family") or ""),
        "outreach_next_best_action": str(outreach_plan.get("next_best_action") or ""),
        "last_outreach_at": company.last_outreach_at.isoformat() if company.last_outreach_at else "",
        "priority_score": str(priority["priority_score"]),
        "priority_band": str(priority["priority_band"]),
        "interest_level": str(priority["interest_level"]),
        "description": company.description or "",
        "uploaded_classification": str(analyst.get("classification") or ""),
        "uploaded_fit_type": str(analyst.get("fit_type") or ""),
        "uploaded_confidence": str(analyst.get("confidence") or ""),
        "uploaded_icp_score_0_10": str(analyst.get("icp_fit_score") or ""),
        "uploaded_intent_score_0_10": str(analyst.get("intent_score") or ""),
        "uploaded_icp_why": str(analyst.get("icp_why") or ""),
        "uploaded_intent_why": str(analyst.get("intent_why") or ""),
        "uploaded_positive_signals": _joined_signal_values(uploaded_signals.get("positive") if isinstance(uploaded_signals, dict) else []),
        "uploaded_negative_signals": _joined_signal_values(uploaded_signals.get("negative") if isinstance(uploaded_signals, dict) else []),
        "researched_company_overview": str(icp.get("company_overview") or generated_analyst.get("company_overview") or company.description or ""),
        "researched_industry": str(icp.get("industry") or generated_analyst.get("industry") or company.industry or ""),
        "researched_category": str(icp.get("category") or generated_analyst.get("category") or company.vertical or ""),
        "researched_core_focus": str(icp.get("core_focus") or generated_analyst.get("core_focus") or ""),
        "researched_fit_type": str(icp.get("fit_type") or generated_analyst.get("fit_type") or ""),
        "researched_classification": str(icp.get("classification") or generated_analyst.get("classification") or ""),
        "researched_confidence": str(icp.get("confidence") or generated_analyst.get("confidence") or ""),
        "researched_financial_capacity_met": str(icp.get("financial_capacity_met") or generated_analyst.get("financial_capacity_met") or ""),
        "researched_revenue_funding": str(icp.get("revenue_funding") or generated_analyst.get("revenue_funding") or ""),
        "researched_icp_score_0_10": str(icp.get("icp_fit_score") or generated_analyst.get("icp_fit_score") or ""),
        "researched_icp_why": str(icp.get("icp_why") or generated_analyst.get("icp_why") or ""),
        "researched_intent_score_0_10": str(icp.get("intent_score") or generated_analyst.get("intent_score") or ""),
        "researched_intent_why": str(icp.get("intent_why") or generated_analyst.get("intent_why") or ""),
        "researched_ps_impl_hiring": str(icp.get("ps_impl_hiring") or ""),
        "researched_leadership_org_moves": str(icp.get("leadership_org_moves") or ""),
        "researched_pr_funding_expansion": str(icp.get("pr_funding_expansion") or ""),
        "researched_events_thought_leadership": str(icp.get("events_thought_leadership") or ""),
        "researched_reviews_case_studies": str(icp.get("reviews_case_studies") or ""),
        "researched_internal_ai_overlap": str(icp.get("internal_ai_overlap") or ""),
        "researched_strategic_constraints": str(icp.get("strategic_constraints") or ""),
        "researched_ps_cs_contraction": str(icp.get("ps_cs_contraction") or ""),
        "researched_build_vs_buy": str(icp.get("build_vs_buy") or ""),
        "researched_ai_acquisition": str(icp.get("ai_acquisition") or ""),
        "researched_employee_count": str(icp.get("employee_count") or generated_analyst.get("employee_count") or company.employee_count or ""),
        "researched_funding_stage": str(icp.get("funding_stage") or generated_analyst.get("funding_stage") or company.funding_stage or ""),
        "researched_arr_estimate": str(icp.get("arr_estimate") or generated_analyst.get("arr_estimate") or company.arr_estimate or ""),
        "researched_committee_coverage": str(icp.get("committee_coverage") or generated_analyst.get("committee_coverage") or profile.get("committee_coverage") or ""),
        "researched_open_gaps": " | ".join(str(item).strip() for item in (icp.get("open_gaps") or generated_analyst.get("open_gaps") or profile.get("open_gaps") or []) if str(item).strip()) if isinstance((icp.get("open_gaps") or generated_analyst.get("open_gaps") or profile.get("open_gaps")), list) else str(icp.get("open_gaps") or generated_analyst.get("open_gaps") or profile.get("open_gaps") or ""),
        "researched_icp_personas": " | ".join(
            " - ".join(part for part in [str(item.get("title") or "").strip(), str(item.get("name") or "").strip(), str(item.get("relevance") or "").strip()] if part)
            for item in (icp.get("icp_personas") or profile.get("icp_personas") or [])
            if isinstance(item, dict)
        ),
        "researched_account_thesis": str(icp.get("account_thesis") or generated_analyst.get("account_thesis") or company.account_thesis or ""),
        "researched_why_now": str(icp.get("why_now") or generated_analyst.get("why_now") or company.why_now or ""),
        "researched_beacon_angle": str(icp.get("beacon_angle") or generated_analyst.get("beacon_angle") or company.beacon_angle or ""),
        "researched_recommended_outreach_strategy": str(icp.get("recommended_outreach_strategy") or generated_analyst.get("recommended_outreach_strategy") or profile.get("recommended_outreach_strategy") or ""),
        "researched_conversation_starter": str(icp.get("conversation_starter") or generated_analyst.get("conversation_starter") or profile.get("conversation_starter") or ""),
        "researched_next_steps": str(icp.get("next_steps") or generated_analyst.get("next_steps") or profile.get("next_steps") or ""),
        "researched_generated_positive_signals": _joined_signal_values(generated_signals.get("positive") if isinstance(generated_signals, dict) else []),
        "researched_generated_negative_signals": _joined_signal_values(generated_signals.get("negative") if isinstance(generated_signals, dict) else []),
        "researched_evidence_level": str(research_quality.get("evidence_level") or ""),
        "researched_evidence_score": str(research_quality.get("evidence_score") or ""),
        "enriched_at": company.enriched_at.isoformat() if company.enriched_at else "",
        "created_at": company.created_at.isoformat(),
        "updated_at": company.updated_at.isoformat(),
    }

    if isinstance(raw_row, dict):
        for key, value in raw_row.items():
            row[f"source_{key}"] = str(value or "")

    return row


def _contact_export_row(company: Company, contact: Contact) -> dict[str, str]:
    profile = company.prospecting_profile if isinstance(company.prospecting_profile, dict) else {}
    warm_path = contact.warm_intro_path if isinstance(contact.warm_intro_path, dict) else {}
    talking_points = contact.talking_points if isinstance(contact.talking_points, list) else []
    enrichment = contact.enrichment_data if isinstance(contact.enrichment_data, dict) else {}
    sequence_plan = enrichment.get("sequence_plan") if isinstance(enrichment.get("sequence_plan"), dict) else {}
    sequence_steps = sequence_plan.get("steps") if isinstance(sequence_plan.get("steps"), list) else []
    return {
        "company_id": str(company.id),
        "company_name": company.name,
        "company_domain": company.domain,
        "company_owner_email": company.assigned_rep_email or "",
        "company_owner_name": company.assigned_rep_name or company.assigned_rep or "",
        "company_outreach_lane": company.recommended_outreach_lane or "",
        "contact_id": str(contact.id),
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "full_name": f"{contact.first_name} {contact.last_name}".strip(),
        "title": contact.title or "",
        "email": contact.email or "",
        "email_verified": "yes" if contact.email_verified else "no",
        "linkedin_url": contact.linkedin_url or "",
        "persona": contact.persona or "",
        "persona_type": contact.persona_type or "",
        "assigned_rep_email": contact.assigned_rep_email or company.assigned_rep_email or "",
        "outreach_lane": contact.outreach_lane or company.recommended_outreach_lane or "",
        "sequence_status": contact.sequence_status or "",
        "instantly_status": contact.instantly_status or "",
        "instantly_campaign_id": contact.instantly_campaign_id or company.instantly_campaign_id or "",
        "warm_intro_strength": str(contact.warm_intro_strength or ""),
        "warm_intro_name": str(warm_path.get("name") or ""),
        "warm_intro_path": str(warm_path.get("connection_path") or ""),
        "warm_intro_why": str(warm_path.get("why_it_works") or ""),
        "conversation_starter": contact.conversation_starter or str(profile.get("conversation_starter") or ""),
        "personalization_notes": contact.personalization_notes or "",
        "talking_points": " | ".join(str(item).strip() for item in talking_points if str(item).strip()),
        "account_thesis": company.account_thesis or "",
        "why_now": company.why_now or "",
        "beacon_angle": company.beacon_angle or "",
        "sequence_family": str(sequence_plan.get("sequence_family") or ""),
        "sequence_goal": str(sequence_plan.get("goal") or ""),
        "sequence_hooks": " | ".join(str(item).strip() for item in sequence_plan.get("personalization_hooks", []) if str(item).strip()) if isinstance(sequence_plan.get("personalization_hooks"), list) else "",
        "sequence_step_1": str(sequence_steps[0].get("objective") or "") if len(sequence_steps) > 0 and isinstance(sequence_steps[0], dict) else "",
        "sequence_step_2": str(sequence_steps[1].get("objective") or "") if len(sequence_steps) > 1 and isinstance(sequence_steps[1], dict) else "",
        "sequence_step_3": str(sequence_steps[2].get("objective") or "") if len(sequence_steps) > 2 and isinstance(sequence_steps[2], dict) else "",
        "sequence_step_4": str(sequence_steps[3].get("objective") or "") if len(sequence_steps) > 3 and isinstance(sequence_steps[3], dict) else "",
        "sequence_step_5": str(sequence_steps[4].get("objective") or "") if len(sequence_steps) > 4 and isinstance(sequence_steps[4], dict) else "",
    }


# ── CSV Upload ─────────────────────────────────────────────────────────────────

# Headers that indicate the CSV already has rich ICP/analyst data.
# If a CSV has NONE of these (just company name + maybe domain), we trigger
# the full ICP intelligence pipeline to research each company from scratch.
_RICH_DATA_HEADERS = {
    "industry", "sector", "employee_count", "employees", "headcount",
    "funding_stage", "stage", "round", "series", "total funding",
    "annual revenue", "arr", "revenue", "icp fit score", "intent score",
    "classification", "fit type", "confidence", "core focus",
    "icp why", "intent why", "ps impl hiring", "reviews case studies",
    "category", "description", "overview", "what they do",
}


def _is_minimal_upload(rows: list[dict]) -> bool:
    """
    Detect if the uploaded CSV is 'minimal' — just company names (and maybe
    domain/industry) without detailed ICP/analyst columns.

    When minimal, we trigger the full ICP intelligence pipeline that researches
    each company using web search, Apollo, and Claude.
    """
    if not rows:
        return False

    # Normalize all headers in the first row
    from app.services.account_sourcing import _normalize_header
    headers = {_normalize_header(h) for h in rows[0].keys()}

    # Count how many "rich data" headers are present with actual values
    rich_count = 0
    for row in rows[:3]:  # Sample first few rows
        for header, value in row.items():
            normalized = _normalize_header(header)
            if normalized in _RICH_DATA_HEADERS and value and str(value).strip():
                rich_count += 1
                break  # One rich value per row is enough

    # If fewer than half the sampled rows have rich data, it's minimal
    sample_size = min(len(rows), 3)
    return rich_count < (sample_size / 2)


def _normalized_verdict(value: object) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _build_upload_verdict_summary(rows: list[dict]) -> dict[str, object]:
    counts = {
        "target": 0,
        "watch": 0,
        "non_target": 0,
        "unknown": 0,
    }
    for row in rows:
        fields = row_to_company_fields(row)
        import_block = fields.get("enrichment_sources") if isinstance(fields.get("enrichment_sources"), dict) else {}
        analyst = import_block.get("import", {}).get("analyst") if isinstance(import_block.get("import"), dict) else {}
        verdict = _normalized_verdict((analyst or {}).get("classification"))
        if verdict == "target":
            counts["target"] += 1
        elif verdict == "watch":
            counts["watch"] += 1
        elif verdict in {"non-target", "bad-fit", "do-not-target"}:
            counts["non_target"] += 1
        else:
            counts["unknown"] += 1

    has_uploaded_verdicts = (counts["target"] + counts["watch"] + counts["non_target"]) > 0
    pass_auto = _is_minimal_upload(rows) or (
        has_uploaded_verdicts and counts["target"] > 0 and counts["non_target"] == 0
    ) or (not has_uploaded_verdicts)
    requires_confirmation = not pass_auto and has_uploaded_verdicts
    message = (
        "TAL verdicts look safe to enrich."
        if pass_auto
        else "Some uploaded accounts are marked as non-target or missing a clear target verdict."
    )
    return {
        **counts,
        "has_uploaded_verdicts": has_uploaded_verdicts,
        "pass_auto": pass_auto,
        "requires_confirmation": requires_confirmation,
        "message": message,
    }


def _estimate_batch_eta_seconds(batch: SourcingBatch) -> int | None:
    total = int(batch.total_rows or 0)
    processed = int(batch.processed_rows or 0)
    if total <= 0 or processed <= 0 or processed >= total:
        return 0 if processed >= total and total > 0 else None
    elapsed = max((datetime.utcnow() - batch.created_at).total_seconds(), 1)
    per_row = elapsed / processed
    remaining = max(total - processed, 0)
    return int(per_row * remaining)


async def _batch_contacts_found(session, batch_id: UUID) -> int:
    return int(
        (
            await session.execute(
                select(func.count(Contact.id))
                .join(Company, Contact.company_id == Company.id)
                .where(Company.sourcing_batch_id == batch_id)
            )
        ).scalar_one()
        or 0
    )


async def _batch_current_stage(session, batch_id: UUID, batch: SourcingBatch) -> tuple[str | None, str | None]:
    meta = batch.meta if isinstance(batch.meta, dict) else {}
    if batch.status == "awaiting_confirmation":
        return "tal_review", "Waiting for approval before running enrichment"
    if batch.status == "cancelled":
        return "cancelled", "Import saved without enrichment"
    if batch.status == "pending":
        return "queued", str(meta.get("progress_message") or "Queued for research")
    if batch.status == "processing":
        total = int(batch.total_rows or 0)
        processed = int(batch.processed_rows or 0)
        fallback = (
            f"Processed {processed} of {total} accounts" if total > 0 else "Research in progress"
        )
        return str(meta.get("current_stage") or "research_running"), str(meta.get("progress_message") or fallback)
    if batch.status == "completed":
        return "completed", "Research complete"
    if batch.status == "failed":
        return "failed", str(meta.get("progress_message") or "Research failed")
    return str(meta.get("current_stage") or "unknown"), str(meta.get("progress_message") or "")


async def _build_batch_read(session, batch: SourcingBatch) -> SourcingBatchRead:
    meta = batch.meta if isinstance(batch.meta, dict) else {}
    current_stage, progress_message = await _batch_current_stage(session, batch.id, batch)
    read = SourcingBatchRead.model_validate(batch)
    read.current_stage = current_stage
    read.progress_message = progress_message or meta.get("progress_message")
    read.eta_seconds = _estimate_batch_eta_seconds(batch)
    read.contacts_found = await _batch_contacts_found(session, batch.id)
    read.verdict_summary = meta.get("verdict_summary")
    read.requires_confirmation = bool(meta.get("requires_confirmation"))
    read.auto_started = bool(meta.get("auto_started"))
    return read


async def _queue_batch_enrichment(session, batch: SourcingBatch) -> None:
    batch.status = "processing"
    meta = dict(batch.meta or {})
    meta["auto_started"] = True
    meta["requires_confirmation"] = False
    meta["current_stage"] = "queued"
    meta["progress_message"] = "Queued for enrichment"
    batch.meta = meta
    batch.updated_at = datetime.utcnow()
    session.add(batch)
    await session.commit()
    try:
        from app.tasks.enrichment import icp_research_batch_task

        icp_research_batch_task.delay(str(batch.id))
    except Exception as exc:
        batch.status = "failed"
        batch.error_log = [*(batch.error_log or []), {"batch": str(batch.id), "error": str(exc)}]
        batch.updated_at = datetime.utcnow()
        session.add(batch)
        await session.commit()
        raise HTTPException(status_code=500, detail="Failed to queue batch enrichment") from exc


async def _queue_batch_import(
    session,
    batch: SourcingBatch,
    rows: list[dict[str, str]],
    admin_payload: dict[str, str],
) -> None:
    batch.status = "processing"
    meta = dict(batch.meta or {})
    meta["current_stage"] = "queued"
    meta["progress_message"] = "Queued for import"
    batch.meta = meta
    batch.updated_at = datetime.utcnow()
    session.add(batch)
    await session.commit()
    try:
        from app.tasks.enrichment import process_sourcing_upload_task

        process_sourcing_upload_task.delay(str(batch.id), rows, admin_payload)
    except Exception as exc:
        batch.status = "failed"
        batch.error_log = [*(batch.error_log or []), {"batch": str(batch.id), "error": str(exc)}]
        batch.updated_at = datetime.utcnow()
        session.add(batch)
        await session.commit()
        raise HTTPException(status_code=500, detail="Failed to queue batch import") from exc


async def _process_uploaded_rows(
    session,
    batch: SourcingBatch,
    rows: list[dict[str, str]],
    admin_payload: dict[str, str],
) -> None:
    batch_id = batch.id
    repo = CompanyRepository(session)
    created, attached_existing, skipped, failed = 0, 0, 0, 0
    errors: list[dict[str, str]] = []

    all_users = (await session.execute(select(User).where(User.is_active == True))).scalars().all()  # noqa: E712
    _user_by_email: dict[str, User] = {u.email.strip().lower(): u for u in all_users if u.email}
    # Full-name index: map each normalized full name to the matching users. A name
    # is only trusted when it resolves to EXACTLY ONE active user — ambiguous or
    # unknown names fall through to "unassigned" rather than risk a mis-match.
    _users_by_full_name: dict[str, list[User]] = {}
    for user in all_users:
        full = (user.name or "").strip().lower()
        if full:
            _users_by_full_name.setdefault(full, []).append(user)

    def _resolve_user(rep_email: str | None, rep_name: str | None) -> dict[str, str] | None:
        """Resolve an uploaded AE/SDR cell to an active user.

        Matching is strict to prevent the wrong rep being assigned from a CSV:
          - exact, normalized email match wins;
          - otherwise an exact, normalized FULL-name match, but only when it is
            unambiguous (exactly one active user with that name);
          - NO first-name / fuzzy matching. A first-name fallback previously let
            a name fragment collide with an unrelated rep (e.g. the sheet said
            one SDR but the account was assigned to a different same-first-name
            user). When nothing resolves, leave the slot unassigned and log it.
        """
        found: User | None = None
        if rep_email:
            found = _user_by_email.get(rep_email.strip().lower())
        if not found and rep_name:
            matches = _users_by_full_name.get(rep_name.strip().lower()) or []
            if len(matches) == 1:
                found = matches[0]
        if not found:
            if rep_email or rep_name:
                logger.warning(
                    "Sourcing import could not resolve owner cell to an active "
                    "user; leaving unassigned (email=%r name=%r batch=%s)",
                    rep_email,
                    rep_name,
                    batch_id,
                )
            return None
        return {
            "id": str(found.id),
            "email": found.email,
            "name": found.name,
        }

    async def _update_batch_progress(current_stage: str, progress_message: str) -> None:
        progress_batch = await session.get(SourcingBatch, batch_id)
        if not progress_batch:
            return
        progress_batch.processed_rows = created + attached_existing + skipped + failed
        progress_batch.created_companies = created + attached_existing
        progress_batch.skipped_rows = skipped
        progress_batch.failed_rows = failed
        progress_batch.error_log = errors if errors else None
        meta = dict(progress_batch.meta or {})
        meta["current_stage"] = current_stage
        meta["progress_message"] = progress_message
        progress_batch.meta = meta
        progress_batch.updated_at = datetime.utcnow()
        session.add(progress_batch)
        await session.commit()
        await session.refresh(progress_batch)

    await _update_batch_progress("import_running", f"Importing 0 of {len(rows)} rows")

    for idx, row in enumerate(rows, start=1):
        fields = row_to_company_fields(row)
        domain = fields["domain"]
        name = fields["name"]

        ae_user = _resolve_user(fields.get("assigned_rep_email"), fields.get("assigned_rep_name") or fields.get("assigned_rep"))
        sdr_user = _resolve_user(fields.get("sdr_email"), fields.get("sdr_name"))
        if ae_user:
            fields["assigned_to_id"] = ae_user["id"]
            fields["assigned_rep_email"] = ae_user["email"]
            fields["assigned_rep_name"] = ae_user["name"]
            fields["assigned_rep"] = ae_user["name"]
        if sdr_user:
            fields["sdr_id"] = sdr_user["id"]
            fields["sdr_email"] = sdr_user["email"]
            fields["sdr_name"] = sdr_user["name"]

        try:
            company = None
            if not domain.endswith(".unknown"):
                company = await repo.get_by_domain(domain)
            if not company:
                company = await repo.get_by_name(name)
            if not company:
                company = await repo.get_by_normalized_name(name)

            if company:
                already_in_batch = company.sourcing_batch_id == batch_id
                company = merge_company_from_upload(company, fields)
                company.sourcing_batch_id = batch_id
                append_company_activity_log(
                    company,
                    action="company_import_updated",
                    actor_name=admin_payload["name"],
                    actor_email=admin_payload["email"],
                    message=f"Updated from upload {batch.filename}",
                    metadata={"source": "upload", "batch_id": str(batch_id)},
                )
                company.updated_at = datetime.utcnow()
                company = refresh_company_prospecting_fields(company)
                company.icp_score, company.icp_tier = score_company(company)
                session.add(company)
                await session.commit()
                await session.refresh(company)
                from app.services.company_auto_mapping import backfill_orphans_for_company
                await backfill_orphans_for_company(session, company)
                await session.commit()
                if already_in_batch:
                    skipped += 1
                else:
                    attached_existing += 1
            else:
                company = Company(
                    **fields,
                    sourcing_batch_id=batch_id,
                    created_by_id=UUID(admin_payload["id"]) if admin_payload.get("id") else None,
                    created_by_name=admin_payload.get("name"),
                )
                append_company_activity_log(
                    company,
                    action="company_created",
                    actor_name=admin_payload["name"],
                    actor_email=admin_payload["email"],
                    message=f"Created from upload {batch.filename}",
                    metadata={"source": "upload", "batch_id": str(batch_id)},
                )
                company = refresh_company_prospecting_fields(company)
                company.icp_score, company.icp_tier = score_company(company)
                session.add(company)
                await session.commit()
                await session.refresh(company)
                from app.services.company_auto_mapping import backfill_orphans_for_company
                await backfill_orphans_for_company(session, company)
                await session.commit()
                created += 1

            profile = company.prospecting_profile if isinstance(company.prospecting_profile, dict) else {}
            inv = profile.get("investors") if isinstance(profile.get("investors"), dict) else {}
            ownership = profile.get("ownership_stage")
            if ownership and not company.ownership_stage:
                company.ownership_stage = str(ownership)[:500]
            pe = inv.get("pe") if isinstance(inv.get("pe"), list) else []
            if pe and not company.pe_investors:
                company.pe_investors = "; ".join(str(i).strip() for i in pe if str(i).strip())
            vc = inv.get("vc_growth") if isinstance(inv.get("vc_growth"), list) else []
            if vc and not company.vc_investors:
                company.vc_investors = "; ".join(str(i).strip() for i in vc if str(i).strip())
            strat = inv.get("strategic") if isinstance(inv.get("strategic"), list) else []
            if strat and not company.strategic_investors:
                company.strategic_investors = "; ".join(str(i).strip() for i in strat if str(i).strip())
            if any([pe, vc, strat, ownership]):
                session.add(company)
                await session.commit()

            contact_fields = row_to_contact_fields(row, fields)
            if contact_fields:
                if ae_user:
                    contact_fields["assigned_to_id"] = ae_user["id"]
                    contact_fields["assigned_rep_email"] = ae_user["email"]
                if sdr_user:
                    contact_fields["sdr_id"] = sdr_user["id"]
                    contact_fields["sdr_name"] = sdr_user["name"]
                existing_contact = None
                if contact_fields.get("email"):
                    existing_contact = (
                        await session.execute(
                            select(Contact).where(Contact.email == contact_fields["email"]).limit(1)
                        )
                    ).scalars().first()
                if not existing_contact:
                    existing_contact = (
                        await session.execute(
                            select(Contact).where(
                                Contact.company_id == company.id,
                                Contact.first_name == contact_fields.get("first_name"),
                                Contact.last_name == contact_fields.get("last_name"),
                            ).limit(1)
                        )
                    ).scalars().first()

                if existing_contact:
                    for key, value in contact_fields.items():
                        if value and not getattr(existing_contact, key, None):
                            setattr(existing_contact, key, value)
                    refresh_contact_sequence_plan(existing_contact, company)
                    session.add(existing_contact)
                    resolved_contact = existing_contact
                else:
                    contact = Contact(**contact_fields, company_id=company.id)
                    refresh_contact_sequence_plan(contact, company)
                    session.add(contact)
                    resolved_contact = contact
                await session.commit()
                await session.refresh(resolved_contact)

                try:
                    await _auto_create_angel_records(session, company, resolved_contact)
                    await session.commit()
                except Exception:
                    await session.rollback()

                company_contacts = (
                    await session.execute(select(Contact).where(Contact.company_id == company.id))
                ).scalars().all()
                refresh_company_prospecting_fields(company, company_contacts)
                company.icp_score, company.icp_tier = score_company(company)
                session.add(company)
                await session.commit()

        except Exception as e:
            await session.rollback()
            failed += 1
            errors.append({"name": name, "error": str(e)})

        await _update_batch_progress("import_running", f"Imported {idx} of {len(rows)} rows")

    final_batch = await session.get(SourcingBatch, batch_id)
    if not final_batch:
        return
    final_batch.status = "awaiting_confirmation" if bool((final_batch.meta or {}).get("requires_confirmation")) else "pending"
    session.add(final_batch)
    await session.commit()
    await _update_batch_progress("import_completed", "Import complete, preparing enrichment")


async def _build_competitive_landscape(session, company: Company) -> list[dict[str, str]]:
    cache = company.enrichment_cache if isinstance(company.enrichment_cache, dict) else {}

    cached_cards = cache.get("competitive_landscape_v2")
    if isinstance(cached_cards, list) and cached_cards:
        normalized_cards: list[dict[str, str]] = []
        for item in cached_cards:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            normalized_cards.append(
                {
                    "name": name,
                    "website": str(item.get("website") or "").strip(),
                    "summary": str(item.get("summary") or "").strip()[:320],
                    "pitch_angle": str(item.get("pitch_angle") or "").strip()[:320],
                    "source": str(item.get("source") or "icp_analysis"),
                }
            )
            if len(normalized_cards) >= 4:
                break
        if normalized_cards:
            # Older cached cards used a single generic pitch across all competitors.
            # If we detect that shape, rebuild from fresher AI/DB signals instead.
            pitch_values = [str(card.get("pitch_angle") or "").strip().lower() for card in normalized_cards]
            pitch_values = [value for value in pitch_values if value]
            pitch_bodies: set[str] = set()
            for card in normalized_cards:
                name = str(card.get("name") or "").strip().lower()
                pitch = str(card.get("pitch_angle") or "").strip().lower()
                if not pitch:
                    continue
                body = pitch
                if name:
                    body = re.sub(rf"^against\s+{re.escape(name)}\s*[:,\-]?\s*", "", body)
                pitch_bodies.add(body)

            has_repeated_generic_pitch = (
                len(normalized_cards) > 1
                and (len(set(pitch_values)) <= 1 or len(pitch_bodies) <= 1)
            )
            if not has_repeated_generic_pitch:
                return normalized_cards

    ai_entry = cache.get("ai_summary") if isinstance(cache.get("ai_summary"), dict) else {}
    ai_data = ai_entry.get("data") if isinstance(ai_entry.get("data"), dict) else {}
    seed_names = []
    for item in ai_data.get("competitive_landscape") if isinstance(ai_data.get("competitive_landscape"), list) else []:
        label = str(item or "").strip()
        if label:
            seed_names.append(label)

    seed_pitch_tracks = [
        "Highlight faster implementation cycles with fewer delivery handoffs.",
        "Emphasize lower services overhead and clearer rollout ownership.",
        "Position Beacon as the safer path for complex cross-team deployments.",
        "Lead with deployment-risk reduction and measurable time-to-value gains.",
    ]

    if seed_names:
        category = str(company.vertical or company.industry or "").strip()
        seeded_cards: list[dict[str, str]] = []
        seen_seed: set[str] = set()
        for idx, label in enumerate(seed_names):
            key = label.lower()
            if key in seen_seed:
                continue
            seen_seed.add(key)
            summary = f"{label} is a comparable option buyers evaluate alongside {company.name}."
            if category:
                summary = f"{summary} Category context: {category}."
            seeded_cards.append(
                {
                    "name": label,
                    "website": "",
                    "summary": summary[:320],
                    "pitch_angle": f"Against {label}: {seed_pitch_tracks[idx % len(seed_pitch_tracks)]}",
                    "source": "research",
                }
            )
            if len(seeded_cards) >= 4:
                break
        if seeded_cards:
            return seeded_cards

    # Try specific filters first, then broaden
    base = select(Company).where(Company.id != company.id)
    candidates = []
    if company.industry:
        candidates = (
            await session.execute(base.where(Company.industry == company.industry).order_by(Company.enriched_at.desc().nullslast(), Company.updated_at.desc()).limit(8))
        ).scalars().all()
    if not candidates and company.vertical:
        candidates = (
            await session.execute(base.where(Company.vertical == company.vertical).order_by(Company.enriched_at.desc().nullslast(), Company.updated_at.desc()).limit(8))
        ).scalars().all()
    # Last resort: companies with enrichment data (descriptions)
    if not candidates:
        candidates = (
            await session.execute(
                base.where(Company.description.isnot(None), Company.description != "")
                .order_by(Company.enriched_at.desc().nullslast(), Company.updated_at.desc())
                .limit(8)
            )
        ).scalars().all()
    # Final: any companies with real domains
    if not candidates:
        candidates = (
            await session.execute(
                base.where(~Company.domain.endswith(".unknown"))
                .order_by(Company.enriched_at.desc().nullslast(), Company.updated_at.desc())
                .limit(8)
            )
        ).scalars().all()

    results: list[dict[str, str]] = []
    seen: set[str] = set()

    def _pitch_angle_from_text(text: str) -> str:
        normalized = text.lower()
        if "implementation" in normalized or "professional services" in normalized:
            return "Competitors are investing in implementation motion -> pitch zero-friction implementation acceleration."
        if "automation" in normalized or "orchestration" in normalized:
            return "Automation is clearly strategic here -> pitch Beacon as the faster path without adding headcount."
        if "integration" in normalized:
            return "Integration complexity is visible -> pitch faster rollout and less coordination drag."
        return "Use Beacon to shorten time-to-value and reduce manual implementation work."

    for candidate in candidates:
        key = candidate.name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        context = " ".join(
            value
            for value in [
                candidate.description or "",
                candidate.account_thesis or "",
                candidate.why_now or "",
                candidate.beacon_angle or "",
            ]
            if value
        ).strip()
        results.append(
            {
                "name": candidate.name,
                "website": "" if candidate.domain.endswith(".unknown") else f"https://{candidate.domain}",
                "summary": (candidate.description or candidate.account_thesis or candidate.why_now or "Comparable operating motion in the same market.")[:220],
                "pitch_angle": _pitch_angle_from_text(context or candidate.name),
                "source": "db",
            }
        )
        if len(results) >= 4:
            return results

    for idx, label in enumerate(seed_names):
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "name": label,
                "website": "",
                "summary": "Mentioned in Beacon's lightweight competitive scan.",
                "pitch_angle": f"Against {label}: {seed_pitch_tracks[idx % len(seed_pitch_tracks)]}",
                "source": "research",
            }
        )
        if len(results) >= 4:
            break

    return results[:4]


@router.post("/upload", response_model=SourcingBatchRead, status_code=202)
async def upload_csv(
    admin: AdminUser,
    file: UploadFile = File(...),
    session: DBSession = None,
):
    """
    Upload a CSV of target companies. Creates a SourcingBatch, parses rows
    into Company records (deduped), and queues background enrichment.
    """
    lower_name = (file.filename or "").lower()
    if not (lower_name.endswith(".csv") or lower_name.endswith(".xlsx")):
        raise HTTPException(status_code=400, detail="File must be a .csv or .xlsx")

    # The request path only normalizes and persists uploaded data. Expensive
    # research is queued afterward so the browser is not held open for minutes.
    content = await file.read()
    rows = parse_tabular_file(file.filename or "upload.csv", content)
    if not rows:
        raise HTTPException(
            status_code=400,
            detail="No valid rows found. The file needs at least a company name or domain column.",
        )
    verdict_summary = _build_upload_verdict_summary(rows)

    # Create batch record
    batch = SourcingBatch(
        filename=file.filename or "upload.csv",
        total_rows=len(rows),
        status="awaiting_confirmation" if verdict_summary["requires_confirmation"] else "pending",
        created_by_id=admin.id,
        created_by_name=admin.name,
        created_by_email=admin.email,
        meta={
            "upload_mode": "file",
            "verdict_summary": verdict_summary,
            "requires_confirmation": verdict_summary["requires_confirmation"],
            "auto_started": False,
            "progress_message": "Upload received and parsed",
            "current_stage": "upload_received",
        },
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(batch)
    await session.commit()
    await session.refresh(batch)

    await _queue_batch_import(
        session,
        batch,
        rows,
        {
            "id": str(admin.id),
            "name": admin.name,
            "email": admin.email,
        },
    )
    await session.refresh(batch)
    return await _build_batch_read(session, batch)


# ── Batch Status ───────────────────────────────────────────────────────────────

@router.get("/batches/{batch_id}", response_model=SourcingBatchRead)
async def get_batch_status(batch_id: UUID, _user: CurrentUser, session: DBSession = None):
    """Poll batch enrichment progress."""
    batch = await session.get(SourcingBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return await _build_batch_read(session, batch)


@router.post("/batches/{batch_id}/confirm", response_model=SourcingBatchRead)
async def confirm_batch_enrichment(
    batch_id: UUID,
    payload: BatchConfirmPayload,
    _admin: AdminUser,
    session: DBSession = None,
):
    batch = await session.get(SourcingBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch.status == "cancelled":
        raise HTTPException(status_code=400, detail="This batch was cancelled")
    if batch.status == "completed":
        return await _build_batch_read(session, batch)
    if batch.status == "awaiting_confirmation" and payload.force:
        await _queue_batch_enrichment(session, batch)
        await session.refresh(batch)
    return await _build_batch_read(session, batch)


@router.post("/batches/{batch_id}/cancel", response_model=SourcingBatchRead)
async def cancel_batch_enrichment(batch_id: UUID, _admin: AdminUser, session: DBSession = None):
    batch = await session.get(SourcingBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    batch.status = "cancelled"
    meta = dict(batch.meta or {})
    meta["progress_message"] = "Import kept without enrichment"
    meta["current_stage"] = "cancelled"
    batch.meta = meta
    batch.updated_at = datetime.utcnow()
    session.add(batch)
    await session.commit()
    await session.refresh(batch)
    return await _build_batch_read(session, batch)


@router.get("/batches/{batch_id}/companies", response_model=list[CompanyRead])
async def get_batch_companies(batch_id: UUID, _user: CurrentUser, session: DBSession = None, page: Pagination = None):
    """List companies belonging to a specific sourcing batch."""
    batch = await session.get(SourcingBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    result = await session.execute(
        select(Company)
        .where(Company.sourcing_batch_id == batch_id)
        .where(company_visibility_filter(_user.id, _user.role == "admin"))
        .offset(page.skip)
        .limit(page.limit)
        .order_by(Company.created_at.desc())
    )
    return result.scalars().all()


# ── All Sourced Companies ──────────────────────────────────────────────────────

@router.get("/companies", response_model=PaginatedResponse[CompanyRead])
async def list_sourced_companies(
    _user: CurrentUser,
    session: DBSession = None,
    page: Pagination = None,
    q: str | None = Query(default=None),
    icp_tier: str | None = Query(default=None),
    disposition: str | None = Query(default=None),
    account_status: str | None = Query(default=None, description="One or more account_status values (comma-separated). Use 'unset' for accounts with no status."),
    recommended_outreach_lane: str | None = Query(default=None),
    assigned_rep_email: str | None = Query(default=None),
    owner_id: str | None = Query(default=None, description="One or more user UUIDs (comma-separated). Matches AE or SDR ownership."),
    ae_id: str | None = Query(default=None, description="One or more user UUIDs (comma-separated). Matches assigned_to_id (AE) only."),
    sdr_id: str | None = Query(default=None, description="One or more user UUIDs (comma-separated). Matches sdr_id only."),
    journey_stage: str | None = Query(default=None, description="Recotap journey stage(s), comma-separated. Use 'not_scored' for accounts with no Recotap journey stage."),
    prospects_min: int | None = Query(default=None, ge=0, description="Inclusive lower bound on the count of contacts (prospects) per account."),
    prospects_max: int | None = Query(default=None, ge=0, description="Inclusive upper bound on the count of contacts (prospects) per account."),
):
    """List sourced companies plus lightweight ClickUp-imported accounts."""
    stmt = (
        select(Company)
        .where(_account_sourcing_visibility_filter())
        .where(company_visibility_filter(_user.id, _user.role == "admin"))
    )
    search_term = (q or "").strip()
    if search_term:
        like = f"%{search_term}%"
        stmt = stmt.where(
            or_(
                Company.name.ilike(like),
                Company.domain.ilike(like),
                Company.industry.ilike(like),
                Company.assigned_rep.ilike(like),
                Company.assigned_rep_email.ilike(like),
                Company.disposition.ilike(like),
                Company.recommended_outreach_lane.ilike(like),
            )
        )
    stmt = _apply_text_multi_filter(stmt, Company.icp_tier, icp_tier)
    stmt = _apply_text_multi_filter(stmt, Company.disposition, disposition)
    if account_status:
        status_tokens = [t.strip().lower() for t in str(account_status).split(",") if t.strip()]
        status_clauses = []
        real_statuses = [t for t in status_tokens if t != "unset"]
        if real_statuses:
            status_clauses.append(Company.account_status.in_(real_statuses))
        if "unset" in status_tokens:
            status_clauses.append(or_(Company.account_status.is_(None), Company.account_status == ""))
        if status_clauses:
            stmt = stmt.where(or_(*status_clauses))
    stmt = _apply_text_multi_filter(stmt, Company.recommended_outreach_lane, recommended_outreach_lane)
    if assigned_rep_email:
        stmt = stmt.where(Company.assigned_rep_email == assigned_rep_email)
    if owner_id:
        owner_uuids: list[UUID] = []
        owner_unassigned = False
        for raw in str(owner_id).split(","):
            raw = raw.strip()
            if not raw:
                continue
            # Mirror the contacts repository: a "__unassigned__" sentinel means
            # "no owner" (both ownership slots null), OR-ed in alongside any real
            # owner ids so reps can surface accounts that slipped through.
            if raw == "__unassigned__":
                owner_unassigned = True
                continue
            try:
                owner_uuids.append(UUID(raw))
            except ValueError:
                continue
        owner_clauses = []
        if owner_uuids:
            owner_clauses.append(or_(Company.assigned_to_id.in_(owner_uuids), Company.sdr_id.in_(owner_uuids)))
        if owner_unassigned:
            owner_clauses.append(and_(Company.assigned_to_id.is_(None), Company.sdr_id.is_(None)))
        if owner_clauses:
            stmt = stmt.where(or_(*owner_clauses) if len(owner_clauses) > 1 else owner_clauses[0])

    if ae_id:
        ae_uuids: list[UUID] = []
        for raw in str(ae_id).split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ae_uuids.append(UUID(raw))
            except ValueError:
                continue
        if ae_uuids:
            stmt = stmt.where(Company.assigned_to_id.in_(ae_uuids))

    if sdr_id:
        sdr_uuids: list[UUID] = []
        for raw in str(sdr_id).split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                sdr_uuids.append(UUID(raw))
            except ValueError:
                continue
        if sdr_uuids:
            stmt = stmt.where(Company.sdr_id.in_(sdr_uuids))

    # Recotap journey-stage filter — joins via recotap_accounts.company_id (set
    # on pull/seed), so no domain-normalization in SQL. "not_scored" matches
    # accounts with no Recotap row or an empty/null stage.
    if journey_stage:
        stages = [s.strip() for s in str(journey_stage).split(",") if s.strip()]
        want_not_scored = "not_scored" in stages
        real_stages = [s for s in stages if s != "not_scored"]
        journey_clauses = []
        if real_stages:
            journey_clauses.append(
                Company.id.in_(
                    select(RecotapAccount.company_id).where(
                        RecotapAccount.company_id.is_not(None),
                        RecotapAccount.journey_stage.in_(real_stages),
                    )
                )
            )
        if want_not_scored:
            journey_clauses.append(
                Company.id.notin_(
                    select(RecotapAccount.company_id).where(
                        RecotapAccount.company_id.is_not(None),
                        RecotapAccount.journey_stage.is_not(None),
                        RecotapAccount.journey_stage != "",
                    )
                )
            )
        if journey_clauses:
            stmt = stmt.where(or_(*journey_clauses) if len(journey_clauses) > 1 else journey_clauses[0])

    # Advanced filter: prospects (contacts) per account. We materialise a
    # per-company count via a correlated subquery and apply >= / <= bounds.
    # The UI flattens its operator+value into min/max, so the backend just
    # ANDs whichever bounds are present.
    if prospects_min is not None or prospects_max is not None:
        prospect_count_sub = (
            select(Contact.company_id, func.count(Contact.id).label("cnt"))
            .group_by(Contact.company_id)
            .subquery()
        )
        prospect_count = func.coalesce(prospect_count_sub.c.cnt, 0)
        stmt = stmt.outerjoin(prospect_count_sub, Company.id == prospect_count_sub.c.company_id)
        if prospects_min is not None:
            stmt = stmt.where(prospect_count >= prospects_min)
        if prospects_max is not None:
            stmt = stmt.where(prospect_count <= prospects_max)

    total = (
        await session.execute(
            select(func.count()).select_from(stmt.order_by(None).subquery())
        )
    ).scalar_one()
    items = (
        await session.execute(
            stmt.order_by(Company.created_at.desc(), Company.id.desc())
            .offset(page.skip)
            .limit(page.limit)
        )
    ).scalars().all()
    reads = [CompanyRead.model_validate(company) for company in items]
    sig = await recotap_signals(session, [company.domain for company in items])
    for read, company in zip(reads, items):
        read.recotap = sig.get(recotap_domain(company.domain))
    return PaginatedResponse.build(items=reads, total=total, skip=page.skip, limit=page.limit)


@router.get("/summary", response_model=CompanySourcingSummary)
async def get_sourced_company_summary(
    _user: CurrentUser,
    session: DBSession = None,
    assigned_rep_email: str | None = Query(default=None),
    owner_id: str | None = Query(default=None, description="One or more user UUIDs (comma-separated). Matches AE or SDR ownership."),
):
    stmt = (
        select(Company)
        .where(_account_sourcing_visibility_filter())
        .where(company_visibility_filter(_user.id, _user.role == "admin"))
    )
    if assigned_rep_email:
        stmt = stmt.where(Company.assigned_rep_email == assigned_rep_email)
    if owner_id:
        owner_uuids: list[UUID] = []
        owner_unassigned = False
        for raw in str(owner_id).split(","):
            raw = raw.strip()
            if not raw:
                continue
            # Mirror the contacts repository: a "__unassigned__" sentinel means
            # "no owner" (both ownership slots null), OR-ed in alongside any real
            # owner ids so reps can surface accounts that slipped through.
            if raw == "__unassigned__":
                owner_unassigned = True
                continue
            try:
                owner_uuids.append(UUID(raw))
            except ValueError:
                continue
        owner_clauses = []
        if owner_uuids:
            owner_clauses.append(or_(Company.assigned_to_id.in_(owner_uuids), Company.sdr_id.in_(owner_uuids)))
        if owner_unassigned:
            owner_clauses.append(and_(Company.assigned_to_id.is_(None), Company.sdr_id.is_(None)))
        if owner_clauses:
            stmt = stmt.where(or_(*owner_clauses) if len(owner_clauses) > 1 else owner_clauses[0])

    # The summary only inspects a handful of columns per company (the counters
    # below + the JSONB keys account_priority_snapshot / _icp_analysis read).
    # Load just those and skip the heavy unused blobs (enrichment_sources,
    # tech_stack) and the long Text columns, so computing ~12 counters no longer
    # drags the whole companies table — including columns this handler never
    # touches — into memory. Behavior and response shape are unchanged.
    stmt = stmt.options(
        load_only(
            Company.icp_tier,
            Company.disposition,
            Company.domain,
            Company.enriched_at,
            Company.outreach_plan,
            Company.enrichment_cache,
            Company.intent_signals,
            Company.prospecting_profile,
            Company.recommended_outreach_lane,
            Company.outreach_status,
            Company.icp_score,
        )
    )
    companies = (await session.execute(stmt)).scalars().all()

    hot_count = 0
    warm_count = 0
    high_priority_count = 0
    engaged_count = 0
    unresolved_count = 0
    unenriched_count = 0
    researched_count = 0
    target_verdict_count = 0
    watch_verdict_count = 0
    enriched_count = 0
    total_contacts = 0

    for company in companies:
        if company.icp_tier == "hot":
            hot_count += 1
        if company.icp_tier == "warm":
            warm_count += 1
        if account_priority_snapshot(company).get("priority_band") == "high":
            high_priority_count += 1
        if (company.disposition or "").lower() in {"interested", "working"}:
            engaged_count += 1
        if company.domain.endswith(".unknown"):
            unresolved_count += 1
        if company.enriched_at:
            enriched_count += 1
        else:
            unenriched_count += 1

        icp_analysis = _icp_analysis(company)
        if icp_analysis:
            researched_count += 1
        classification = str(icp_analysis.get("classification") or "").lower()
        if classification == "target":
            target_verdict_count += 1
        if classification == "watch":
            watch_verdict_count += 1

        outreach_plan = company.outreach_plan if isinstance(company.outreach_plan, dict) else {}
        total_contacts += int(outreach_plan.get("contact_count") or 0)

    return CompanySourcingSummary(
        total_companies=len(companies),
        hot_count=hot_count,
        warm_count=warm_count,
        high_priority_count=high_priority_count,
        engaged_count=engaged_count,
        unresolved_count=unresolved_count,
        unenriched_count=unenriched_count,
        researched_count=researched_count,
        target_verdict_count=target_verdict_count,
        watch_verdict_count=watch_verdict_count,
        enriched_count=enriched_count,
        total_contacts=total_contacts,
    )


@router.post("/companies/manual", response_model=SourcingBatchRead, status_code=202)
async def create_manual_company(
    payload: ManualCompanyCreate,
    current_user: AdminUser,
    session: DBSession = None,
):
    name = _clean_company_name(payload.name or "")
    if not name:
        raise HTTPException(status_code=400, detail="Company name is required")

    filename = f"Manual entry - {name}"
    domain = (payload.domain or "").strip()
    normalized_domain = domain.lower().replace("https://", "").replace("http://", "").removeprefix("www.").split("/")[0] if domain else ""
    fake_row = {"company name": name}
    if normalized_domain:
        fake_row["domain"] = normalized_domain

    batch = SourcingBatch(
        filename=filename,
        total_rows=1,
        status="pending",
        created_by_id=current_user.id,
        created_by_name=current_user.name,
        created_by_email=current_user.email,
        meta={
            "upload_mode": "manual_entry",
            "verdict_summary": {
                "target": 0,
                "watch": 0,
                "non_target": 0,
                "unknown": 1,
                "has_uploaded_verdicts": False,
                "pass_auto": True,
                "requires_confirmation": False,
                "message": "Manual account added and queued for enrichment.",
            },
            "requires_confirmation": False,
            "auto_started": False,
            "current_stage": "manual_created",
            "progress_message": "Manual account created",
        },
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(batch)
    await session.commit()
    await session.refresh(batch)

    fields = row_to_company_fields(fake_row)
    repo = CompanyRepository(session)
    existing = None
    if normalized_domain:
        existing = await repo.get_by_domain(fields["domain"])
    if not existing:
        existing = await repo.get_by_name(fields["name"])
    if not existing:
        # Looser dedupe: catches the "added 'zywave', then added 'zywave.com'"
        # case where the first row has a placeholder *.unknown domain and the
        # second row's name (or domain) wouldn't otherwise match.
        existing = await repo.get_by_normalized_name(fields["name"])
    # Also try the raw domain root as a name match (handles "added zywave.com
    # as a name" by stripping ".com" and looking for "zywave").
    if not existing and normalized_domain:
        existing = await repo.get_by_normalized_name(normalized_domain)

    if existing:
        company = merge_company_from_upload(existing, fields)
        company.sourcing_batch_id = batch.id
        append_company_activity_log(
            company,
            action="manual_company_requeued",
            actor_name=current_user.name,
            actor_email=current_user.email,
            message=f"Added back into sourcing by {current_user.name}",
            metadata={"batch_id": str(batch.id)},
        )
    elif normalized_domain:
        # Real domain supplied: funnel the insert through the domain-keyed
        # get-or-create so a duplicate domain (e.g. two admins adding the same
        # account at once) collapses onto the existing row under the
        # lower(domain) unique index instead of raising. The lookups above just
        # missed, so created=True is the expected path.
        from app.repositories.company import get_or_create_company_by_domain

        create_fields = {k: v for k, v in fields.items() if k != "domain"}
        company, created = await get_or_create_company_by_domain(
            session,
            fields["domain"],
            defaults={
                **create_fields,
                "sourcing_batch_id": batch.id,
                "created_by_id": current_user.id,
                "created_by_name": current_user.name,
            },
        )
        if created:
            append_company_activity_log(
                company,
                action="manual_company_created",
                actor_name=current_user.name,
                actor_email=current_user.email,
                message=f"Manually created by {current_user.name}",
                metadata={"batch_id": str(batch.id)},
            )
        else:
            company = merge_company_from_upload(company, fields)
            company.sourcing_batch_id = batch.id
            append_company_activity_log(
                company,
                action="manual_company_requeued",
                actor_name=current_user.name,
                actor_email=current_user.email,
                message=f"Added back into sourcing by {current_user.name}",
                metadata={"batch_id": str(batch.id)},
            )
    else:
        company = Company(**fields, sourcing_batch_id=batch.id, created_by_id=current_user.id, created_by_name=current_user.name)
        append_company_activity_log(
            company,
            action="manual_company_created",
            actor_name=current_user.name,
            actor_email=current_user.email,
            message=f"Manually created by {current_user.name}",
            metadata={"batch_id": str(batch.id)},
        )
    company = refresh_company_prospecting_fields(company)
    company.icp_score, company.icp_tier = score_company(company)
    session.add(company)
    await session.commit()
    await session.refresh(company)

    # Bell alert so admins + the assigned owner know an account was added.
    try:
        from app.services.notifications import notify_records_added

        actor = current_user.name or current_user.email
        await notify_records_added(
            session,
            kind="accounts",
            count=1,
            actor_name=actor,
            owner_user_id=company.assigned_to_id or company.sdr_id,
            detail=f"{actor} added account {company.name}.",
        )
    except Exception:
        pass  # informational only

    from app.services.company_auto_mapping import backfill_orphans_for_company
    await backfill_orphans_for_company(session, company)
    await session.commit()

    batch.created_companies = 1
    meta = dict(batch.meta or {})
    meta["company_id"] = str(company.id)
    meta["company_name"] = company.name
    batch.meta = meta
    batch.updated_at = datetime.utcnow()
    session.add(batch)
    await session.commit()
    await session.refresh(batch)
    await _queue_batch_enrichment(session, batch)
    await session.refresh(batch)
    return await _build_batch_read(session, batch)


# ── Single Company Detail ─────────────────────────────────────────────────────

@router.get("/companies/{company_id}", response_model=CompanyRead)
async def get_sourced_company(company_id: UUID, _user: CurrentUser, session: DBSession = None):
    """Get a single sourced company with full enrichment data (including cache)."""
    company = await session.get(Company, company_id)
    if not company or not _can_see_company(company, _user):
        # 404 (not 403) so a non-admin can't probe which company ids exist.
        raise HTTPException(status_code=404, detail="Company not found")
    read = CompanyRead.model_validate(company)
    cache = dict(read.enrichment_cache or {})
    cache["competitive_landscape_v2"] = await _build_competitive_landscape(session, company)
    read.enrichment_cache = cache
    sig = await recotap_signals(session, [company.domain])
    read.recotap = sig.get(recotap_domain(company.domain))
    return read


@router.post("/recotap/refresh")
async def refresh_recotap_signals(
    _user: CurrentUser,
    session: DBSession = None,
    seed: bool | None = None,
    overwrite: bool = False,
    full: bool = False,
):
    """Pull live Recotap account signals into recotap_accounts.

    Mock seeding (deterministic journey-stage/score signals for every sourced
    company) defaults ON for the sandbox — which scores asynchronously, so the UI
    needs something to render — and OFF for prod, where real pulled data exists and
    fabricated signals would pollute it. An explicit ?seed=true/false always wins.
    """
    from app.config import settings

    is_prod = settings.RECOTAP_ENVIRONMENT.strip().lower() == "prod"
    do_seed = (not is_prod) if seed is None else seed
    # Incremental by default (only changed accounts); ?full=true forces a complete
    # re-pull (useful as a periodic safety net since lastSync omits deletions).
    pulled = await recotap_pull(session, incremental=not full)
    seeded = await recotap_seed(session, overwrite=overwrite) if do_seed else {"seeded": 0}
    # Always derive journey stage from CRM deal progress LAST, so it wins over
    # Recotap's intent stage for accounts with an active deal.
    crm = await recotap_crm_sync(session)
    return {"pull": pulled, "seed": seeded, "seeded_mock": do_seed, "crm_journey": crm}


@router.get("/recotap/summary")
async def recotap_summary(_user: CurrentUser, session: DBSession = None):
    """Journey-stage + engagement counts across sourced accounts — powers the
    Account Sourcing journey funnel + filter chips."""
    total = (
        await session.execute(select(func.count(Company.id)).where(_account_sourcing_visibility_filter()))
    ).scalar_one()
    stage_rows = (
        await session.execute(
            select(RecotapAccount.journey_stage, func.count())
            .where(RecotapAccount.company_id.is_not(None))
            .group_by(RecotapAccount.journey_stage)
        )
    ).all()
    stages = {s: 0 for s in RECOTAP_JOURNEY_STAGES}
    scored = 0
    for stage, cnt in stage_rows:
        if stage and stage in stages:
            stages[stage] = cnt
            scored += cnt
    eng_rows = (
        await session.execute(
            select(RecotapAccount.engagement, func.count())
            .where(RecotapAccount.company_id.is_not(None))
            .group_by(RecotapAccount.engagement)
        )
    ).all()
    engagement = {e: 0 for e in RECOTAP_ENGAGEMENT_LEVELS}
    for eng, cnt in eng_rows:
        if eng and eng in engagement:
            engagement[eng] = cnt
    return {
        "stages": stages,
        "engagement": engagement,
        "scored": scored,
        "not_scored": max(0, total - scored),
        "total": total,
    }


@router.post("/recotap/push")
async def push_recotap_crm_status(
    _user: CurrentUser,
    session: DBSession = None,
    limit: int | None = None,
    dry_run: bool = False,
):
    """Push Beacon CRM deal-stage status to Recotap (a custom field when configured,
    else the legacy 'CRM: ...' tag) via upsert. `limit` caps the number pushed
    (test runs); `dry_run=true` returns the exact payloads it WOULD send WITHOUT
    writing anything to Recotap."""
    return await recotap_push_status(session, limit=limit, dry_run=dry_run)


@router.put("/companies/{company_id}", response_model=CompanyRead)
async def update_sourced_company(company_id: UUID, payload: CompanyUpdate, current_user: CurrentUser, session: DBSession = None):
    """Update sourced company workflow fields like owner, disposition, and rep feedback."""
    repo = CompanyRepository(session)
    company = await repo.get_or_raise(company_id)
    if not _can_see_company(company, current_user):
        raise HTTPException(status_code=404, detail="Company not found")

    update_data = payload.model_dump(exclude_unset=True)
    changed_fields = {
        key: {"before": getattr(company, key, None), "after": value}
        for key, value in update_data.items()
        if getattr(company, key, None) != value
    }
    for key, value in update_data.items():
        setattr(company, key, value)

    if (
        "outreach_status" in update_data
        and update_data.get("outreach_status")
        and update_data.get("outreach_status") != "not_started"
        and "last_outreach_at" not in update_data
        and not company.last_outreach_at
    ):
        # First-touch timestamps are inferred from the first non-default status so
        # the UI can sort/filter on outreach recency without extra client logic.
        company.last_outreach_at = datetime.utcnow()

    if update_data.get("assigned_rep_email") and not update_data.get("assigned_rep"):
        company.assigned_rep = update_data["assigned_rep_email"]
    if update_data.get("assigned_rep_name") and not update_data.get("assigned_rep"):
        company.assigned_rep = update_data["assigned_rep_name"]

    contacts = (
        await session.execute(select(Contact).where(Contact.company_id == company.id))
    ).scalars().all()
    for contact in contacts:
        # Keep contact-level sequencing aligned with the latest company owner and
        # outreach lane whenever the account record changes.
        if company.assigned_rep_email:
            contact.assigned_rep_email = company.assigned_rep_email
        if company.recommended_outreach_lane and not contact.outreach_lane:
            contact.outreach_lane = company.recommended_outreach_lane
        refresh_contact_sequence_plan(contact, company)
        session.add(contact)

    refresh_company_prospecting_fields(company, contacts)
    if changed_fields:
        summary = ", ".join(
            f"{field.replace('_', ' ')} -> {str(change['after'])[:60]}"
            for field, change in list(changed_fields.items())[:3]
        )
        append_company_activity_log(
            company,
            action="company_updated",
            actor_name=current_user.name,
            actor_email=current_user.email,
            message=f"Updated {summary}",
            metadata={"changes": changed_fields},
        )
    company.updated_at = datetime.utcnow()
    company.icp_score, company.icp_tier = score_company(company)
    return await repo.save(company)


@router.get("/export")
async def export_sourced_companies(
    _user: CurrentUser,
    session: DBSession = None,
    assigned_rep: str | None = Query(default=None),
    assigned_rep_email: str | None = Query(default=None),
    disposition: str | None = Query(default=None),
    batch_id: UUID | None = Query(default=None),
    prospects_min: int | None = Query(default=None, ge=0),
    prospects_max: int | None = Query(default=None, ge=0),
):
    """Export sourced companies and preserved source columns as CSV.

    Accepts the same `prospects_min`/`prospects_max` bounds as the list
    endpoint so the user can "download the filtered list" without the
    frontend having to enumerate every visible company id.
    """
    stmt = (
        select(Company)
        .where(Company.sourcing_batch_id.isnot(None))
        .where(company_visibility_filter(_user.id, _user.role == "admin"))
        .order_by(Company.created_at.desc())
    )
    if assigned_rep:
        stmt = stmt.where(Company.assigned_rep == assigned_rep)
    if assigned_rep_email:
        stmt = stmt.where(Company.assigned_rep_email == assigned_rep_email)
    if disposition:
        stmt = stmt.where(Company.disposition == disposition)
    if batch_id:
        stmt = stmt.where(Company.sourcing_batch_id == batch_id)

    if prospects_min is not None or prospects_max is not None:
        prospect_count_sub = (
            select(Contact.company_id, func.count(Contact.id).label("cnt"))
            .group_by(Contact.company_id)
            .subquery()
        )
        prospect_count = func.coalesce(prospect_count_sub.c.cnt, 0)
        stmt = stmt.outerjoin(prospect_count_sub, Company.id == prospect_count_sub.c.company_id)
        if prospects_min is not None:
            stmt = stmt.where(prospect_count >= prospects_min)
        if prospects_max is not None:
            stmt = stmt.where(prospect_count <= prospects_max)

    companies = (await session.execute(stmt)).scalars().all()
    rows = [_company_export_row(company) for company in companies]

    headers: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in headers:
                headers.append(key)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers or ["company_id"])
    writer.writeheader()
    if rows:
        writer.writerows(rows)

    content = buffer.getvalue()
    filename = f"sourced_companies_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export-contacts")
async def export_sourced_contacts(
    current_user: CurrentUser,
    session: DBSession,
    assigned_rep_email: str | None = Query(default=None),
    batch_id: UUID | None = Query(default=None),
):
    stmt = (
        select(Contact, Company)
        .join(Company, Contact.company_id == Company.id)
        .where(Company.sourcing_batch_id.isnot(None))
        .order_by(Company.created_at.desc(), Contact.created_at.desc())
    )
    # Prospect-visibility: a non-admin may export only their own + unassigned
    # contacts (this CSV emits full name/email/linkedin, so an ungated export was
    # a full-identity workspace dump).
    restriction = await visible_contact_restriction(session, current_user)
    if restriction is not None:
        stmt = stmt.where(restriction)
    if assigned_rep_email:
        stmt = stmt.where(
            (Contact.assigned_rep_email == assigned_rep_email) | (Company.assigned_rep_email == assigned_rep_email)
        )
    if batch_id:
        stmt = stmt.where(Company.sourcing_batch_id == batch_id)

    rows = []
    for contact, company in (await session.execute(stmt)).all():
        rows.append(_contact_export_row(company, contact))

    headers: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in headers:
                headers.append(key)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers or ["contact_id"])
    writer.writeheader()
    if rows:
        writer.writerows(rows)

    content = buffer.getvalue()
    filename = f"sourced_contacts_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Company Re-enrich ─────────────────────────────────────────────────────────

@router.post("/companies/{company_id}/re-enrich")
async def re_enrich_company(company_id: UUID, _user: CurrentUser, session: DBSession = None):
    """Re-run the deep TAL / ICP research pipeline for a company."""
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    try:
        from app.tasks.enrichment import icp_research_single_task

        task = icp_research_single_task.delay(str(company_id))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to queue company re-enrichment") from exc
    return {
        "company_id": str(company_id),
        "task_id": task.id,
        "status": "queued",
        "message": "Deep research re-enrichment started",
    }


@router.post("/companies/bulk-icp-research")
async def bulk_icp_research_companies(
    unenriched_only: bool = Query(default=False, description="Only queue companies with no enriched_at timestamp"),
    session: DBSession = None,
    _: AdminUser = None,
):
    """Queue free ICP research (no Apollo/Hunter credits) for all sourced companies.

    Uses existing DB contacts + web research + Claude analysis.
    """
    stmt = select(Company).where(
        ~Company.enrichment_sources.contains({"clickup_import": {}}),
        ~Company.enrichment_sources.contains({"prospect_import_placeholder": {}}),
    )
    if unenriched_only:
        stmt = stmt.where(Company.enriched_at.is_(None))

    result = await session.execute(stmt)
    companies = result.scalars().all()

    try:
        from app.tasks.enrichment import icp_research_free_task
    except Exception as exc:
        raise HTTPException(status_code=500, detail="ICP research task not available") from exc

    queued = 0
    for company in companies:
        try:
            icp_research_free_task.delay(str(company.id))
            queued += 1
        except Exception:
            pass

    return {
        "queued": queued,
        "total": len(companies),
        "unenriched_only": unenriched_only,
        "message": f"Queued {queued} companies for free ICP research (no Apollo/Hunter credits)",
    }


@router.post("/companies/bulk-enrich")
async def bulk_enrich_companies(
    unenriched_only: bool = Query(default=False, description="Only queue companies with no enriched_at timestamp"),
    session: DBSession = None,
    _: AdminUser = None,
):
    """Queue ICP research for all (or unenriched-only) sourced companies.

    Returns counts of how many tasks were queued and skipped.
    """
    stmt = select(Company).where(
        ~Company.enrichment_sources.contains({"clickup_import": {}}),
        ~Company.enrichment_sources.contains({"prospect_import_placeholder": {}}),
    )
    if unenriched_only:
        stmt = stmt.where(Company.enriched_at.is_(None))

    result = await session.execute(stmt)
    companies = result.scalars().all()

    try:
        from app.tasks.enrichment import icp_research_single_task
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Enrichment task not available") from exc

    queued = 0
    for company in companies:
        try:
            icp_research_single_task.delay(str(company.id))
            queued += 1
        except Exception:
            pass

    return {
        "queued": queued,
        "total": len(companies),
        "unenriched_only": unenriched_only,
        "message": f"Queued {queued} companies for enrichment",
    }


@router.post("/companies/{company_id}/icp-research")
async def icp_research_company(company_id: UUID, _user: CurrentUser, session: DBSession = None):
    """Run the full ICP intelligence pipeline for a single company.

    Uses web search, Apollo, website scraping, and Claude AI to produce
    comprehensive ICP analysis with TAL filtering and intent scoring.
    """
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    try:
        from app.tasks.enrichment import icp_research_single_task

        task = icp_research_single_task.delay(str(company_id))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to queue ICP research") from exc
    return {
        "company_id": str(company_id),
        "task_id": task.id,
        "status": "queued",
        "message": "ICP intelligence research started — this takes 15-30 seconds per company",
    }


# ── Company Contacts ──────────────────────────────────────────────────────────

@router.get("/companies/{company_id}/contacts", response_model=list[ContactRead])
async def get_company_contacts(company_id: UUID, session: DBSession, current_user: CurrentUser):
    """Get the contacts for a company, scoped to what the caller may see.

    A non-admin normally sees only the company's contacts they own (either slot)
    plus unassigned ones — this prevents the cross-rep prospect-visibility leak.
    The one exception: if the caller OWNS this account (its AE or SDR slot), they
    see ALL of its prospects (owning the account means seeing everyone worked
    there, even prospects a teammate is assigned at the prospect level).
    """
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    stmt = (
        select(Contact)
        .where(Contact.company_id == company_id)
        .order_by(Contact.created_at.desc())
    )
    restriction = await visible_contact_restriction(session, current_user)
    # Owning the account (its AE or SDR slot) grants visibility to ALL of that
    # account's prospects — even ones a teammate is assigned at the prospect level.
    # Without this, an account owner saw only prospects assigned directly to them
    # (e.g. Dynamo Software's account SDR could see just 1 of 7 prospects, the rest
    # being co-owned by the account AE).
    #
    # SDRs are EXCLUDED from this account-owner bypass: they are hard-restricted to
    # their OWN prospects everywhere, so even on an account they own they see only
    # prospects in their own slots — never an AE-held teammate's prospect.
    is_sdr = (current_user.role or "").lower() == "sdr"
    owns_account = (
        not is_sdr
        and current_user.id is not None
        and current_user.id in {company.assigned_to_id, company.sdr_id}
    )
    if restriction is not None and not owns_account:
        stmt = stmt.where(restriction)
    result = await session.execute(stmt)
    all_contacts = result.scalars().all()
    filtered_contacts = [contact for contact in all_contacts if is_priority_stakeholder_candidate(contact)]
    contacts = filtered_contacts or all_contacts
    reads = [ContactRead.model_validate(contact) for contact in contacts]
    for read in reads:
        read.company_name = company.name
    await apply_contact_tracking(session, reads)
    return reads


@router.get("/contacts/{contact_id}", response_model=ContactRead)
async def get_company_contact(contact_id: UUID, _user: CurrentUser, session: DBSession = None):
    contact = await session.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    company_name = None
    if contact.company_id:
        company = await session.get(Company, contact.company_id)
        if company:
            company_name = company.name
    return await to_contact_read(session, contact, company_name=company_name)


@router.put("/contacts/{contact_id}", response_model=ContactRead)
async def update_company_contact(contact_id: UUID, payload: ContactUpdate, _user: CurrentUser, session: DBSession = None):
    contact = await session.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(contact, key, value)

    if contact.company_id:
        company = await session.get(Company, contact.company_id)
        if company:
            refresh_contact_sequence_plan(contact, company)

    contact.updated_at = datetime.utcnow()
    session.add(contact)
    await session.commit()
    await session.refresh(contact)

    if contact.company_id:
        company = await session.get(Company, contact.company_id)
        if company:
            company_contacts = (
                await session.execute(select(Contact).where(Contact.company_id == company.id))
            ).scalars().all()
            refresh_company_prospecting_fields(company, company_contacts)
            company.updated_at = datetime.utcnow()
            session.add(company)
            await session.commit()
            return await to_contact_read(session, contact, company_name=company.name)
    return await to_contact_read(session, contact)


# ── Notes ─────────────────────────────────────────────────────────────────────

class NoteCreate(BaseModel):
    body: str


@router.post("/companies/{company_id}/notes")
async def add_company_note(company_id: UUID, payload: NoteCreate, session: DBSession, current_user: CurrentUser):
    """Append a manual note to the company's activity log."""
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Note body cannot be empty")
    append_company_activity_log(
        company,
        action="note",
        actor_name=current_user.name,
        actor_email=current_user.email,
        message=body,
        metadata={"type": "manual_note"},
    )
    company.updated_at = datetime.utcnow()
    session.add(company)
    await session.commit()
    await session.refresh(company)
    cache = company.enrichment_cache or {}
    return {"activity_log": cache.get("activity_log", [])}


@router.post("/contacts/{contact_id}/notes")
async def add_contact_note(contact_id: UUID, payload: NoteCreate, session: DBSession, current_user: CurrentUser):
    """Append a manual note to a contact stored in the enrichment_data JSON field."""
    contact = await session.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Note body cannot be empty")
    import copy
    data = copy.deepcopy(contact.enrichment_data or {})
    existing = data.get("notes_log")
    entries = list(existing) if isinstance(existing, list) else []
    entries.append({
        "action": "note",
        "message": body,
        "actor_name": current_user.name,
        "actor_email": current_user.email,
        "at": datetime.utcnow().isoformat(),
        "metadata": {"type": "manual_note"},
    })
    data["notes_log"] = entries[-40:]
    contact.enrichment_data = data
    contact.updated_at = datetime.utcnow()
    session.add(contact)
    await session.commit()
    await session.refresh(contact)
    return {"notes_log": (contact.enrichment_data or {}).get("notes_log", [])}


# ── Contact Re-enrich ─────────────────────────────────────────────────────────

@router.post("/contacts/{contact_id}/re-enrich")
async def re_enrich_contact(contact_id: UUID, _user: CurrentUser, session: DBSession = None):
    """Re-enrich a single contact via Apollo + AI persona classification."""
    contact = await session.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    try:
        from app.tasks.enrichment import re_enrich_contact_task

        task = re_enrich_contact_task.delay(str(contact_id))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to queue contact re-enrichment") from exc
    return {
        "contact_id": str(contact_id),
        "task_id": task.id,
        "status": "queued",
        "message": "Contact re-enrichment started",
    }


# ── Push to Instantly (placeholder) ───────────────────────────────────────────

@router.post("/companies/{company_id}/push-instantly")
async def push_to_instantly(
    company_id: UUID,
    _user: CurrentUser,
    campaign_id: str = "default",
    session: DBSession = None,
):
    """Push company contacts to an Instantly email campaign."""
    company = await session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Get contacts with emails
    result = await session.execute(
        select(Contact)
        .where(Contact.company_id == company_id, Contact.email.isnot(None))
    )
    contacts = result.scalars().all()

    if not contacts:
        raise HTTPException(status_code=400, detail="No contacts with emails found for this company")

    from app.clients.instantly import InstantlyClient
    from app.clients.instantly_events import INSTANTLY_WEBHOOK_EVENTS
    from app.config import settings
    instantly = InstantlyClient()

    # Ensure webhooks are registered so we receive event callbacks
    if settings.INSTANTLY_WEBHOOK_URL:
        try:
            await instantly.ensure_webhook(
                url=settings.INSTANTLY_WEBHOOK_URL,
                event_types=INSTANTLY_WEBHOOK_EVENTS,
            )
        except Exception:
            pass  # non-fatal

    results = []
    lead_payloads = []
    for contact in contacts:
        lead_payloads.append({
            "email": contact.email,
            "first_name": contact.first_name or "",
            "last_name": contact.last_name or "",
            "company_name": company.name,
            "job_title": contact.title or "",
            "linkedin_url": contact.linkedin_url or "",
        })
        contact.instantly_status = "pushed"
        contact.sequence_status = "queued_instantly"
        contact.instantly_campaign_id = campaign_id
        session.add(contact)

    # Bulk add leads to Instantly (up to 1000 per call)
    try:
        r = await instantly.add_leads_bulk(campaign_id=campaign_id, leads=lead_payloads)
        results = [{"status": "pushed"} for _ in lead_payloads]
    except Exception as e:
        results = [{"status": "error", "error": str(e)} for _ in lead_payloads]

    company.instantly_campaign_id = campaign_id
    session.add(company)
    await session.commit()

    return {
        "company_id": str(company_id),
        "company_name": company.name,
        "contacts_pushed": len(results),
        "campaign_id": campaign_id,
        "results": results,
    }


# ── Batches List ───────────────────────────────────────────────────────────────

@router.get("/batches", response_model=list[SourcingBatchRead])
async def list_batches(_user: CurrentUser, session: DBSession = None, page: Pagination = None):
    """List all sourcing batches."""
    result = (
        await session.execute(
        select(SourcingBatch)
        .offset(page.skip)
        .limit(page.limit)
        .order_by(SourcingBatch.created_at.desc())
        )
    ).scalars().all()
    return [await _build_batch_read(session, batch) for batch in result]
