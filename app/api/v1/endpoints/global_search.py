from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import SQLModel

from app.core.dependencies import CurrentUser, DBSession
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal
from app.repositories.company import CompanyRepository, company_visibility_filter
from app.repositories.contact import ContactRepository, visible_contact_restriction
from app.repositories.deal import DealRepository, deal_visibility_filter
from app.models.meeting import Meeting
from app.models.sales_resource import SalesResource
from app.models.task import Task

router = APIRouter(prefix="/search", tags=["global-search"])

FUZZY_MIN_QUERY_LENGTH = 3


class GlobalSearchItem(SQLModel):
    id: str
    kind: str
    title: str
    subtitle: Optional[str] = None
    meta: Optional[str] = None
    link: str


class GlobalSearchSection(SQLModel):
    key: str
    label: str
    items: list[GlobalSearchItem]


class GlobalSearchResponse(SQLModel):
    query: str
    sections: list[GlobalSearchSection]


def _contains(text_value: Optional[str], needle: str) -> bool:
    return bool(text_value and needle.lower() in text_value.lower())


def _display_domain(value: Optional[str]) -> Optional[str]:
    domain = (value or "").strip()
    if not domain:
        return None
    if domain.lower().endswith(".unknown") or domain.isdigit():
        return None
    return domain


def _fuzzy_match(value: ColumnElement, query: str) -> ColumnElement[bool]:
    """Add typo tolerance only when the query is long enough to be meaningful.

    PostgreSQL's indexed pg_trgm `%` operator uses the configured similarity
    threshold (0.30 locally). It was checked against the local dataset on
    2026-08-24: `Beacn` → `Beacon` scores 0.444 and remains a match. One- and
    two-character input stays substring-only because trigram similarity is too
    noisy at that size.
    """
    if len(query) < FUZZY_MIN_QUERY_LENGTH:
        return literal(False)
    return value.op("%", is_comparison=True)(query)


