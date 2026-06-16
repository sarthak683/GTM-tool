import json
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import Text as sa_Text, and_, func, or_, select

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.dependencies import CurrentUser, DBSession, Pagination
from app.models.company import Company
from app.models.deal import Deal
from app.models.meeting import Meeting, MeetingCreate, MeetingRead, MeetingUpdate
from app.models.user import User
from app.repositories.meeting import MeetingRepository
from app.services.deal_stages import get_configured_deal_stages
from app.services.internal_domains import get_internal_domains, is_internal_only
from app.services.meeting_automation import _collect_recipient_ids
from app.services.permissions import require_workspace_permission
from app.schemas.common import PaginatedResponse

router = APIRouter(prefix="/meetings", tags=["meetings"])


class MeetingPrepMonitor(BaseModel):
    window_hours: int
    upcoming_count: int
    no_company_count: int
    no_deal_count: int
    no_intel_count: int
    no_recipient_count: int
    unlinked: list[MeetingRead]


# Recurring operational-cadence titles (daily standups, account daily/weekly
# syncs, 1:1s, catch-ups, all-hands). The prep view uses this to keep these off
# the pre-meeting list — they are not buyer-facing sales meetings and were ~86%
# of upcoming meetings in prod. Mirrors the cadence terms in
# meeting_automation._NON_SALES_TITLE_RE (the pre-meeting intel gate), so the
# page matches what intel actually generates briefs for. Postgres regex (~*).
_RECURRING_CADENCE_TITLE_SQL = (
    r"(cadence|huddle|scrum|stand[ -]?up|retro|sync[ -]?up|catch[ -]?up|"
    r"check[ -]?in|daily (sync|connect|cadence|update|standup|catch up|check in)|"
    r"weekly (sync|connect|cadence|update)|bi[ -]?weekly (sync|cadence)|"
    r"monthly (sync|cadence)|1:1|one[ -]on[ -]one|all[ -]hands|"
    r"team (sync|standup|huddle)|"
    # recruiting / interviews (mirrors meeting_automation._NON_SALES_TITLE_RE)
    r"interview|hiring|candidate|screening|recruit|sdr\M)"
)

# A title carrying a clear sales-intent keyword is always a real meeting, even
# if it also contains a cadence word (e.g. "Acme — Introduction (Catch up with
# Igor)" or "Acme: Demo syncup"). This override prevents the cadence filter from
# hiding genuine intros/demos/POCs. Kept in sync with meeting_automation.
_SALES_INTENT_TITLE_SQL = (
    r"(introduction|intro\M|discovery|demo|poc|pov|kick[ -]?off|kickoff|"
    r"proposal|pricing|negotiation|evaluation|eval\M|onboarding|deep[ -]?dive|"
    r"walkthrough|presentation|pilot|business case)"
)


