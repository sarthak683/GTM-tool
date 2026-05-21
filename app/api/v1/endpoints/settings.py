"""
Settings endpoints — workspace-level configuration.

GET  /settings/outreach  → current outreach sequence defaults
PATCH /settings/outreach → update step delays
GET  /settings/outreach-content → current outreach AI templates and guidance
PATCH /settings/outreach-content → update outreach AI templates and guidance
GET  /settings/email-sync → current Gmail sync status
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlmodel import select as sm_select

from app.config import settings
from app.core.dependencies import AdminUser, CurrentUser, DBSession
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.models.settings import (
    ClickUpCrmSettingsRead,
    ClickUpCrmSettingsUpdate,
    DealStageSettingsRead,
    DealStageSettingsUpdate,
    DealFunnelSettingsRead,
    DealFunnelSettingsUpdate,
    GmailConnectUrlRead,
    GmailSettingsRead,
    GmailSettingsUpdate,
    OutreachContentSettingsRead,
    OutreachContentSettingsUpdate,
    OutreachSettingsRead,
    OutreachSettingsUpdate,
    OutreachTimingStep,
    PipelineSummarySettingsRead,
    PipelineSummarySettingsUpdate,
    PreMeetingAutomationSettingsRead,
    ReportSenderSettingsRead,
    ReportSenderSettingsUpdate,
    SalesReportSettingsRead,
    SalesReportSettingsUpdate,
    ProspectStageSettingsRead,
    ProspectStageSettingsUpdate,
    PreMeetingAutomationSettingsUpdate,
    RolePermissionsRead,
    RolePermissionsUpdate,
    StageBucketSettings,
    SyncScheduleSettingsRead,
    SyncScheduleSettingsUpdate,
    WorkspaceSettings,
    ZippySystemPromptRead,
    ZippySystemPromptUpdate,
)
from app.models.user_email_connection import UserEmailConnection
from app.services.deal_stages import (
    DEFAULT_DEAL_STAGE_SETTINGS,
    filter_funnel_config_to_stage_ids,
    get_configured_deal_stage_ids,
    normalize_deal_stage_settings,
)
from app.services.gmail_oauth import (
    GMAIL_SEND_SCOPE,
    GOOGLE_EMAIL_SCOPE,
    build_gmail_connect_url,
    create_gmail_oauth_state,
    decode_gmail_oauth_state,
    exchange_gmail_code,
)
from app.services.meeting_automation import normalize_pre_meeting_settings, run_due_pre_meeting_intel_once
from app.services.permissions import normalize_role_permissions
from app.services.us_pod_call_report import normalize_sales_report_settings

router = APIRouter(prefix="/settings", tags=["settings"])

_DEFAULTS = [0, 3, 7]
_DEFAULT_OUTREACH_CONTENT = {
    "general_prompt": (
        "Write concise enterprise outbound emails for Beacon.li. Personalize to the contact and company, "
        "avoid hype, avoid fluff, and keep the CTA low-friction."
    ),
    "linkedin_prompt": (
        "Keep LinkedIn notes conversational and specific to the person's role or recent company context."
    ),
    "step_templates": [
        {
            "step_number": 1,
            "channel": "email",
            "label": "Initial email",
            "goal": "Start a personalized conversation with a specific reason for reaching out.",
            "subject_hint": "Quick question about {{company_name}}",
            "body_template": (
                "Hi {{first_name}},\n\n"
                "Noticed {{company_name}} is pushing on {{reason_to_reach_out}}. Beacon helps teams reduce "
                "implementation drag without replacing the systems they already run.\n\n"
                "Worth a quick compare?"
            ),
            "prompt_hint": "Open with a strong personalization point and end with a simple CTA.",
        },
        {
            "step_number": 2,
            "channel": "linkedin",
            "label": "Follow-up",
            "goal": "Make a light LinkedIn touch so the rep can stay visible between emails.",
            "subject_hint": None,
            "body_template": (
                "Reference the contact's role, the first email, and one concrete reason to connect on LinkedIn."
            ),
            "prompt_hint": "Keep it short and human. This is a LinkedIn touch, not a full email.",
        },
        {
            "step_number": 3,
            "channel": "call",
            "label": "Final touch",
            "goal": "Give the SDR a concise call step with context and a reason to reach out live.",
            "subject_hint": None,
            "body_template": (
                "Call this contact and reference the latest company signal plus the most relevant implementation pain Beacon can solve."
            ),
            "prompt_hint": "Make this feel like a live talk track the SDR can use during the call, not an email.",
        },
    ],
}
_DEFAULT_DEAL_FUNNEL = {
    "active": [
        "reprospect",
        "demo_scheduled",
        "demo_done",
        "qualified_lead",
        "poc_agreed",
        "poc_wip",
        "poc_done",
        "commercial_negotiation",
        "msa_review",
    ],
    "inactive": ["closed_won", "churned", "not_a_fit", "cold", "closed_lost", "on_hold", "nurture", "closed"],
    "tofu": ["qualified_lead", "poc_agreed"],
    "mofu": ["poc_wip", "poc_done", "commercial_negotiation", "msa_review", "workshop"],
    "bofu": ["closed_won"],
    "visible_cards": ["active", "inactive", "tofu", "mofu", "bofu", "total"],
}
_DEFAULT_PROSPECT_FUNNEL = {
    "active": ["outreach", "in_progress", "meeting_booked"],
    "inactive": ["negative_response", "no_response", "not_a_fit"],
    "tofu": ["outreach"],
    "mofu": ["in_progress"],
    "bofu": ["meeting_booked"],
    "visible_cards": ["active", "inactive", "tofu", "mofu", "bofu", "total"],
}
_SUMMARY_CARD_KEYS = {"active", "inactive", "tofu", "mofu", "bofu", "total"}
_DEFAULT_ROLE_PERMISSIONS = {
    "ae": {
        "crm_import": False,
        "prospect_migration": True,
        "manage_team": False,
        "run_pre_meeting_intel": True,
    },
    "sdr": {
        "crm_import": False,
        "prospect_migration": True,
        "manage_team": False,
        "run_pre_meeting_intel": False,
    },
}
_DEFAULT_CLICKUP_CRM_SETTINGS = {
    "team_id": settings.CLICKUP_TEAM_ID or None,
    "space_id": settings.CLICKUP_SPACE_ID or None,
    "deals_list_id": settings.CLICKUP_DEALS_LIST_ID or None,
}
_PROSPECT_STAGES = {"outreach", "in_progress", "meeting_booked", "negative_response", "no_response", "not_a_fit"}


async def _get_or_create(session) -> WorkspaceSettings:
    """Return the single settings row, creating it with defaults if absent."""
    row = await session.get(WorkspaceSettings, 1)
    if row is None:
        row = WorkspaceSettings(
            id=1,
            outreach_step_delays=_DEFAULTS,
            outreach_content_settings=_DEFAULT_OUTREACH_CONTENT,
            deal_funnel_config=_DEFAULT_DEAL_FUNNEL,
            deal_stage_settings=[dict(item) for item in DEFAULT_DEAL_STAGE_SETTINGS],
            role_permissions=_DEFAULT_ROLE_PERMISSIONS,
            prospect_funnel_config=_DEFAULT_PROSPECT_FUNNEL,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    elif not row.deal_stage_settings:
        row.deal_stage_settings = [dict(item) for item in DEFAULT_DEAL_STAGE_SETTINGS]
        session.add(row)
        await session.commit()
        await session.refresh(row)
    changed = False
    if not row.role_permissions:
        row.role_permissions = _DEFAULT_ROLE_PERMISSIONS
        changed = True
    if not row.pre_meeting_automation_settings:
        row.pre_meeting_automation_settings = normalize_pre_meeting_settings(None)
        changed = True
    if not row.clickup_crm_settings:
        row.clickup_crm_settings = dict(_DEFAULT_CLICKUP_CRM_SETTINGS)
        changed = True
    if changed:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


def _normalized_role_permissions(value: dict | None) -> RolePermissionsRead:
    normalized = normalize_role_permissions(value)
    return RolePermissionsRead(**normalized)


def _normalized_pre_meeting_settings(value: dict | None) -> PreMeetingAutomationSettingsRead:
    return PreMeetingAutomationSettingsRead(**normalize_pre_meeting_settings(value))


def _normalized_clickup_crm_settings(value: dict | None) -> ClickUpCrmSettingsRead:
    raw = value if isinstance(value, dict) else {}

    def _normalize_clickup_id(v: object) -> str | None:
        if v in (None, ""):
            return None
        text = str(v).strip()
        if not text:
            return None

        # Accept scientific notation / float-like values and normalize to digits.
        try:
            numeric = Decimal(text)
            if numeric == numeric.to_integral_value():
                return str(int(numeric))
        except (InvalidOperation, ValueError):
            pass

        digits = "".join(ch for ch in text if ch.isdigit())
        return digits or text

    return ClickUpCrmSettingsRead(
        team_id=_normalize_clickup_id(raw.get("team_id") or _DEFAULT_CLICKUP_CRM_SETTINGS["team_id"]),
        space_id=_normalize_clickup_id(raw.get("space_id") or _DEFAULT_CLICKUP_CRM_SETTINGS["space_id"]),
        deals_list_id=_normalize_clickup_id(raw.get("deals_list_id") or _DEFAULT_CLICKUP_CRM_SETTINGS["deals_list_id"]),
    )


def _normalized_bucket_config(value: dict | None, default: dict[str, list[str]]) -> StageBucketSettings:
    raw = value if isinstance(value, dict) else {}
    return StageBucketSettings(
        active=list(raw.get("active") or default.get("active") or []),
        inactive=list(raw.get("inactive") or default.get("inactive") or []),
        tofu=list(raw.get("tofu") or default["tofu"]),
        mofu=list(raw.get("mofu") or default["mofu"]),
        bofu=list(raw.get("bofu") or default["bofu"]),
    )


def _normalized_outreach_content(value: dict | None, step_count: int | None = None) -> OutreachContentSettingsRead:
    raw = value if isinstance(value, dict) else {}
    raw_steps = raw.get("step_templates")
    steps = raw_steps if isinstance(raw_steps, list) and raw_steps else _DEFAULT_OUTREACH_CONTENT["step_templates"]
    normalized_steps = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        normalized_steps.append(
            {
                "step_number": int(step.get("step_number") or idx),
                "channel": str(step.get("channel") or "email").strip().lower() or "email",
                "label": str(step.get("label") or f"Step {idx}"),
                "goal": str(step.get("goal") or ""),
                "subject_hint": str(step.get("subject_hint") or "") or None,
                "body_template": str(step.get("body_template") or "") or None,
                "prompt_hint": str(step.get("prompt_hint") or "") or None,
            }
        )
    if not normalized_steps:
        normalized_steps = _DEFAULT_OUTREACH_CONTENT["step_templates"]
    normalized_steps.sort(key=lambda step: step["step_number"])
    target_steps = step_count or len(normalized_steps)
    while len(normalized_steps) < target_steps:
        last = normalized_steps[-1]
        normalized_steps.append(
            {
                **last,
                "step_number": len(normalized_steps) + 1,
                "label": f"Step {len(normalized_steps) + 1}",
            }
        )
    return OutreachContentSettingsRead(
        general_prompt=str(raw.get("general_prompt") or _DEFAULT_OUTREACH_CONTENT["general_prompt"]),
        linkedin_prompt=str(raw.get("linkedin_prompt") or _DEFAULT_OUTREACH_CONTENT["linkedin_prompt"]),
        step_templates=normalized_steps,
    )


def _normalized_deal_funnel_config(value: dict | None) -> DealFunnelSettingsRead:
    normalized = _normalized_bucket_config(value, _DEFAULT_DEAL_FUNNEL)
    return DealFunnelSettingsRead(tofu=normalized.tofu, mofu=normalized.mofu, bofu=normalized.bofu)


def _normalized_visible_cards(value: dict | None, defaults: dict) -> list[str]:
    visible_cards = value.get("visible_cards") if isinstance(value, dict) else None
    if not isinstance(visible_cards, list):
        visible_cards = defaults.get("visible_cards")
    normalized = [card for card in visible_cards or [] if card in _SUMMARY_CARD_KEYS]
    return normalized or list(defaults.get("visible_cards") or ["active", "inactive", "tofu", "mofu", "bofu", "total"])


def _normalized_pipeline_summary_settings(row: WorkspaceSettings) -> PipelineSummarySettingsRead:
    allowed_deal_stage_ids = [stage["id"] for stage in normalize_deal_stage_settings(row.deal_stage_settings)]
    normalized_deal_funnel = filter_funnel_config_to_stage_ids(row.deal_funnel_config, allowed_deal_stage_ids, _DEFAULT_DEAL_FUNNEL)
    return PipelineSummarySettingsRead(
        deal={
            **_normalized_bucket_config(normalized_deal_funnel, _DEFAULT_DEAL_FUNNEL).model_dump(),
            "visible_cards": _normalized_visible_cards(normalized_deal_funnel, _DEFAULT_DEAL_FUNNEL),
        },
        prospect={
            **_normalized_bucket_config(row.prospect_funnel_config, _DEFAULT_PROSPECT_FUNNEL).model_dump(),
            "visible_cards": _normalized_visible_cards(row.prospect_funnel_config, _DEFAULT_PROSPECT_FUNNEL),
        },
    )


def _validate_funnel_stage_ids(stage_ids: list[str], allowed_stages: set[str] | list[str], scope: str) -> None:
    allowed = set(allowed_stages)
    invalid = [stage for stage in stage_ids if stage not in allowed]
    if invalid:
        raise HTTPException(status_code=422, detail=f"Unknown {scope} stages in funnel settings: {', '.join(sorted(set(invalid)))}")


def _validate_visible_cards(cards: list[str], scope: str) -> None:
    invalid = [card for card in cards if card not in _SUMMARY_CARD_KEYS]
    if invalid:
        raise HTTPException(status_code=422, detail=f"Unknown {scope} summary cards: {', '.join(sorted(set(invalid)))}")


@router.get("/workspace", response_model=dict)
async def get_workspace_settings(session: DBSession, _user: CurrentUser):
    """Return a compact snapshot of workspace-level settings."""
    row = await _get_or_create(session)
    normalized_deal_stages = normalize_deal_stage_settings(row.deal_stage_settings)
    return {
        "id": row.id,
        "outreach_step_delays": row.outreach_step_delays or _DEFAULTS,
        "outreach_content_settings": _normalized_outreach_content(
            row.outreach_content_settings,
            step_count=len(row.outreach_step_delays or _DEFAULTS),
        ).model_dump(),
        "deal_stage_settings": normalized_deal_stages,
        "pipeline_summary": _normalized_pipeline_summary_settings(row).model_dump(),
        "role_permissions": _normalized_role_permissions(row.role_permissions).model_dump(),
        "pre_meeting_automation_settings": _normalized_pre_meeting_settings(
            row.pre_meeting_automation_settings
        ).model_dump(),
        "sync_schedule_settings": _normalized_sync_schedule(row.sync_schedule_settings).model_dump(),
        "clickup_crm_settings": _normalized_clickup_crm_settings(row.clickup_crm_settings).model_dump(),
        "updated_at": datetime.utcnow().isoformat(),
    }


@router.get("/deal-stages", response_model=DealStageSettingsRead)
async def get_deal_stage_settings(session: DBSession, _user: CurrentUser):
    row = await _get_or_create(session)
    return DealStageSettingsRead(stages=normalize_deal_stage_settings(row.deal_stage_settings))


@router.patch("/deal-stages", response_model=DealStageSettingsRead)
async def update_deal_stage_settings(body: DealStageSettingsUpdate, session: DBSession, _admin: AdminUser):
    stages = normalize_deal_stage_settings([stage.model_dump() for stage in body.stages])
    if not stages:
        raise HTTPException(status_code=422, detail="At least one deal stage is required")

    stage_ids = [stage["id"] for stage in stages]
    row = await _get_or_create(session)
    row.deal_stage_settings = stages
    row.deal_funnel_config = filter_funnel_config_to_stage_ids(row.deal_funnel_config, stage_ids, _DEFAULT_DEAL_FUNNEL)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return DealStageSettingsRead(stages=normalize_deal_stage_settings(row.deal_stage_settings))


_DEFAULT_PROSPECT_STAGES = [
    {"id": "outreach", "label": "Outreach", "group": "active", "color": "#2563eb"},
    {"id": "in_progress", "label": "In Progress", "group": "active", "color": "#7c3aed"},
    {"id": "meeting_booked", "label": "Meeting Booked", "group": "active", "color": "#0ea5e9"},
    {"id": "negative_response", "label": "Negative Response", "group": "closed", "color": "#ef4444"},
    {"id": "no_response", "label": "No Response", "group": "closed", "color": "#94a3b8"},
    {"id": "not_a_fit", "label": "Not a Fit", "group": "closed", "color": "#9ca3af"},
]


def _normalize_prospect_stage_settings(raw: list[dict] | None) -> list[dict]:
    if not raw:
        return _DEFAULT_PROSPECT_STAGES
    stages = []
    for stage in raw:
        if not isinstance(stage, dict) or not stage.get("id"):
            continue
        stages.append({
            "id": stage["id"],
            "label": stage.get("label") or stage["id"].replace("_", " ").title(),
            "group": stage.get("group", "active") if stage.get("group") in ("active", "closed") else "active",
            "color": stage.get("color") or "#94a3b8",
        })
    return stages or _DEFAULT_PROSPECT_STAGES


@router.get("/prospect-stages", response_model=ProspectStageSettingsRead)
async def get_prospect_stage_settings(session: DBSession, _user: CurrentUser):
    row = await _get_or_create(session)
    return ProspectStageSettingsRead(stages=_normalize_prospect_stage_settings(row.prospect_stage_settings))


@router.patch("/prospect-stages", response_model=ProspectStageSettingsRead)
async def update_prospect_stage_settings(body: ProspectStageSettingsUpdate, session: DBSession, _admin: AdminUser):
    stages = _normalize_prospect_stage_settings([stage.model_dump() for stage in body.stages])
    if not stages:
        raise HTTPException(status_code=422, detail="At least one prospect stage is required")

    stage_ids = [stage["id"] for stage in stages]
    row = await _get_or_create(session)
    row.prospect_stage_settings = stages
    row.prospect_funnel_config = filter_funnel_config_to_stage_ids(row.prospect_funnel_config, stage_ids, _DEFAULT_PROSPECT_FUNNEL)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ProspectStageSettingsRead(stages=_normalize_prospect_stage_settings(row.prospect_stage_settings))


@router.get("/clickup-crm", response_model=ClickUpCrmSettingsRead)
async def get_clickup_crm_settings(session: DBSession, _user: CurrentUser):
    row = await _get_or_create(session)
    return _normalized_clickup_crm_settings(row.clickup_crm_settings)


@router.patch("/clickup-crm", response_model=ClickUpCrmSettingsRead)
async def update_clickup_crm_settings(body: ClickUpCrmSettingsUpdate, session: DBSession, _admin: AdminUser):
    def _normalize_clickup_id(v: str | None) -> str | None:
        text = (v or "").strip()
        if not text:
            return None
        try:
            numeric = Decimal(text)
            if numeric == numeric.to_integral_value():
                return str(int(numeric))
        except (InvalidOperation, ValueError):
            pass
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits or text

    row = await _get_or_create(session)
    row.clickup_crm_settings = {
        "team_id": _normalize_clickup_id(body.team_id),
        "space_id": _normalize_clickup_id(body.space_id),
        "deals_list_id": _normalize_clickup_id(body.deals_list_id),
    }
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _normalized_clickup_crm_settings(row.clickup_crm_settings)


async def _gmail_status(session: DBSession) -> GmailSettingsRead:
    import redis
    from app.tasks.email_sync import REDIS_KEY_LAST_SYNC

    row = await _get_or_create(session)
    last_sync_epoch = None
    try:
        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        last = r.get(REDIS_KEY_LAST_SYNC)
        last_sync_epoch = int(last) if last else None
        r.close()
    except Exception:
        last_sync_epoch = None

    return GmailSettingsRead(
        configured=bool(row.gmail_shared_inbox and row.gmail_token_data),
        inbox=row.gmail_shared_inbox,
        connected_email=row.gmail_connected_email,
        connected_at=row.gmail_connected_at,
        interval_seconds=settings.EMAIL_SYNC_INTERVAL_SECONDS,
        last_sync_epoch=last_sync_epoch,
        last_error=row.gmail_last_error,
    )


def _has_scope(token_data: dict | None, scope: str) -> bool:
    if not isinstance(token_data, dict):
        return False
    return scope in set(token_data.get("scopes") or [])


async def _report_sender_status(session: DBSession) -> ReportSenderSettingsRead:
    row = await _get_or_create(session)
    return ReportSenderSettingsRead(
        configured=bool(
            row.report_sender_email
            and row.report_sender_connected_email
            and row.report_sender_token_data
            and _has_scope(row.report_sender_token_data, GMAIL_SEND_SCOPE)
        ),
        sender_email=row.report_sender_email,
        connected_email=row.report_sender_connected_email,
        connected_at=row.report_sender_connected_at,
        last_error=row.report_sender_last_error,
        has_send_scope=_has_scope(row.report_sender_token_data, GMAIL_SEND_SCOPE),
    )


@router.get("/outreach", response_model=OutreachSettingsRead)
async def get_outreach_settings(session: DBSession, _user: CurrentUser):
    """Return the global outreach sequence timing defaults."""
    row = await _get_or_create(session)
    delays = row.outreach_step_delays or _DEFAULTS
    content = _normalized_outreach_content(row.outreach_content_settings, step_count=len(delays))
    steps = [
        OutreachTimingStep(
            step_number=index,
            day=delay,
            channel=(content.step_templates[index - 1].channel if index - 1 < len(content.step_templates) else "email"),
        )
        for index, delay in enumerate(delays, start=1)
    ]
    return OutreachSettingsRead(step_delays=delays, steps_count=len(delays), steps=steps)


@router.patch("/outreach", response_model=OutreachSettingsRead)
async def update_outreach_settings(body: OutreachSettingsUpdate, session: DBSession, _user: CurrentUser):
    """
    Update global outreach step delays.
    Accepts a list of integers — one per step, in days from sequence start.
    E.g. [0, 4, 10] → send on Day 0, Day 4, Day 10.
    """
    raw_steps = body.steps or [
        OutreachTimingStep(step_number=index, day=delay, channel="email")
        for index, delay in enumerate(body.step_delays, start=1)
    ]

    if len(raw_steps) < 1 or len(raw_steps) > 20:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="steps must have 1–20 entries")

    normalized_steps: list[OutreachTimingStep] = []
    last_day = -1
    for index, step in enumerate(sorted(raw_steps, key=lambda item: item.step_number), start=1):
        channel = step.channel.strip().lower() if step.channel else "email"
        if channel not in {"email", "call", "linkedin"}:
            raise HTTPException(status_code=422, detail="step channel must be email, call, or linkedin")
        if step.day < 0:
            raise HTTPException(status_code=422, detail="step day must be 0 or greater")
        if step.day < last_day:
            raise HTTPException(status_code=422, detail="step days must be in ascending order")
        normalized_steps.append(OutreachTimingStep(step_number=index, day=step.day, channel=channel))
        last_day = step.day

    row = await _get_or_create(session)
    row.outreach_step_delays = [step.day for step in normalized_steps]
    content = _normalized_outreach_content(row.outreach_content_settings, step_count=len(normalized_steps))
    updated_templates = []
    for index, step in enumerate(normalized_steps, start=1):
        template = content.step_templates[index - 1]
        updated_templates.append(
            {
                "step_number": index,
                "channel": step.channel,
                "label": template.label,
                "goal": template.goal,
                "subject_hint": template.subject_hint,
                "body_template": template.body_template,
                "prompt_hint": template.prompt_hint,
            }
        )
    row.outreach_content_settings = {
        **(row.outreach_content_settings or {}),
        "step_templates": updated_templates,
    }
    session.add(row)
    await session.commit()
    await session.refresh(row)

    # Propagate the new cadence to every prospect that hasn't started a
    # sequence yet — their progress tracker renders from sequence_plan.steps,
    # so without this refresh they'd still show the old channel labels.
    # Contacts already in flight (launched / replied / meeting_booked / etc.)
    # are protected by the guard inside refresh_contact_sequence_plan.
    from app.models.contact import Contact
    from app.models.company import Company
    from app.services.account_sourcing import (
        _workspace_step_schedule,
        refresh_contact_sequence_plan,
    )

    ws_schedule = _workspace_step_schedule(
        row.outreach_step_delays, row.outreach_content_settings
    )
    refreshed_count = 0
    if ws_schedule:
        stmt = sm_select(Contact).where(
            Contact.instantly_campaign_id.is_(None),
            (Contact.sequence_status.is_(None) | Contact.sequence_status.in_(["ready", "research_needed", ""])),
        )
        contacts_to_refresh = (await session.execute(stmt)).scalars().all()
        company_cache: dict = {}
        for contact in contacts_to_refresh:
            if not contact.company_id:
                continue
            company = company_cache.get(contact.company_id)
            if company is None:
                company = await session.get(Company, contact.company_id)
                company_cache[contact.company_id] = company
            if not company:
                continue
            refresh_contact_sequence_plan(contact, company, workspace_schedule=ws_schedule)
            session.add(contact)
            refreshed_count += 1
        if refreshed_count:
            await session.commit()

    delays = row.outreach_step_delays
    return OutreachSettingsRead(step_delays=delays, steps_count=len(delays), steps=normalized_steps)


@router.get("/outreach-content", response_model=OutreachContentSettingsRead)
async def get_outreach_content_settings(session: DBSession, _user: CurrentUser):
    row = await _get_or_create(session)
    step_count = len(row.outreach_step_delays or _DEFAULTS)
    return _normalized_outreach_content(row.outreach_content_settings, step_count=step_count)


@router.patch("/outreach-content", response_model=OutreachContentSettingsRead)
async def update_outreach_content_settings(
    body: OutreachContentSettingsUpdate,
    session: DBSession,
    _user: CurrentUser,
):
    if len(body.step_templates) < 1 or len(body.step_templates) > 20:
        raise HTTPException(status_code=422, detail="step_templates must have 1–20 entries")

    seen_numbers: set[int] = set()
    normalized_steps = []
    for idx, step in enumerate(sorted(body.step_templates, key=lambda item: item.step_number), start=1):
        if step.step_number < 1 or step.step_number > 10:
            raise HTTPException(status_code=422, detail="step_number must be between 1 and 10")
        if step.step_number in seen_numbers:
            raise HTTPException(status_code=422, detail="step_numbers must be unique")
        seen_numbers.add(step.step_number)
        channel = (step.channel or "email").strip().lower()
        if channel not in {"email", "call", "linkedin"}:
            raise HTTPException(status_code=422, detail="step channel must be email, call, or linkedin")
        normalized_steps.append(
            {
                "step_number": step.step_number,
                "channel": channel,
                "label": step.label.strip() or f"Step {idx}",
                "goal": step.goal.strip(),
                "subject_hint": (step.subject_hint or "").strip() or None,
                "body_template": (step.body_template or "").strip() or None,
                "prompt_hint": (step.prompt_hint or "").strip() or None,
            }
        )

    row = await _get_or_create(session)
    row.outreach_content_settings = {
        "general_prompt": body.general_prompt.strip(),
        "linkedin_prompt": body.linkedin_prompt.strip(),
        "step_templates": normalized_steps,
    }
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _normalized_outreach_content(row.outreach_content_settings)


@router.get("/deal-funnel", response_model=DealFunnelSettingsRead)
async def get_deal_funnel_settings(session: DBSession, _user: CurrentUser):
    row = await _get_or_create(session)
    return _normalized_deal_funnel_config(row.deal_funnel_config)


@router.patch("/deal-funnel", response_model=DealFunnelSettingsRead)
async def update_deal_funnel_settings(body: DealFunnelSettingsUpdate, session: DBSession, _admin: AdminUser):
    allowed_deal_stage_ids = await get_configured_deal_stage_ids(session)
    _validate_funnel_stage_ids(body.tofu, allowed_deal_stage_ids, "deal")
    _validate_funnel_stage_ids(body.mofu, allowed_deal_stage_ids, "deal")
    _validate_funnel_stage_ids(body.bofu, allowed_deal_stage_ids, "deal")

    row = await _get_or_create(session)
    row.deal_funnel_config = {
        "tofu": body.tofu,
        "mofu": body.mofu,
        "bofu": body.bofu,
    }
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _normalized_deal_funnel_config(row.deal_funnel_config)


@router.get("/pipeline-summary", response_model=PipelineSummarySettingsRead)
async def get_pipeline_summary_settings(session: DBSession, _user: CurrentUser):
    row = await _get_or_create(session)
    return _normalized_pipeline_summary_settings(row)


@router.patch("/pipeline-summary", response_model=PipelineSummarySettingsRead)
async def update_pipeline_summary_settings(
    body: PipelineSummarySettingsUpdate,
    session: DBSession,
    _admin: AdminUser,
):
    allowed_deal_stage_ids = await get_configured_deal_stage_ids(session)
    for bucket in (body.deal.active, body.deal.inactive, body.deal.tofu, body.deal.mofu, body.deal.bofu):
        _validate_funnel_stage_ids(bucket, allowed_deal_stage_ids, "deal")
    for bucket in (body.prospect.active, body.prospect.inactive, body.prospect.tofu, body.prospect.mofu, body.prospect.bofu):
        _validate_funnel_stage_ids(bucket, _PROSPECT_STAGES, "prospect")
    _validate_visible_cards(body.deal.visible_cards, "deal")
    _validate_visible_cards(body.prospect.visible_cards, "prospect")

    row = await _get_or_create(session)
    row.deal_funnel_config = {
        "active": body.deal.active,
        "inactive": body.deal.inactive,
        "tofu": body.deal.tofu,
        "mofu": body.deal.mofu,
        "bofu": body.deal.bofu,
        "visible_cards": body.deal.visible_cards,
    }
    row.prospect_funnel_config = {
        "active": body.prospect.active,
        "inactive": body.prospect.inactive,
        "tofu": body.prospect.tofu,
        "mofu": body.prospect.mofu,
        "bofu": body.prospect.bofu,
        "visible_cards": body.prospect.visible_cards,
    }
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _normalized_pipeline_summary_settings(row)


@router.get("/role-permissions", response_model=RolePermissionsRead)
async def get_role_permissions(session: DBSession, _user: CurrentUser):
    row = await _get_or_create(session)
    return _normalized_role_permissions(row.role_permissions)


@router.patch("/role-permissions", response_model=RolePermissionsRead)
async def update_role_permissions(
    body: RolePermissionsUpdate,
    session: DBSession,
    _admin: AdminUser,
):
    row = await _get_or_create(session)
    row.role_permissions = body.model_dump()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _normalized_role_permissions(row.role_permissions)


@router.get("/pre-meeting-automation", response_model=PreMeetingAutomationSettingsRead)
async def get_pre_meeting_automation_settings(session: DBSession, _user: CurrentUser):
    row = await _get_or_create(session)
    return _normalized_pre_meeting_settings(row.pre_meeting_automation_settings)


@router.patch("/pre-meeting-automation", response_model=PreMeetingAutomationSettingsRead)
async def update_pre_meeting_automation_settings(
    body: PreMeetingAutomationSettingsUpdate,
    session: DBSession,
    _admin: AdminUser,
):
    if body.send_hours_before < 1 or body.send_hours_before > 168:
        raise HTTPException(status_code=422, detail="send_hours_before must be between 1 and 168")
    if body.generate_hours_before < body.send_hours_before or body.generate_hours_before > 168:
        raise HTTPException(status_code=422, detail="generate_hours_before must be between send_hours_before and 168")

    row = await _get_or_create(session)
    row.pre_meeting_automation_settings = body.model_dump()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _normalized_pre_meeting_settings(row.pre_meeting_automation_settings)


@router.post("/pre-meeting-automation/run-now", response_model=dict)
async def run_pre_meeting_automation_now(session: DBSession, _admin: AdminUser):
    _ = session
    return await run_due_pre_meeting_intel_once()


# ── Zippy system prompt ──────────────────────────────────────────────────────
#
# Readable by all authenticated users (so everyone can see what Zippy knows).
# Writable only by admins.

@router.get("/zippy-system-prompt", response_model=ZippySystemPromptRead)
async def get_zippy_system_prompt(session: DBSession, _user: CurrentUser):
    from app.services.zippy_agent import SYSTEM_PROMPT as DEFAULT_ZIPPY_PROMPT

    row = await _get_or_create(session)
    override = (row.zippy_system_prompt or "").strip()
    if override:
        return ZippySystemPromptRead(prompt=override, is_default=False)
    return ZippySystemPromptRead(prompt=DEFAULT_ZIPPY_PROMPT, is_default=True)


@router.patch("/zippy-system-prompt", response_model=ZippySystemPromptRead)
async def update_zippy_system_prompt(
    body: ZippySystemPromptUpdate,
    session: DBSession,
    _admin: AdminUser,
):
    from app.services.zippy_agent import SYSTEM_PROMPT as DEFAULT_ZIPPY_PROMPT

    row = await _get_or_create(session)
    cleaned = (body.prompt or "").strip()
    # Empty string resets to the built-in default so admins can back out of a
    # bad edit without needing a redeploy.
    row.zippy_system_prompt = cleaned or None
    session.add(row)
    await session.commit()
    await session.refresh(row)
    if row.zippy_system_prompt:
        return ZippySystemPromptRead(prompt=row.zippy_system_prompt, is_default=False)
    return ZippySystemPromptRead(prompt=DEFAULT_ZIPPY_PROMPT, is_default=True)


SYNC_DEFAULTS = {
    "tldv_sync_enabled": True,
    "tldv_sync_interval_minutes": 5,
    "tldv_page_size": 10,
    "tldv_max_pages": 2,
    "tldv_last_synced_at": None,
    "email_sync_interval_seconds": 180,
    "deal_health_hour": 2,
}


def _normalized_sync_schedule(value: dict | None) -> SyncScheduleSettingsRead:
    merged = {**SYNC_DEFAULTS, **(value or {})}
    return SyncScheduleSettingsRead(**merged)


def _sales_report_settings_from_sync(value: dict | None) -> SalesReportSettingsRead:
    raw = value.get("sales_report") if isinstance(value, dict) else None
    return SalesReportSettingsRead(**normalize_sales_report_settings(raw if isinstance(raw, dict) else None))


@router.get("/sync-schedule", response_model=SyncScheduleSettingsRead)
async def get_sync_schedule(session: DBSession, _user: CurrentUser):
    row = await _get_or_create(session)
    return _normalized_sync_schedule(row.sync_schedule_settings)


@router.patch("/sync-schedule", response_model=SyncScheduleSettingsRead)
async def update_sync_schedule(body: SyncScheduleSettingsUpdate, session: DBSession, _admin: AdminUser):
    row = await _get_or_create(session)
    current = {**SYNC_DEFAULTS, **(row.sync_schedule_settings or {})}
    updates = body.model_dump(exclude_unset=True)
    current.update(updates)
    # Clamp values
    current["tldv_sync_interval_minutes"] = max(1, min(60, current["tldv_sync_interval_minutes"]))
    current["tldv_page_size"] = max(5, min(50, current["tldv_page_size"]))
    current["tldv_max_pages"] = max(1, min(10, current["tldv_max_pages"]))
    current["email_sync_interval_seconds"] = max(60, min(3600, current["email_sync_interval_seconds"]))
    current["deal_health_hour"] = max(0, min(23, current["deal_health_hour"]))
    row.sync_schedule_settings = current
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _normalized_sync_schedule(row.sync_schedule_settings)


@router.get("/sales-report", response_model=SalesReportSettingsRead)
async def get_sales_report_settings(session: DBSession, _user: CurrentUser):
    row = await _get_or_create(session)
    return _sales_report_settings_from_sync(row.sync_schedule_settings)


@router.patch("/sales-report", response_model=SalesReportSettingsRead)
async def update_sales_report_settings(body: SalesReportSettingsUpdate, session: DBSession, _admin: AdminUser):
    row = await _get_or_create(session)
    sync_settings = dict(row.sync_schedule_settings or {})
    current = normalize_sales_report_settings(sync_settings.get("sales_report") if isinstance(sync_settings.get("sales_report"), dict) else None)
    updates = body.model_dump(exclude_unset=True)
    # Preserve send history unless explicitly changing the schedule identity.
    schedule_keys = {"send_timezone", "send_hour", "send_minute", "send_days", "weekly_report_day"}
    if schedule_keys.intersection(updates):
        current["last_scheduled_send_key"] = None
        current["last_scheduled_send_at"] = None
    current.update(updates)
    normalized = normalize_sales_report_settings(current)
    sync_settings["sales_report"] = normalized
    row.sync_schedule_settings = sync_settings
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return SalesReportSettingsRead(**normalized)


@router.post("/sync-schedule/tldv-now", response_model=dict)
async def trigger_tldv_sync_now(_admin: AdminUser):
    from app.tasks.tldv_sync import sync_tldv_meetings
    sync_tldv_meetings.delay()
    return {"status": "queued"}


@router.post("/sync-schedule/tldv-stop", response_model=dict)
async def stop_tldv_sync(session: DBSession, _admin: AdminUser):
    row = await _get_or_create(session)
    current = {**SYNC_DEFAULTS, **(row.sync_schedule_settings or {})}
    current["tldv_sync_enabled"] = False
    row.sync_schedule_settings = current
    session.add(row)
    await session.commit()
    return {"status": "stopped", "tldv_sync_enabled": False}


@router.get("/email-sync", response_model=GmailSettingsRead)
async def get_gmail_settings(session: DBSession, _user: CurrentUser):
    return await _gmail_status(session)


@router.patch("/email-sync", response_model=GmailSettingsRead)
async def update_gmail_settings(body: GmailSettingsUpdate, session: DBSession, _admin: AdminUser):
    row = await _get_or_create(session)
    row.gmail_shared_inbox = body.inbox.strip().lower()
    session.add(row)
    await session.commit()
    return await _gmail_status(session)


@router.get("/email-sync/google/connect-url", response_model=GmailConnectUrlRead)
async def get_gmail_connect_url(admin: AdminUser, session: DBSession):
    if not settings.gmail_client_id or not settings.gmail_client_secret:
        raise UnauthorizedError("Gmail OAuth is not configured. Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET.")

    row = await _get_or_create(session)
    if not row.gmail_shared_inbox:
        raise ForbiddenError("Set the shared inbox address before connecting Gmail.")

    state = create_gmail_oauth_state(str(admin.id))
    return GmailConnectUrlRead(url=build_gmail_connect_url(state))


@router.get("/report-sender", response_model=ReportSenderSettingsRead)
async def get_report_sender_settings(session: DBSession, _user: CurrentUser):
    return await _report_sender_status(session)


@router.patch("/report-sender", response_model=ReportSenderSettingsRead)
async def update_report_sender_settings(body: ReportSenderSettingsUpdate, session: DBSession, _admin: AdminUser):
    row = await _get_or_create(session)
    row.report_sender_email = body.sender_email.strip().lower()
    session.add(row)
    await session.commit()
    return await _report_sender_status(session)


@router.get("/report-sender/google/connect-url", response_model=GmailConnectUrlRead)
async def get_report_sender_connect_url(admin: AdminUser, session: DBSession):
    if not settings.gmail_client_id or not settings.gmail_client_secret:
        raise UnauthorizedError("Gmail OAuth is not configured. Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET.")

    row = await _get_or_create(session)
    if not row.report_sender_email:
        raise ForbiddenError("Set the report sender email before connecting Gmail.")

    state = create_gmail_oauth_state(str(admin.id), flow="report_sender")
    return GmailConnectUrlRead(url=build_gmail_connect_url(state, scopes=f"{GOOGLE_EMAIL_SCOPE} {GMAIL_SEND_SCOPE}"))


@router.get("/email-sync/google/callback")
async def gmail_callback(
    session: DBSession,
    code: str = Query(...),
    state: str = Query(...),
):
    payload = decode_gmail_oauth_state(state)
    if not payload:
        return RedirectResponse(f"{settings.FRONTEND_URL}/settings?gmail=error")
    flow = str(payload.get("flow") or "shared").lower()
    failure_query = "report_sender=error" if flow == "report_sender" else "gmail=error"

    try:
        gmail_info = await exchange_gmail_code(code)
    except Exception:
        if flow == "personal":
            user_id = payload.get("sub")
            if user_id:
                result = await session.execute(
                    sm_select(UserEmailConnection).where(
                        UserEmailConnection.user_id == UUID(user_id)
                    )
                )
                connection = result.scalar_one_or_none()
                if connection:
                    connection.last_error = "Failed to complete Gmail OAuth exchange"
                    connection.updated_at = datetime.utcnow()
                    session.add(connection)
                    await session.commit()
        else:
            row = await _get_or_create(session)
            if flow == "report_sender":
                row.report_sender_last_error = "Failed to complete Gmail OAuth exchange"
            else:
                row.gmail_last_error = "Failed to complete Gmail OAuth exchange"
            session.add(row)
            await session.commit()
        return RedirectResponse(f"{settings.FRONTEND_URL}/settings?{failure_query}")

    if flow == "personal":
        user_id = payload.get("sub")
        if not user_id:
            return RedirectResponse(f"{settings.FRONTEND_URL}/settings?gmail=error")

        result = await session.execute(
            sm_select(UserEmailConnection).where(
                UserEmailConnection.user_id == UUID(user_id)
            )
        )
        connection = result.scalar_one_or_none()
        if connection:
            connection.email_address = gmail_info["email_address"]
            connection.token_data = gmail_info["token_data"]
            connection.is_active = True
            connection.backfill_completed = False
            connection.last_sync_epoch = None
            connection.last_error = None
            connection.connected_at = datetime.utcnow()
            connection.updated_at = datetime.utcnow()
        else:
            connection = UserEmailConnection(
                user_id=UUID(user_id),
                email_address=gmail_info["email_address"],
                token_data=gmail_info["token_data"],
                connected_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )

        session.add(connection)
        await session.commit()
        await session.refresh(connection)

        from app.tasks.personal_email_sync import sync_personal_inbox

        sync_personal_inbox.delay(str(connection.id))
        query = urlencode({"gmail_connected": "1", "email": gmail_info["email_address"]})
        return RedirectResponse(f"{settings.FRONTEND_URL}/settings?{query}")

    if flow == "report_sender":
        row = await _get_or_create(session)
        row.report_sender_connected_email = gmail_info["email_address"]
        row.report_sender_token_data = gmail_info["token_data"]
        row.report_sender_connected_at = datetime.utcnow()
        if not row.report_sender_email:
            row.report_sender_email = gmail_info["email_address"]
        if row.report_sender_email.lower() != str(gmail_info["email_address"] or "").lower():
            row.report_sender_last_error = (
                f"Connected {gmail_info['email_address']}, but configured sender is {row.report_sender_email}. "
                "Reconnect with the configured mailbox or update the sender email."
            )
        elif not _has_scope(row.report_sender_token_data, GMAIL_SEND_SCOPE):
            row.report_sender_last_error = "Connected mailbox is missing Gmail send permission. Reconnect report sender."
        else:
            row.report_sender_last_error = None
        session.add(row)
        await session.commit()
        query = urlencode({"report_sender": "connected", "email": gmail_info["email_address"]})
        return RedirectResponse(f"{settings.FRONTEND_URL}/settings?{query}")

    row = await _get_or_create(session)
    row.gmail_connected_email = gmail_info["email_address"]
    row.gmail_token_data = gmail_info["token_data"]
    row.gmail_connected_at = datetime.utcnow()
    row.gmail_last_error = None
    session.add(row)
    await session.commit()
    return RedirectResponse(f"{settings.FRONTEND_URL}/settings?gmail=connected")


@router.delete("/email-sync/google")
async def disconnect_gmail(session: DBSession, _admin: AdminUser):
    row = await _get_or_create(session)
    row.gmail_connected_email = None
    row.gmail_connected_at = None
    row.gmail_token_data = None
    row.gmail_last_error = None
    session.add(row)
    await session.commit()
    return {"status": "disconnected"}


@router.delete("/report-sender/google")
async def disconnect_report_sender(session: DBSession, _admin: AdminUser):
    row = await _get_or_create(session)
    row.report_sender_connected_email = None
    row.report_sender_connected_at = None
    row.report_sender_token_data = None
    row.report_sender_last_error = None
    session.add(row)
    await session.commit()
    return {"status": "disconnected"}