@router.get("/global", response_model=GlobalSearchResponse)
async def global_search(
    session: DBSession,
    current_user: CurrentUser,
    q: str = Query(..., min_length=1, description="Global search query"),
):
    query = q.strip()
    pattern = f"%{query}%"

    # Prospect-visibility: a non-admin must not surface other reps' contacts via
    # search. None for admins/granted users (see all); otherwise own + unassigned.
    contact_restriction = await visible_contact_restriction(session, current_user)

    company_rank = func.greatest(
        func.similarity(Company.name, query),
        func.similarity(Company.domain, query),
    )
    company_rows = (
        await session.execute(
            CompanyRepository.visible_to(current_user, include_disabled=True)
            .where(
                or_(
                    Company.name.ilike(pattern),
                    Company.domain.ilike(pattern),
                    Company.industry.ilike(pattern),
                    Company.description.ilike(pattern),
                    _fuzzy_match(Company.name, query),
                    _fuzzy_match(Company.domain, query),
                ),
            )
            .order_by(company_rank.desc(), Company.updated_at.desc())
            .limit(5)
        )
    ).scalars().all()

    contact_name = func.concat(Contact.first_name, literal(" "), Contact.last_name)
    contact_rank = func.greatest(
        func.similarity(contact_name, query),
        func.similarity(Contact.email, query),
        func.similarity(Company.name, query),
    )
    contact_rows = (
        await session.execute(
            (
                (await ContactRepository.visible_to(session, current_user))
                .add_columns(Company.name.label("company_name"))
                .outerjoin(Company, Contact.company_id == Company.id)
                .where(
                    or_(
                        Contact.first_name.ilike(pattern),
                        Contact.last_name.ilike(pattern),
                        contact_name.ilike(pattern),
                        Contact.email.ilike(pattern),
                        Contact.title.ilike(pattern),
                        Company.name.ilike(pattern),
                        _fuzzy_match(contact_name, query),
                        _fuzzy_match(Contact.email, query),
                        _fuzzy_match(Company.name, query),
                    )
                )
                .order_by(contact_rank.desc(), Contact.updated_at.desc())
                .limit(6)
            )
        )
    ).all()

    deal_rank = func.greatest(
        func.similarity(Deal.name, query),
        func.similarity(Company.name, query),
    )
    deal_rows = (
        await session.execute(
            DealRepository.visible_to(current_user)
            .add_columns(Company.name.label("company_name"))
            .outerjoin(Company, Deal.company_id == Company.id)
            .where(
                or_(
                    Deal.name.ilike(pattern),
                    Deal.stage.ilike(pattern),
                    Deal.next_step.ilike(pattern),
                    Company.name.ilike(pattern),
                    _fuzzy_match(Deal.name, query),
                    _fuzzy_match(Company.name, query),
                ),
            )
            .order_by(deal_rank.desc(), Deal.updated_at.desc())
            .limit(6)
        )
    ).all()

    meeting_stmt = (
        select(Meeting, Company.name.label("company_name"))
        .outerjoin(Company, Meeting.company_id == Company.id)
        .where(
            or_(
                Meeting.title.ilike(pattern),
                Meeting.meeting_type.ilike(pattern),
                Company.name.ilike(pattern),
                _fuzzy_match(Meeting.title, query),
                _fuzzy_match(Company.name, query),
            )
        )
    )
    # Mirror GET /meetings visibility: non-admins must not surface other
    # reps' meetings through search — only their own (owned/synced) or those
    # on their deals/accounts.
    if not current_user.is_admin:
        meeting_stmt = meeting_stmt.outerjoin(Deal, Meeting.deal_id == Deal.id).where(
            or_(
                Meeting.owner_user_id == current_user.id,
                Meeting.synced_by_user_id == current_user.id,
                Deal.assigned_to_id == current_user.id,
                Company.assigned_to_id == current_user.id,
            )
        )
    meeting_rows = (
        await session.execute(
            meeting_stmt.order_by(
                func.greatest(
                    func.similarity(Meeting.title, query),
                    func.similarity(Company.name, query),
                ).desc(),
                Meeting.updated_at.desc(),
            ).limit(4)
        )
    ).all()

    task_rows = (
        await session.execute(
            select(
                Task,
                Company.name.label("company_name"),
                func.concat(Contact.first_name, literal(" "), Contact.last_name).label("contact_name"),
                Deal.name.label("deal_name"),
            )
            .outerjoin(
                Company,
                and_(
                    Task.entity_type == "company",
                    Task.entity_id == Company.id,
                    company_visibility_filter(
                        current_user.id,
                        current_user.is_admin,
                        include_disabled=True,
                    ),
                ),
            )
            .outerjoin(
                Contact,
                # Gate the contact join: a restricted user only joins (and can
                # match/see the name of) contacts they may view. For a hidden
                # contact the join yields NULL, so neither contact_name nor the
                # email/name search-match clauses below can surface it.
                and_(
                    Task.entity_type == "contact",
                    Task.entity_id == Contact.id,
                    contact_restriction if contact_restriction is not None else literal(True),
                ),
            )
            .outerjoin(
                Deal,
                and_(
                    Task.entity_type == "deal",
                    Task.entity_id == Deal.id,
                    deal_visibility_filter(current_user.id, current_user.is_admin),
                ),
            )
            .where(
                or_(
                    Task.title.ilike(pattern),
                    Task.description.ilike(pattern),
                    Company.name.ilike(pattern),
                    Contact.email.ilike(pattern),
                    func.concat(Contact.first_name, literal(" "), Contact.last_name).ilike(pattern),
                    Deal.name.ilike(pattern),
                    _fuzzy_match(Task.title, query),
                    _fuzzy_match(Company.name, query),
                    _fuzzy_match(contact_name, query),
                    _fuzzy_match(Deal.name, query),
                ),
                literal(True) if current_user.is_admin else or_(
                    Task.assigned_to_id == current_user.id,
                    Task.created_by_id == current_user.id,
                ),
                or_(
                    and_(Task.entity_type == "company", Company.id.is_not(None)),
                    and_(Task.entity_type == "contact", Contact.id.is_not(None)),
                    and_(Task.entity_type == "deal", Deal.id.is_not(None)),
                ),
            )
            .order_by(
                func.greatest(
                    func.similarity(Task.title, query),
                    func.similarity(Company.name, query),
                    func.similarity(contact_name, query),
                    func.similarity(Deal.name, query),
                ).desc(),
                Task.updated_at.desc(),
            )
            .limit(6)
        )
    ).all()

    resource_rows = (
        await session.execute(
            select(SalesResource)
            .where(
                SalesResource.is_active == True,  # noqa: E712
                or_(
                    SalesResource.title.ilike(pattern),
                    SalesResource.description.ilike(pattern),
                    SalesResource.content.ilike(pattern),
                    _fuzzy_match(SalesResource.title, query),
                ),
            )
            .order_by(
                func.similarity(SalesResource.title, query).desc(),
                SalesResource.updated_at.desc(),
            )
            .limit(5)
        )
    ).scalars().all()

    sections: list[GlobalSearchSection] = []

    deal_items = []
    for deal, company_name in deal_rows:
        deal_items.append(
            GlobalSearchItem(
                id=str(deal.id),
                kind="deal",
                title=deal.name,
                subtitle=company_name or deal.stage.replace("_", " "),
                meta=deal.stage.replace("_", " ").title(),
                link=f"/pipeline?deal={deal.id}",
            )
        )
    if deal_items:
        sections.append(GlobalSearchSection(key="deals", label="Deals", items=deal_items))

    company_items = [
        GlobalSearchItem(
            id=str(company.id),
            kind="company",
            title=company.name,
            subtitle=_display_domain(company.domain) or company.industry or "Domain not found",
            meta="Account",
            link=f"/account-sourcing/{company.id}",
        )
        for company in company_rows
        if company.id
    ]
    if company_items:
        sections.append(GlobalSearchSection(key="companies", label="Accounts", items=company_items))

    contact_items = []
    for contact, company_name in contact_rows:
        full_name = f"{contact.first_name} {contact.last_name}".strip() or contact.email or "Unnamed contact"
        contact_items.append(
            GlobalSearchItem(
                id=str(contact.id),
                kind="contact",
                title=full_name,
                subtitle=contact.title or contact.email or company_name,
                meta=company_name or "Prospect",
                link=f"/contacts/{contact.id}",
            )
        )
    if contact_items:
        sections.append(GlobalSearchSection(key="contacts", label="Prospects", items=contact_items))

    meeting_items = []
    for meeting, company_name in meeting_rows:
        meeting_items.append(
            GlobalSearchItem(
                id=str(meeting.id),
                kind="meeting",
                title=meeting.title,
                subtitle=company_name or meeting.meeting_type,
                meta=meeting.status.replace("_", " ").title(),
                link=f"/meetings/{meeting.id}",
            )
        )
    if meeting_items:
        sections.append(GlobalSearchSection(key="meetings", label="Meetings", items=meeting_items))

    task_items = []
    for task, company_name, contact_name, deal_name in task_rows:
        entity_name = company_name or (contact_name.strip() if contact_name else "") or deal_name or "Task"
        if task.entity_type == "company":
            link = f"/account-sourcing/{task.entity_id}"
        elif task.entity_type == "contact":
            link = f"/contacts/{task.entity_id}"
        else:
            link = f"/pipeline?deal={task.entity_id}"
        task_items.append(
            GlobalSearchItem(
                id=str(task.id),
                kind="task",
                title=task.title,
                subtitle=entity_name,
                meta=task.status.replace("_", " ").title(),
                link=link,
            )
        )
    if task_items:
        sections.append(GlobalSearchSection(key="tasks", label="Tasks", items=task_items))

    resource_items = []
    for resource in resource_rows:
        meta_parts = [resource.category.replace("_", " ").title()]
        if resource.modules:
            meta_parts.append(", ".join(module.replace("_", " ") for module in resource.modules[:2]))
        resource_items.append(
            GlobalSearchItem(
                id=str(resource.id),
                kind="resource",
                title=resource.title,
                subtitle=resource.description,
                meta=" • ".join(part for part in meta_parts if part),
                link=f"/knowledge-base?resource={resource.id}",
            )
        )
    if resource_items:
        sections.append(GlobalSearchSection(key="resources", label="Knowledge", items=resource_items))

    return GlobalSearchResponse(query=query, sections=sections)