@router.get("/", response_model=PaginatedResponse[MeetingRead])
async def list_meetings(
    session: DBSession,
    current_user: CurrentUser,
    pagination: Pagination,
    company_id: Optional[UUID] = Query(default=None),
    deal_id: Optional[UUID] = Query(default=None),
    status: list[str] = Query(default=[]),
    temporal_status: list[str] = Query(default=[], description="'upcoming' and 'overdue' are virtual filters for scheduled meetings."),
    meeting_type: list[str] = Query(default=[]),
    assignee_id: list[UUID] = Query(default=[]),
    assignee_unassigned: bool = Query(default=False, description="If true, include meetings with no assignee (deal AE and meeting owner both null). OR'd with assignee_id."),
    link_state: list[str] = Query(default=[]),
    has_intel: Optional[bool] = Query(default=None),
    order: str = Query(default="desc"),
    q: Optional[str] = Query(default=None, description="Free-text search across title, linked company name, and attendee name/email."),
    synced_after: Optional[datetime] = Query(default=None, description="Only return meetings whose synced_at is on/after this ISO timestamp. Used by the 'Recently synced' shortcut."),
    include_internal: bool = Query(default=False, description="If false (default), hide meetings where every attendee is from an internal domain. Set true to include them."),
    internal_scope: str = Query(default="exclude", description="Internal meeting visibility: exclude, include, or only."),
    exclude_closed_pipeline: bool = Query(default=False, description="If true (the upcoming/prep view), hide clutter that isn't a buyer-facing sales meeting: meetings on closed/customer accounts AND recurring operational cadences (daily standups, account daily/weekly syncs, 1:1s, catch-ups). Mirrors the pre-meeting intel eligibility gate."),
):
    stmt = select(Meeting)
    count_stmt = select(func.count()).select_from(Meeting)
    joined_deal = False
    joined_company = False

    def ensure_deal_join() -> None:
        nonlocal stmt, count_stmt, joined_deal
        if joined_deal:
            return
        stmt = stmt.outerjoin(Deal, Meeting.deal_id == Deal.id)
        count_stmt = count_stmt.outerjoin(Deal, Meeting.deal_id == Deal.id)
        joined_deal = True

    def ensure_company_join() -> None:
        nonlocal stmt, count_stmt, joined_company
        if joined_company:
            return
        stmt = stmt.outerjoin(Company, Meeting.company_id == Company.id)
        count_stmt = count_stmt.outerjoin(Company, Meeting.company_id == Company.id)
        joined_company = True

    # Visibility: admins see every meeting in the workspace; everyone else sees
    # only their own — meetings they own or synced, or whose linked deal/company
    # is assigned to them. Enforced server-side so it can't be bypassed by
    # clearing the assignee filter in the UI or hitting the API directly. (The
    # `assignee_id` param still lets an admin slice by rep.)
    if current_user.role != "admin":
        ensure_deal_join()
        ensure_company_join()
        own_clause = or_(
            Meeting.owner_user_id == current_user.id,
            Meeting.synced_by_user_id == current_user.id,
            Deal.assigned_to_id == current_user.id,
            Company.assigned_to_id == current_user.id,
        )
        stmt = stmt.where(own_clause)
        count_stmt = count_stmt.where(own_clause)

    if company_id:
        stmt = stmt.where(Meeting.company_id == company_id)
        count_stmt = count_stmt.where(Meeting.company_id == company_id)
    if deal_id:
        stmt = stmt.where(Meeting.deal_id == deal_id)
        count_stmt = count_stmt.where(Meeting.deal_id == deal_id)
    if status:
        stmt = stmt.where(Meeting.status.in_(status))
        count_stmt = count_stmt.where(Meeting.status.in_(status))
    temporal_status_set = {value.strip().lower() for value in temporal_status if value}
    if temporal_status_set:
        now = datetime.utcnow()
        scheduled_time_clauses = []
        if "upcoming" in temporal_status_set:
            scheduled_time_clauses.append(or_(Meeting.scheduled_at.is_(None), Meeting.scheduled_at >= now))
        if "overdue" in temporal_status_set:
            scheduled_time_clauses.append(Meeting.scheduled_at < now)

        clauses = []
        if scheduled_time_clauses:
            clauses.append(
                (Meeting.status == "scheduled")
                & or_(*scheduled_time_clauses)
            )
        passthrough_statuses = [value for value in status if value != "scheduled"]
        if passthrough_statuses:
            clauses.append(Meeting.status.in_(passthrough_statuses))
        if clauses:
            temporal_clause = or_(*clauses)
            stmt = stmt.where(temporal_clause)
            count_stmt = count_stmt.where(temporal_clause)
    if meeting_type:
        stmt = stmt.where(Meeting.meeting_type.in_(meeting_type))
        count_stmt = count_stmt.where(Meeting.meeting_type.in_(meeting_type))
    if assignee_id or assignee_unassigned:
        ensure_deal_join()
        assignee_terms = []
        if assignee_id:
            assignee_terms.append(Deal.assigned_to_id.in_(assignee_id))
            assignee_terms.append(Meeting.owner_user_id.in_(assignee_id))
        if assignee_unassigned:
            # No assignee: neither the linked deal's AE nor the meeting owner is set.
            assignee_terms.append(
                and_(Deal.assigned_to_id.is_(None), Meeting.owner_user_id.is_(None))
            )
        assignee_clause = or_(*assignee_terms)
        stmt = stmt.where(assignee_clause)
        count_stmt = count_stmt.where(assignee_clause)
    link_state_set = {value.strip().lower() for value in link_state if value}
    if link_state_set == {"needs_review"}:
        review_clause = or_(Meeting.company_id.is_(None), Meeting.deal_id.is_(None))
        stmt = stmt.where(review_clause)
        count_stmt = count_stmt.where(review_clause)
    elif link_state_set == {"linked"}:
        stmt = stmt.where(Meeting.company_id.is_not(None), Meeting.deal_id.is_not(None))
        count_stmt = count_stmt.where(Meeting.company_id.is_not(None), Meeting.deal_id.is_not(None))

    # Open-pipeline filter (prep view): hide meetings on closed/customer accounts
    # so e.g. a closed_won customer's daily syncs don't clutter the prep list.
    # Mirrors _is_open_pipeline_meeting: keep deal-linked-open, company-only with
    # no deals or an open deal, and CRM-unlinked; drop closed deals + companies
    # whose deals are all closed.
    if exclude_closed_pipeline:
        ensure_deal_join()
        stage_settings = await get_configured_deal_stages(session)
        closed_ids = [str(s["id"]).strip().lower() for s in stage_settings if s.get("group") == "closed"]
        if closed_ids:
            companies_with_any_deal = select(Deal.company_id).where(Deal.company_id.is_not(None))
            companies_with_open_deal = select(Deal.company_id).where(
                Deal.company_id.is_not(None), Deal.stage.notin_(closed_ids)
            )
            open_pipeline_clause = or_(
                # Deal-linked and the deal is still open.
                and_(Meeting.deal_id.is_not(None), Deal.stage.notin_(closed_ids)),
                # Company-only: keep if the account has no deals or at least one open deal.
                and_(
                    Meeting.deal_id.is_(None),
                    Meeting.company_id.is_not(None),
                    or_(
                        Meeting.company_id.in_(companies_with_open_deal),
                        Meeting.company_id.notin_(companies_with_any_deal),
                    ),
                ),
                # No CRM link at all — leave as-is.
                and_(Meeting.deal_id.is_(None), Meeting.company_id.is_(None)),
            )
            stmt = stmt.where(open_pipeline_clause)
            count_stmt = count_stmt.where(open_pipeline_clause)

        # Prep view also drops recurring operational cadences (daily standups,
        # account daily/weekly syncs, 1:1s, catch-ups). These are not sales prep
        # moments and were flooding the list (~86% of upcoming in prod). NULL
        # titles are kept (can't classify → don't hide a possibly-real meeting).
        # Keep a meeting if: no title, OR it isn't a cadence, OR it has clear
        # sales intent (intro/demo/POC/kickoff) even if it also says "catch up".
        cadence_clause = or_(
            Meeting.title.is_(None),
            Meeting.title.op("!~*")(_RECURRING_CADENCE_TITLE_SQL),
            Meeting.title.op("~*")(_SALES_INTENT_TITLE_SQL),
        )
        stmt = stmt.where(cadence_clause)
        count_stmt = count_stmt.where(cadence_clause)

    if has_intel is True:
        stmt = stmt.where(Meeting.research_data.is_not(None))
        count_stmt = count_stmt.where(Meeting.research_data.is_not(None))
    elif has_intel is False:
        stmt = stmt.where(Meeting.research_data.is_(None))
        count_stmt = count_stmt.where(Meeting.research_data.is_(None))

    internal_scope = (internal_scope or "exclude").strip().lower()
    if internal_scope not in {"exclude", "include", "only"}:
        raise HTTPException(status_code=422, detail="internal_scope must be one of: exclude, include, only")

    # Legacy support: older clients used include_internal=true to mean "show
    # both internal and external". New clients use internal_scope=only for the
    # explicit Internal filter so the UI does not mix customer calls into an
    # internal-only view.
    if include_internal and internal_scope == "exclude":
        internal_scope = "include"

    if internal_scope == "exclude":
        stmt = stmt.where(Meeting.is_internal.is_(False))
        count_stmt = count_stmt.where(Meeting.is_internal.is_(False))
    elif internal_scope == "only":
        stmt = stmt.where(Meeting.is_internal.is_(True))
        count_stmt = count_stmt.where(Meeting.is_internal.is_(True))

    if synced_after is not None:
        # Strip timezone so the DB comparison works against a naive column.
        naive_cutoff = synced_after.replace(tzinfo=None) if synced_after.tzinfo else synced_after
        stmt = stmt.where(Meeting.synced_at.is_not(None), Meeting.synced_at >= naive_cutoff)
        count_stmt = count_stmt.where(Meeting.synced_at.is_not(None), Meeting.synced_at >= naive_cutoff)

    # Free-text search across title + linked company name + attendee JSON.
    # Using ILIKE is enough for the small meetings table here (low-hundreds
    # of rows); no full-text index needed. The JSONB cast-to-text search
    # matches attendee names/emails stored inside `attendees` without having
    # to normalize the blob — scrappy but fine.
    q_trimmed = (q or "").strip()
    if q_trimmed:
        ensure_company_join()
        pattern = f"%{q_trimmed}%"
        q_clause = or_(
            Meeting.title.ilike(pattern),
            Company.name.ilike(pattern),
            func.cast(Meeting.attendees, sa_Text).ilike(pattern),
        )
        stmt = stmt.where(q_clause)
        count_stmt = count_stmt.where(q_clause)

    order_by = (
        Meeting.scheduled_at.asc().nulls_last()
        if order == "asc"
        else Meeting.scheduled_at.desc().nulls_last()
    )
    stmt = stmt.order_by(order_by).offset(pagination.skip).limit(pagination.limit)

    total = (await session.execute(count_stmt)).scalar_one()
    items = list((await session.execute(stmt)).scalars().all())
    return PaginatedResponse.build(items, total, pagination.skip, pagination.limit)


