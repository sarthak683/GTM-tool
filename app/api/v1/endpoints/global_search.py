from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import and_, func, literal, or_, select
from sqlmodel import SQLModel

from app.core.dependencies import CurrentUser, DBSession
from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal
from app.repositories.contact import visible_contact_restriction
from app.models.meeting import Meeting
from app.models.sales_resource import SalesResource
from app.models.task import Task

router = APIRouter(prefix="/search", tags=["global-search"])


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

    company_rows = (
        await session.execute(
            select(Company)
            .where(
                or_(
                    Company.name.ilike(pattern),
                    Company.domain.ilike(pattern),
                    Company.industry.ilike(pattern),
                    Company.description.ilike(pattern),
                )
            )
            .order_by(Company.updated_at.desc())
            .limit(5)
        )
    ).scalars().all()

    contact_rows = (
        await session.execute(
            (
                select(Contact, Company.name.label("company_name"))
                .outerjoin(Company, Contact.company_id == Company.id)
                .where(
                    or_(
                        Contact.first_name.ilike(pattern),
                        Contact.last_name.ilike(pattern),
                        func.concat(Contact.first_name, literal(" "), Contact.last_name).ilike(pattern),
                        Contact.email.ilike(pattern),
                        Contact.title.ilike(pattern),
                        Company.name.ilike(pattern),
                    )
                )
                .where(contact_restriction if contact_restriction is not None else literal(True))
                .order_by(Contact.updated_at.desc())
                .limit(6)
            )
        )
    ).all()

    deal_rows = (
        await session.execute(
            select(Deal, Company.name.label("company_name"))
            .outerjoin(Company, Deal.company_id == Company.id)
            .where(
                or_(
                    Deal.name.ilike(pattern),
                    Deal.stage.ilike(pattern),
                    Deal.next_step.ilike(pattern),
                    Company.name.ilike(pattern),
                )
            )
            .order_by(Deal.updated_at.desc())
            .limit(6)
        )
    ).all()

    meeting_rows = (
        await session.execute(
            select(Meeting, Company.name.label("company_name"))
            .outerjoin(Company, Meeting.company_id == Company.id)
            .where(
                or_(
                    Meeting.title.ilike(pattern),
                    Meeting.meeting_type.ilike(pattern),
                    Company.name.ilike(pattern),
                )
            )
            .order_by(Meeting.updated_at.desc())
            .limit(4)
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
                and_(Task.entity_type == "company", Task.entity_id == Company.id),
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
                and_(Task.entity_type == "deal", Task.entity_id == Deal.id),
            )
            .where(
                or_(
                    Task.title.ilike(pattern),
                    Task.description.ilike(pattern),
                    Company.name.ilike(pattern),
                    Contact.email.ilike(pattern),
                    func.concat(Contact.first_name, literal(" "), Contact.last_name).ilike(pattern),
                    Deal.name.ilike(pattern),
                )
            )
            .order_by(Task.updated_at.desc())
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
                ),
            )
            .order_by(SalesResource.updated_at.desc())
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