@router.get("/prep-monitor", response_model=MeetingPrepMonitor)
async def get_meeting_prep_monitor(
    session: DBSession,
    current_user: CurrentUser,
    window_hours: int = Query(default=24, ge=1, le=168),
):
    now = datetime.utcnow()
    window_end = now + timedelta(hours=window_hours)
    meetings = list((
        await session.execute(
            select(Meeting)
            .where(
                Meeting.status == "scheduled",
                Meeting.is_internal.is_(False),
                Meeting.scheduled_at.is_not(None),
                Meeting.scheduled_at > now,
                Meeting.scheduled_at <= window_end,
            )
            .order_by(Meeting.scheduled_at.asc())
        )
    ).scalars().all())

    no_recipient_count = 0

    for meeting in meetings:
        if not await _collect_recipient_ids(session, meeting):
            no_recipient_count += 1

    unlinked = [meeting for meeting in meetings if not meeting.company_id or not meeting.deal_id]
    return MeetingPrepMonitor(
        window_hours=window_hours,
        upcoming_count=len(meetings),
        no_company_count=sum(1 for meeting in meetings if not meeting.company_id),
        no_deal_count=sum(1 for meeting in meetings if not meeting.deal_id),
        no_intel_count=sum(1 for meeting in meetings if not meeting.research_data),
        no_recipient_count=no_recipient_count,
        unlinked=unlinked,
    )


def _attendees_key(attendees) -> frozenset:
    """Canonical frozenset of attendee emails for dedup comparison."""
    if not attendees:
        return frozenset()
    if isinstance(attendees, list):
        return frozenset(
            str(a.get("email", "")).lower().strip()
            for a in attendees
            if isinstance(a, dict) and a.get("email")
        )
    return frozenset()


async def _classify_internal_from_attendees(session: DBSession, attendees) -> Optional[bool]:
    if not isinstance(attendees, list) or not attendees:
        return None
    internal_domains = await get_internal_domains(session)
    return is_internal_only(attendees, internal_domains)


@router.post("/", response_model=MeetingRead, status_code=201)
async def create_meeting(payload: MeetingCreate, session: DBSession, current_user: CurrentUser):
    data = payload.model_dump()

    # Dedup: reject if a meeting with same title + overlapping attendees already exists
    # within a 2-hour window around the scheduled_at (same name on different days is fine)
    scheduled_at = data.get("scheduled_at")
    title = (data.get("title") or "").strip()
    if scheduled_at and title:
        window_start = scheduled_at - timedelta(hours=1)
        window_end = scheduled_at + timedelta(hours=1)
        existing_candidates = list((await session.execute(
            select(Meeting).where(
                Meeting.title == title,
                Meeting.scheduled_at >= window_start,
                Meeting.scheduled_at <= window_end,
            )
        )).scalars().all())

        incoming_attendees = _attendees_key(data.get("attendees"))
        for candidate in existing_candidates:
            existing_attendees = _attendees_key(candidate.attendees)
            # Same title + same time window + overlapping attendees = duplicate
            if not incoming_attendees or not existing_attendees or incoming_attendees & existing_attendees:
                return candidate  # return existing instead of creating duplicate

    data.setdefault("synced_by_user_id", str(current_user.id))
    data.setdefault("synced_at", datetime.utcnow())
    data.setdefault("external_source", "manual")
    internal_flag = await _classify_internal_from_attendees(session, data.get("attendees"))
    if internal_flag is not None:
        data["is_internal"] = internal_flag
    return await MeetingRepository(session).create(data)


@router.get("/{meeting_id}", response_model=MeetingRead)
async def get_meeting(meeting_id: UUID, session: DBSession, current_user: CurrentUser):
    return await MeetingRepository(session).get_or_raise(meeting_id)


@router.get("/{meeting_id}/recording-url", response_model=dict)
async def get_meeting_recording_url(
    meeting_id: UUID, session: DBSession, current_user: CurrentUser
):
    """
    Return a freshly-signed recording URL for tldv meetings.

    tldv signs download URLs with a short-lived JWT (minutes), so any URL we
    store in the DB is dead by the time the user clicks. This endpoint calls
    tldv at click-time and returns the fresh URL. The frontend opens the URL
    in a new tab/window; the signed token never round-trips through the UI.
    """
    meeting = await MeetingRepository(session).get_or_raise(meeting_id)
    if (meeting.external_source or "").lower() != "tldv" or not meeting.external_source_id:
        raise HTTPException(status_code=404, detail="Meeting has no tldv recording")

    from app.clients.tldv import TldvClient, TldvError

    client = TldvClient()
    try:
        fresh_url = await client.get_recording_download_url(meeting.external_source_id)
    except TldvError as exc:
        message = str(exc)
        # tldv returns this message when a Free-tier user recorded the meeting —
        # their API tier doesn't allow programmatic download. Surface a cleaner
        # message so the UI can explain it rather than showing a raw "403".
        if "free user" in message.lower() or "free tier" in message.lower():
            raise HTTPException(
                status_code=409,
                detail="This meeting was recorded by a tl;dv Free user. The recording is only viewable inside the tl;dv app and cannot be downloaded via API.",
            )
        raise HTTPException(
            status_code=502 if (exc.status_code or 0) >= 500 else 404,
            detail=f"tldv: {message}",
        )
    if not fresh_url:
        raise HTTPException(status_code=404, detail="No recording available for this meeting")
    return {"url": fresh_url}


@router.put("/{meeting_id}", response_model=MeetingRead)
async def update_meeting(meeting_id: UUID, payload: MeetingUpdate, session: DBSession, current_user: CurrentUser):
    repo = MeetingRepository(session)
    meeting = await repo.get_or_raise(meeting_id)
    update_data = payload.model_dump(exclude_unset=True)
    # If a user edits company_id or deal_id, lock the link so future calendar
    # syncs cannot silently overwrite it. Explicitly passing manually_linked
    # (e.g., false to re-enable auto-linking) still wins.
    touches_link = "company_id" in update_data or "deal_id" in update_data
    if touches_link and "manually_linked" not in update_data:
        update_data["manually_linked"] = True
    if "attendees" in update_data:
        internal_flag = await _classify_internal_from_attendees(session, update_data.get("attendees"))
        if internal_flag is not None:
            update_data["is_internal"] = internal_flag
    update_data["updated_at"] = datetime.utcnow()
    return await repo.update(meeting, update_data)


@router.delete("/{meeting_id}", status_code=204)
async def delete_meeting(meeting_id: UUID, session: DBSession, current_user: CurrentUser):
    repo = MeetingRepository(session)
    meeting = await repo.get_or_raise(meeting_id)
    await repo.delete(meeting)


@router.post("/{meeting_id}/pre-brief")
async def generate_pre_brief(meeting_id: UUID, session: DBSession, current_user: CurrentUser):
    """Generate AI pre-meeting brief combining company research + attendee profiles."""
    await require_workspace_permission(session, current_user, "run_pre_meeting_intel")

    repo = MeetingRepository(session)
    meeting = await repo.get_or_raise(meeting_id)

    from app.services.pre_meeting import generate_account_brief
    brief_text = ""

    if meeting.company_id:
        company_brief = await generate_account_brief(meeting.company_id, session)
        brief_text += company_brief.get("brief") or ""

    if meeting.attendees:
        attendee_ids = [a.get("contact_id") for a in meeting.attendees if a.get("contact_id")]
        if attendee_ids:
            from app.services.contact_intelligence import generate_contact_brief
            attendee_briefs = []
            for cid in attendee_ids[:3]:
                try:
                    cb = await generate_contact_brief(UUID(cid), session)
                    if cb.get("brief"):
                        name = cb.get("contact_name", "")
                        attendee_briefs.append(
                            f"\n--- {name} ({cb.get('title', '')}) ---\n{cb['brief']}"
                        )
                except Exception:
                    pass
            if attendee_briefs:
                brief_text += "\n\nSTAKEHOLDER PROFILES:" + "".join(attendee_briefs)

    meeting.pre_brief = brief_text
    meeting.updated_at = datetime.utcnow()
    await repo.save(meeting)
    return {"meeting_id": str(meeting_id), "pre_brief": brief_text}


@router.post("/{meeting_id}/intelligence")
async def run_meeting_intelligence(meeting_id: UUID, session: DBSession, current_user: CurrentUser):
    """
    Full pre-meeting intelligence: website scrape, DuckDuckGo news/signals,
    Hunter contacts, Google News, competitive landscape, GPT-4o executive
    briefing. Saves to meeting.research_data. ~10-15s.
    """
    await require_workspace_permission(session, current_user, "run_pre_meeting_intel")

    import logging
    logger = logging.getLogger(__name__)
    from app.services.pre_meeting_intelligence import run_pre_meeting_intelligence
    try:
        result = await run_pre_meeting_intelligence(meeting_id, session)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Pre-meeting intelligence failed for {meeting_id}")
        raise HTTPException(status_code=500, detail=f"Intelligence run failed: {str(e)}")


@router.post("/{meeting_id}/demo-strategy")
async def generate_demo_strategy(meeting_id: UUID, session: DBSession, current_user: CurrentUser):
    """
    GPT-4o Demo Strategy & Story Lineup. Reads cached research_data (if intel
    was already run) plus company DB profile. Saves to meeting.demo_strategy.
    """
    await require_workspace_permission(session, current_user, "run_pre_meeting_intel")

    import logging
    logger = logging.getLogger(__name__)
    from app.services.pre_meeting_intelligence import generate_meeting_demo_strategy
    try:
        result = await generate_meeting_demo_strategy(meeting_id, session)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Demo strategy generation failed for {meeting_id}")
        raise HTTPException(status_code=500, detail=f"Demo strategy failed: {str(e)}")


@router.post("/{meeting_id}/research-more")
async def research_more(meeting_id: UUID, session: DBSession, current_user: CurrentUser):
    """
    Gap-filling enrichment: detects what's missing in the company's enrichment_cache
    and only fetches those pieces (Hunter firmographics, contacts, Google News, etc.).
    Much faster than running full web intel — only fills actual gaps.
    """
    await require_workspace_permission(session, current_user, "run_pre_meeting_intel")

    import logging
    logger = logging.getLogger(__name__)
    from app.services.pre_meeting_intelligence import run_research_more
    try:
        result = await run_research_more(meeting_id, session)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Research more failed for {meeting_id}")
        raise HTTPException(status_code=500, detail=f"Research more failed: {str(e)}")


@router.get("/{meeting_id}/research-gaps")
async def get_research_gaps(meeting_id: UUID, session: DBSession, current_user: CurrentUser):
    """
    Returns a list of data gaps for the meeting's company — what's missing
    so the frontend can show 'Research More (N gaps)' without running anything.
    """
    from app.models.meeting import Meeting
    from app.models.company import Company
    from datetime import datetime

    meeting = await session.get(Meeting, meeting_id)
    if not meeting or not meeting.company_id:
        return {"gaps": [], "count": 0}

    company = await session.get(Company, meeting.company_id)
    if not company:
        return {"gaps": [], "count": 0}

    import re as _re
    ec = company.enrichment_cache if isinstance(company.enrichment_cache, dict) else {}
    now = datetime.utcnow()
    gaps = []

    # Strip CRM suffixes to get real company name for news/search queries
    raw_name = company.name or ""
    clean_name = _re.sub(r'\s*-\s*(Impl|Skilljar|CS|Pilot|Trial|POC|Demo|Test)\s*$', '', raw_name, flags=_re.IGNORECASE).strip() or raw_name

    def _unwrap(key):
        entry = ec.get(key)
        return entry.get("data") if isinstance(entry, dict) else entry

    def _age_days(key) -> float:
        entry = ec.get(key)
        if not isinstance(entry, dict):
            return 9999
        fetched_at = entry.get("fetched_at")
        if not fetched_at:
            return 9999
        try:
            d = datetime.fromisoformat(fetched_at)
            return (now - d).total_seconds() / 86400
        except Exception:
            return 9999

    def _has_contacts() -> bool:
        data = _unwrap("hunter_contacts")
        if isinstance(data, list):
            return len(data) > 0
        if isinstance(data, dict):
            return len(data.get("contacts", [])) > 0
        return False

    domain = company.domain or ""

    if not _unwrap("hunter_company") and domain and not domain.endswith(".unknown"):
        gaps.append({"key": "hunter_company", "label": "Company firmographics (revenue, tech stack, size)"})

    hc_entry = ec.get("hunter_contacts")
    hc_paused = isinstance(hc_entry, dict) and hc_entry.get("paused")
    if (not _has_contacts() or hc_paused) and domain and not domain.endswith(".unknown"):
        gaps.append({"key": "hunter_contacts", "label": "Verified contacts with seniority & department"})

    if not _unwrap("google_news") or _age_days("google_news") > 7:
        if clean_name:
            gaps.append({"key": "google_news", "label": f"Latest news about {clean_name}"})

    if not _unwrap("web_scrape") and domain and not domain.endswith(".unknown"):
        gaps.append({"key": "web_scrape", "label": "Website content (pricing, product, careers)"})

    if not _unwrap("competitive_landscape_v2") and clean_name:
        gaps.append({"key": "competitive_landscape", "label": "Competitive landscape"})

    icp_raw = _unwrap("icp_analysis")
    icp_data = icp_raw.get("data") if isinstance(icp_raw, dict) and "data" in icp_raw else icp_raw
    if isinstance(icp_data, dict):
        missing_icp = []
        if not icp_data.get("conversation_starter"):
            missing_icp.append("conversation starter")
        if not icp_data.get("why_now"):
            missing_icp.append("why now")
        if missing_icp:
            gaps.append({"key": "icp_fields", "label": f"AI-generated {', '.join(missing_icp)}"})

    return {"gaps": gaps, "count": len(gaps)}


@router.post("/{meeting_id}/post-score")
async def generate_post_score(meeting_id: UUID, payload: dict, session: DBSession, current_user: CurrentUser):
    """Score a meeting from raw notes and generate MoM draft."""
    repo = MeetingRepository(session)
    meeting = await repo.get_or_raise(meeting_id)

    raw_notes = payload.get("raw_notes") or meeting.raw_notes or ""
    if not raw_notes:
        raise HTTPException(
            status_code=400,
            detail="raw_notes required in body or already saved on meeting",
        )

    from app.clients.claude import ClaudeClient
    from app.models.company import Company

    ai = ClaudeClient()
    company_name = "the company"
    if meeting.company_id:
        co = await session.get(Company, meeting.company_id)
        if co:
            company_name = co.name

    if ai.mock:
        result_data = {
            "meeting_score": 72,
            "what_went_right": "Good rapport established. Prospect engaged with the demo.",
            "what_went_wrong": "Did not address budget timeline clearly.",
            "next_steps": "Send pricing deck by EOD. Schedule technical deep-dive.",
            "mom_draft": (
                f"Hi team,\n\nThank you for your time today discussing {company_name}.\n\n"
                "Key takeaways:\n• [Summary from notes]\n\nNext steps:\n• Send pricing deck\n\n"
                "Best,\n[Your name]"
            ),
        }
    else:
        system = (
            "You are a sales coach analysing a meeting debrief. "
            "Respond in JSON only with keys: meeting_score (int 0-100), "
            "what_went_right (string), what_went_wrong (string), "
            "next_steps (string), mom_draft (professional email string)."
        )
        user = (
            f"Company: {company_name}\nMeeting type: {meeting.meeting_type}\n"
            f"Meeting notes:\n{raw_notes[:2000]}\n\nAnalyse and return JSON as specified."
        )
        from app.config import settings

        # Structured JSON debrief score — pin the standard model so
        # max_tokens=700 doesn't route this to the complex tier.
        raw = await ai.complete(system, user, max_tokens=700, model_override=settings.CLAUDE_MODEL_STANDARD)
        try:
            result_data = json.loads(raw or "{}")
        except Exception:
            result_data = {"meeting_score": 50, "mom_draft": raw or ""}

    meeting.raw_notes = raw_notes
    meeting.meeting_score = result_data.get("meeting_score")
    meeting.what_went_right = result_data.get("what_went_right")
    meeting.what_went_wrong = result_data.get("what_went_wrong")
    meeting.next_steps = result_data.get("next_steps")
    meeting.mom_draft = result_data.get("mom_draft")
    meeting.status = "completed"
    meeting.updated_at = datetime.utcnow()
    await repo.save(meeting)

    return {"meeting_id": str(meeting_id), **result_data}
