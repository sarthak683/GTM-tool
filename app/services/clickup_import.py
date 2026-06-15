from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.activity import Activity
from app.models.company import Company
from app.models.company_stage_milestone import CompanyStageMilestone
from app.models.contact import Contact
from app.models.deal import Deal, DealContact
from app.models.settings import WorkspaceSettings
from app.models.task import Task
from app.models.user import User
from app.repositories.deal import DealRepository


STATUS_MAP = {
    "open": "reprospect",
    "reprospect": "reprospect",
    "4.demo scheduled": "demo_scheduled",
    "5.demo done": "demo_done",
    "6.qualified lead": "qualified_lead",
    "7.poc agreed": "poc_agreed",
    "8.poc wip": "poc_wip",
    "9.poc done": "poc_done",
    "10.commercial negotiation": "commercial_negotiation",
    "11.workshop/msa": "msa_review",
    "12.closed won": "closed_won",
    "churned": "churned",
    "not fit": "not_a_fit",
    "cold": "cold",
    "closed lost": "closed_lost",
    "on hold - revisit later": "on_hold",
    "nurture - future fit": "nurture",
    "closed": "closed_lost",
}

INACTIVE_STAGES = frozenset({
    "closed_won", "closed_lost", "not_a_fit", "cold",
    "on_hold", "nurture", "churned", "closed",
})

PRIORITY_MAP = {
    "urgent": "urgent",
    "high": "high",
    "normal": "normal",
    "low": "low",
}

logger = logging.getLogger(__name__)


def _normalize_clickup_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    # Handle values accidentally stored as floats/scientific notation.
    try:
        numeric = Decimal(text)
        if numeric == numeric.to_integral_value():
            return str(int(numeric))
    except (InvalidOperation, ValueError):
        pass

    digits = "".join(ch for ch in text if ch.isdigit())
    return digits or text


@dataclass
class ClickUpImportStats:
    top_level_tasks_seen: int = 0
    subtasks_seen: int = 0
    companies_created: int = 0
    companies_reused: int = 0
    tasks_skipped_no_company: int = 0
    contacts_created: int = 0
    contacts_updated: int = 0
    deals_created: int = 0
    deals_updated: int = 0
    tasks_created: int = 0
    tasks_updated: int = 0
    activities_created: int = 0
    activities_reused: int = 0
    unmatched_assignees: set[str] = field(default_factory=set)
    fields_loaded: int = 0

    def as_response(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["unmatched_assignees"] = sorted(self.unmatched_assignees)
        return payload


@dataclass
class ClickUpReplaceStats:
    deals_deleted: int = 0
    deal_contacts_deleted: int = 0
    deal_tasks_deleted: int = 0
    deal_milestones_deleted: int = 0
    activities_deleted: int = 0
    contacts_deleted: int = 0
    contact_tasks_deleted: int = 0
    contact_activities_deleted: int = 0
    companies_deleted: int = 0

    def as_response(self) -> dict[str, int]:
        return asdict(self)


class ClickUpClient:
    def __init__(self, token: str, base_url: str, cache_dir: Path | None = None) -> None:
        self._headers = {"Authorization": token}
        self._base_url = base_url.rstrip("/")
        self._cache_dir = cache_dir

    async def _get_json(self, path: str, params: dict[str, Any] | None = None, cache_name: str | None = None) -> Any:
        if self._cache_dir and cache_name:
            cache_path = self._cache_dir / cache_name
            if cache_path.exists():
                return json.loads(cache_path.read_text(encoding="utf-8"))

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{self._base_url}{path}", headers=self._headers, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                raise RuntimeError("ClickUp API authentication failed. Check CLICKUP_API_TOKEN.") from exc
            if status == 404:
                raise RuntimeError("ClickUp list not found. Verify the Deals List ID in Settings -> ClickUp CRM.") from exc
            raise RuntimeError(f"ClickUp API request failed with status {status}.") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("ClickUp API request failed due to a network/timeout issue.") from exc

        if self._cache_dir and cache_name:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = self._cache_dir / cache_name
            try:
                cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            except OSError:
                # Cache writes are best-effort only; import should continue if FS is restricted.
                logger.warning("clickup import cache write failed for %s", cache_path)

        return payload

    async def get_fields(self, list_id: str) -> list[dict[str, Any]]:
        payload = await self._get_json(f"/list/{list_id}/field", cache_name="fields.json")
        return payload.get("fields", [])

    async def get_all_tasks(self, list_id: str) -> list[dict[str, Any]]:
        page = 0
        tasks: list[dict[str, Any]] = []
        while True:
            payload = await self._get_json(
                f"/list/{list_id}/task",
                params={
                    "include_closed": "true",
                    "subtasks": "true",
                    "include_markdown_description": "true",
                    "page": page,
                },
                cache_name=f"tasks-page-{page}.json",
            )
            tasks.extend(payload.get("tasks", []))
            if payload.get("last_page", True):
                break
            page += 1
        return tasks

    async def get_comments(self, task_id: str) -> list[dict[str, Any]]:
        payload = await self._get_json(f"/task/{task_id}/comment", cache_name=f"comments-{task_id}.json")
        return payload.get("comments", [])


async def _resolve_clickup_config(session: AsyncSession) -> dict[str, str]:
    row = await session.get(WorkspaceSettings, 1)
    stored = row.clickup_crm_settings if row and isinstance(row.clickup_crm_settings, dict) else {}
    return {
        "api_token": settings.CLICKUP_API_TOKEN,
        "api_base": settings.CLICKUP_API_BASE,
        "team_id": _normalize_clickup_id(stored.get("team_id") or settings.CLICKUP_TEAM_ID or ""),
        "space_id": _normalize_clickup_id(stored.get("space_id") or settings.CLICKUP_SPACE_ID or ""),
        "deals_list_id": _normalize_clickup_id(stored.get("deals_list_id") or settings.CLICKUP_DEALS_LIST_ID or ""),
    }


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-") or "unknown"


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _parse_epoch_ms(value: Any) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.utcfromtimestamp(int(value) / 1000)
    except (TypeError, ValueError, OSError):
        return None


def _parse_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\r", "\n").strip()
    return text or None


def _parse_clickup_status(raw_status: str | None) -> str:
    return STATUS_MAP.get(_normalize_text(raw_status), "reprospect")


def _parse_priority(raw_priority: str | None) -> str:
    return PRIORITY_MAP.get(_normalize_text(raw_priority), "normal")


def _extract_company_name(raw_title: str) -> tuple[str, list[str]]:
    title = (raw_title or "").strip()
    if not title:
        return "Unknown Company", []

    qualifiers: list[str] = []
    pieces = [piece.strip() for piece in title.split("|")]
    base = pieces[0].strip()
    qualifiers.extend(piece for piece in pieces[1:] if piece)

    paren = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", base)
    if paren:
        base = paren.group(1).strip()
        qualifier = paren.group(2).strip()
        if qualifier:
            qualifiers.insert(0, qualifier)

    base = re.sub(r"\s+", " ", base).strip(" -|")
    return base or title, qualifiers


def _extract_domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or parsed.path or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _placeholder_domain(company_name: str) -> str:
    return f"{_slugify(company_name)}.unknown"


def _title_qualifier_tags(raw_title: str, existing_tags: list[str]) -> list[str]:
    _, qualifiers = _extract_company_name(raw_title)
    tags: list[str] = []
    seen: set[str] = set()
    for tag in [*(existing_tags or []), *qualifiers]:
        cleaned = re.sub(r"\s+", " ", str(tag).strip())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(cleaned)
    return tags


def _is_clickup_placeholder_contact(contact: Contact) -> bool:
    enrichment = contact.enrichment_data if isinstance(contact.enrichment_data, dict) else {}
    return enrichment.get("source") == "clickup_import_placeholder"


async def _load_users(session: AsyncSession) -> tuple[dict[str, User], dict[str, User]]:
    users = (await session.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
    return (
        {_normalize_text(user.email): user for user in users if user.email},
        {_normalize_text(user.name): user for user in users if user.name},
    )


def _map_user(assignee: dict[str, Any], users_by_email: dict[str, User], users_by_name: dict[str, User]) -> User | None:
    email = _normalize_text(assignee.get("email"))
    if email and email in users_by_email:
        return users_by_email[email]
    name = _normalize_text(assignee.get("username"))
    if name and name in users_by_name:
        return users_by_name[name]
    # Fuzzy match: ClickUp username's first name → beacon user's first name or email prefix
    if name:
        first = name.split()[0] if name.split() else name
        for user_email, user in users_by_email.items():
            prefix = user_email.split("@")[0]
            user_first = _normalize_text(user.name).split()[0] if user.name else ""
            if first and (first == prefix or first == user_first):
                return user
    return None


async def _resolve_primary_user(
    session: AsyncSession,
    assignees: list[dict[str, Any]],
    users_by_email: dict[str, User],
    users_by_name: dict[str, User],
    stats: ClickUpImportStats,
) -> User | None:
    for assignee in assignees or []:
        user = _map_user(assignee, users_by_email, users_by_name)
        if user:
            return user
    # Auto-create user for the first assignee with an email
    for assignee in assignees or []:
        email = _normalize_text(assignee.get("email"))
        username = assignee.get("username") or assignee.get("email") or ""
        if not email:
            continue
        # Check if user exists (may have been created in this batch)
        existing = (await session.execute(
            select(User).where(func.lower(User.email) == email).limit(1)
        )).scalar_one_or_none()
        if existing:
            users_by_email[email] = existing
            return existing
        # Create new user with placeholder google_id
        new_user = User(
            email=email,
            name=username.strip() or email.split("@")[0],
            google_id=f"clickup_import_{email}",
            role="ae",
            is_active=True,
        )
        session.add(new_user)
        await session.flush()
        users_by_email[email] = new_user
        users_by_name[_normalize_text(new_user.name)] = new_user
        logger.info("Auto-created user %s (%s) from ClickUp assignee", new_user.name, email)
        return new_user
    return None


async def _get_or_create_company(
    session: AsyncSession,
    task: dict[str, Any],
    stats: ClickUpImportStats,
    company_cache: dict[str, Company],
) -> Company | None:
    raw_name = task.get("name") or "Unknown Company"
    company_name, _ = _extract_company_name(raw_name)
    associated_url = next((field.get("value") for field in task.get("custom_fields", []) if field.get("name") == "Associated Company"), None)
    domain = _extract_domain_from_url(associated_url) or _placeholder_domain(company_name)
    cache_key = _normalize_text(domain if not domain.endswith(".unknown") else company_name)

    cached = company_cache.get(cache_key)
    if cached:
        stats.companies_reused += 1
        return cached

    stmt = select(Company).where(
        func.lower(Company.domain) == domain.lower()
        if not domain.endswith(".unknown")
        else func.lower(Company.name) == company_name.lower()
    )
    company = (await session.execute(stmt.limit(1))).scalars().first()
    if company:
        company_cache[cache_key] = company
        stats.companies_reused += 1
        return company

    enrichment_sources = {"clickup_import": {"hidden_from_account_sourcing": True}}
    company = Company(
        name=company_name,
        domain=domain,
        enrichment_sources=enrichment_sources,
        created_at=_parse_epoch_ms(task.get("date_created")) or datetime.utcnow(),
        updated_at=_parse_epoch_ms(task.get("date_updated")) or datetime.utcnow(),
    )
    session.add(company)
    await session.flush()
    company_cache[cache_key] = company
    stats.companies_created += 1
    return company


async def _upsert_deal(
    session: AsyncSession,
    repo: DealRepository,
    task: dict[str, Any],
    company: Company,
    users_by_email: dict[str, User],
    users_by_name: dict[str, User],
    stats: ClickUpImportStats,
) -> Deal:
    existing = (
        await session.execute(
            select(Deal).where(
                Deal.external_source == "clickup_task",
                Deal.external_source_id == str(task["id"]),
            )
        )
    ).scalar_one_or_none()

    assignees = task.get("assignees") or []
    primary_user = await _resolve_primary_user(session, assignees, users_by_email, users_by_name, stats)

    payload: dict[str, Any] = {
        "name": str(task.get("name") or company.name),
        "pipeline_type": "deal",
        "stage": _parse_clickup_status(task.get("status", {}).get("status")),
        "priority": _parse_priority((task.get("priority") or {}).get("priority")),
        "company_id": company.id,
        "assigned_to_id": primary_user.id if primary_user else None,
        "value": _parse_decimal(next((field.get("value") for field in task.get("custom_fields", []) if field.get("name") == "Amount"), None)),
        "close_date_est": _parse_epoch_ms(task.get("due_date")) or None,
        "source": "clickup_import",
        "description": _clean_text(task.get("text_content")) or _clean_text(task.get("description")),
        "next_step": _clean_text(next((field.get("value") for field in task.get("custom_fields", []) if field.get("name") == "Progress Updates"), None)),
        "tags": _title_qualifier_tags(str(task.get("name") or ""), [tag.get("name") for tag in task.get("tags") or []]),
        "last_activity_at": _parse_epoch_ms(next((field.get("value") for field in task.get("custom_fields", []) if field.get("name") == "Last Activity Date"), None)),
        "stage_entered_at": _parse_epoch_ms(task.get("date_updated")) or datetime.utcnow(),
        "created_at": _parse_epoch_ms(task.get("date_created")) or datetime.utcnow(),
        "updated_at": _parse_epoch_ms(task.get("date_updated")) or datetime.utcnow(),
        "external_source": "clickup_task",
        "external_source_id": str(task["id"]),
    }

    if payload["close_date_est"]:
        payload["close_date_est"] = payload["close_date_est"].date()

    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        session.add(existing)
        await session.flush()
        stats.deals_updated += 1
        return existing

    payload["email_cc_alias"] = await repo.generate_unique_email_cc_alias(str(payload.get("name") or "deal"))
    deal = Deal(**payload)
    session.add(deal)
    await session.flush()
    stats.deals_created += 1
    return deal


async def _upsert_placeholder_contact(
    session: AsyncSession,
    task: dict[str, Any],
    company: Company,
    deal: Deal,
    users_by_email: dict[str, User],
    users_by_name: dict[str, User],
    stats: ClickUpImportStats,
) -> Contact:
    assignees = task.get("assignees") or []
    primary_user = await _resolve_primary_user(session, assignees, users_by_email, users_by_name, stats)
    task_id = str(task["id"])

    existing_contacts = (
        await session.execute(select(Contact).where(Contact.company_id == company.id))
    ).scalars().all()
    existing = next(
        (
            contact
            for contact in existing_contacts
            if _is_clickup_placeholder_contact(contact)
            and isinstance(contact.enrichment_data, dict)
            and str(contact.enrichment_data.get("clickup_task_id") or "") == task_id
        ),
        None,
    )

    payload = {
        "first_name": str(task.get("name") or company.name),
        "last_name": "",
        "company_id": company.id,
        "title": "Imported from ClickUp",
        "persona": "unknown",
        "assigned_to_id": primary_user.id if primary_user else None,
        "assigned_rep_email": primary_user.email if primary_user else None,
        "outreach_lane": "outreach",
        "sequence_status": "not_started",
        "enriched_at": None,
        "enrichment_data": {
            "source": "clickup_import_placeholder",
            "clickup_task_id": task_id,
            "clickup_url": task.get("url"),
            "raw_name": task.get("name"),
            "company_name": company.name,
            "deal_id": str(deal.id),
        },
        "personalization_notes": _clean_text(
            next(
                (
                    field.get("value")
                    for field in task.get("custom_fields", [])
                    if field.get("name") == "Progress Updates"
                ),
                None,
            )
        ),
        "created_at": _parse_epoch_ms(task.get("date_created")) or datetime.utcnow(),
        "updated_at": _parse_epoch_ms(task.get("date_updated")) or datetime.utcnow(),
    }

    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        session.add(existing)
        await session.flush()
        # Ensure deal-contact link exists
        existing_link = (await session.execute(
            select(DealContact).where(DealContact.deal_id == deal.id, DealContact.contact_id == existing.id)
        )).scalar_one_or_none()
        if not existing_link:
            session.add(DealContact(deal_id=deal.id, contact_id=existing.id, role="imported"))
            await session.flush()
        stats.contacts_updated += 1
        return existing

    contact = Contact(**payload)
    session.add(contact)
    await session.flush()
    stats.contacts_created += 1

    # Link contact to deal via DealContact junction
    existing_link = (await session.execute(
        select(DealContact).where(DealContact.deal_id == deal.id, DealContact.contact_id == contact.id)
    )).scalar_one_or_none()
    if not existing_link:
        session.add(DealContact(deal_id=deal.id, contact_id=contact.id, role="imported"))
        await session.flush()
    return contact


async def _ensure_import_summary_activity(
    session: AsyncSession,
    deal: Deal,
    task: dict[str, Any],
    assignee_names: list[str],
    users_by_email: dict[str, User],
    users_by_name: dict[str, User],
    stats: ClickUpImportStats,
) -> None:
    existing = (
        await session.execute(
            select(Activity).where(
                Activity.external_source == "clickup_task_import",
                Activity.external_source_id == str(task["id"]),
            )
        )
    ).scalar_one_or_none()
    if existing:
        stats.activities_reused += 1
        return

    raw_status = task.get("status", {}).get("status")
    summary_lines = [
        f"Imported from ClickUp task {task['id']}",
        f"Original status: {raw_status}",
    ]
    if assignee_names:
        summary_lines.append(f"ClickUp assignees: {', '.join(assignee_names)}")
    if task.get("url"):
        summary_lines.append(f"ClickUp URL: {task['url']}")

    creator = _map_user(task.get("creator") or {}, users_by_email, users_by_name)
    session.add(
        Activity(
            deal_id=deal.id,
            type="import_note",
            source="clickup_import",
            medium="other",
            content="\n".join(summary_lines),
            created_by_id=creator.id if creator else None,
            created_at=_parse_epoch_ms(task.get("date_created")) or datetime.utcnow(),
            event_metadata={
                "clickup_task_id": task["id"],
                "raw_status": raw_status,
                "raw_name": task.get("name"),
                "clickup_url": task.get("url"),
                "assignees": assignee_names,
            },
            external_source="clickup_task_import",
            external_source_id=str(task["id"]),
        )
    )
    stats.activities_created += 1


async def _ensure_progress_activity(
    session: AsyncSession,
    deal: Deal,
    task: dict[str, Any],
    users_by_email: dict[str, User],
    users_by_name: dict[str, User],
    stats: ClickUpImportStats,
) -> None:
    progress = _clean_text(next((field.get("value") for field in task.get("custom_fields", []) if field.get("name") == "Progress Updates"), None))
    if not progress:
        return

    existing = (
        await session.execute(
            select(Activity).where(
                Activity.external_source == "clickup_progress",
                Activity.external_source_id == str(task["id"]),
            )
        )
    ).scalar_one_or_none()
    if existing:
        stats.activities_reused += 1
        return

    creator = _map_user(task.get("creator") or {}, users_by_email, users_by_name)
    session.add(
        Activity(
            deal_id=deal.id,
            type="note",
            source="clickup_import",
            medium="other",
            content=progress,
            created_by_id=creator.id if creator else None,
            created_at=_parse_epoch_ms(next((field.get("value") for field in task.get("custom_fields", []) if field.get("name") == "Last Activity Date"), None))
            or _parse_epoch_ms(task.get("date_updated"))
            or datetime.utcnow(),
            event_metadata={"clickup_task_id": task["id"], "kind": "progress_updates"},
            external_source="clickup_progress",
            external_source_id=str(task["id"]),
        )
    )
    stats.activities_created += 1


async def _ensure_comment_activities(
    session: AsyncSession,
    deal: Deal,
    task: dict[str, Any],
    comments: list[dict[str, Any]],
    users_by_email: dict[str, User],
    users_by_name: dict[str, User],
    stats: ClickUpImportStats,
) -> None:
    for comment in comments:
        external_id = str(comment["id"])
        existing = (
            await session.execute(
                select(Activity).where(
                    Activity.external_source == "clickup_comment",
                    Activity.external_source_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            stats.activities_reused += 1
            continue

        user = _map_user(comment.get("user") or {}, users_by_email, users_by_name)
        session.add(
            Activity(
                deal_id=deal.id,
                type="comment",
                source="clickup_import",
                medium="other",
                content=_clean_text(comment.get("comment_text")) or "Imported ClickUp comment",
                created_by_id=user.id if user else None,
                created_at=_parse_epoch_ms(comment.get("date")) or _parse_epoch_ms(task.get("date_updated")) or datetime.utcnow(),
                event_metadata={"clickup_task_id": task["id"], "reply_count": comment.get("reply_count", 0)},
                external_source="clickup_comment",
                external_source_id=external_id,
            )
        )
        stats.activities_created += 1


async def _upsert_subtask(
    session: AsyncSession,
    subtask: dict[str, Any],
    deal: Deal,
    users_by_email: dict[str, User],
    users_by_name: dict[str, User],
    stats: ClickUpImportStats,
) -> None:
    system_key = f"clickup_subtask:{subtask['id']}"
    existing = (await session.execute(select(Task).where(Task.system_key == system_key))).scalar_one_or_none()

    assignees = subtask.get("assignees") or []
    assignee_user = None
    for assignee in assignees:
        assignee_user = _map_user(assignee, users_by_email, users_by_name)
        if assignee_user:
            break

    creator = _map_user(subtask.get("creator") or {}, users_by_email, users_by_name)
    task_payload = {
        "entity_type": "deal",
        "entity_id": deal.id,
        "task_type": "manual",
        "title": _clean_text(subtask.get("name")) or f"Imported ClickUp subtask {subtask['id']}",
        "description": _clean_text(subtask.get("text_content")) or _clean_text(subtask.get("description")),
        "status": "completed" if _normalize_text(subtask.get("status", {}).get("type")) in {"done", "closed"} else "open",
        "priority": _parse_priority((subtask.get("priority") or {}).get("priority")),
        "source": "clickup_import",
        "due_at": _parse_epoch_ms(subtask.get("due_date")),
        "system_key": system_key,
        "created_by_id": creator.id if creator else None,
        "assigned_role": assignee_user.role if assignee_user else ("ae" if deal.assigned_to_id else None),
        "assigned_to_id": assignee_user.id if assignee_user else deal.assigned_to_id,
        "created_at": _parse_epoch_ms(subtask.get("date_created")) or datetime.utcnow(),
        "updated_at": _parse_epoch_ms(subtask.get("date_updated")) or datetime.utcnow(),
    }

    if existing:
        for key, value in task_payload.items():
            setattr(existing, key, value)
        session.add(existing)
        stats.tasks_updated += 1
        return

    session.add(Task(**task_payload))
    stats.tasks_created += 1


async def _fetch_comments_for_tasks(client: ClickUpClient, tasks: list[dict[str, Any]], enabled: bool) -> dict[str, list[dict[str, Any]]]:
    if not enabled:
        return {}

    results: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        results[str(task["id"])] = await client.get_comments(str(task["id"]))
    return results


async def replace_pipeline_deal_data(session: AsyncSession) -> ClickUpReplaceStats:
    stats = ClickUpReplaceStats()
    deal_ids = list((await session.execute(select(Deal.id))).scalars().all())

    clickup_placeholder_contacts = [
        contact
        for contact in (await session.execute(select(Contact))).scalars().all()
        if _is_clickup_placeholder_contact(contact)
    ]
    clickup_placeholder_contact_ids = [contact.id for contact in clickup_placeholder_contacts if contact.id]

    imported_company_candidates = (
        await session.execute(select(Company).where(Company.enrichment_sources.is_not(None)))
    ).scalars().all()
    imported_company_ids = [
        company.id
        for company in imported_company_candidates
        if isinstance(company.enrichment_sources, dict) and "clickup_import" in company.enrichment_sources
    ]

    if deal_ids:
        activity_delete = await session.execute(delete(Activity).where(Activity.deal_id.in_(deal_ids)))
        stats.activities_deleted = activity_delete.rowcount or 0

        task_delete = await session.execute(delete(Task).where(Task.entity_type == "deal", Task.entity_id.in_(deal_ids)))
        stats.deal_tasks_deleted = task_delete.rowcount or 0

        milestone_delete = await session.execute(
            delete(CompanyStageMilestone).where(CompanyStageMilestone.deal_id.in_(deal_ids))
        )
        stats.deal_milestones_deleted = milestone_delete.rowcount or 0

        deal_contact_delete = await session.execute(delete(DealContact).where(DealContact.deal_id.in_(deal_ids)))
        stats.deal_contacts_deleted = deal_contact_delete.rowcount or 0

        deal_delete = await session.execute(delete(Deal).where(Deal.id.in_(deal_ids)))
        stats.deals_deleted = deal_delete.rowcount or 0

    if clickup_placeholder_contact_ids:
        contact_activity_delete = await session.execute(
            delete(Activity).where(Activity.contact_id.in_(clickup_placeholder_contact_ids))
        )
        stats.contact_activities_deleted = contact_activity_delete.rowcount or 0

        contact_task_delete = await session.execute(
            delete(Task).where(Task.entity_type == "contact", Task.entity_id.in_(clickup_placeholder_contact_ids))
        )
        stats.contact_tasks_deleted = contact_task_delete.rowcount or 0

        contact_delete = await session.execute(
            delete(Contact).where(Contact.id.in_(clickup_placeholder_contact_ids))
        )
        stats.contacts_deleted = contact_delete.rowcount or 0

    if not deal_ids and not clickup_placeholder_contact_ids:
        return stats

    companies_deleted = 0
    for company_id in imported_company_ids:
        has_deals = (await session.execute(select(Deal.id).where(Deal.company_id == company_id).limit(1))).first()
        has_contacts = (await session.execute(select(Contact.id).where(Contact.company_id == company_id).limit(1))).first()
        if has_deals or has_contacts:
            continue
        company = await session.get(Company, company_id)
        if company:
            await session.delete(company)
            companies_deleted += 1
    stats.companies_deleted = companies_deleted
    return stats


async def import_sales_crm_clickup(
    session: AsyncSession,
    *,
    replace_existing: bool = True,
    limit: int = 0,
    cache_dir: str | None = None,
    skip_comments: bool = False,
    skip_subtasks: bool = False,
) -> dict[str, Any]:
    started_at = perf_counter()
    clickup_config = await _resolve_clickup_config(session)
    if not clickup_config["api_token"]:
        raise RuntimeError("CLICKUP_API_TOKEN is not configured")
    if not clickup_config["deals_list_id"]:
        raise RuntimeError("ClickUp Deals list ID is not configured")
    if not clickup_config["deals_list_id"].isdigit():
        raise RuntimeError("ClickUp Deals list ID is invalid. Use the numeric list ID from ClickUp.")

    replace_stats = ClickUpReplaceStats()
    if replace_existing:
        logger.info("clickup import: clearing existing pipeline data")
        replace_stats = await replace_pipeline_deal_data(session)
        logger.info(
            "clickup import: cleared deals=%s companies=%s activities=%s deal_tasks=%s contacts=%s",
            replace_stats.deals_deleted,
            replace_stats.companies_deleted,
            replace_stats.activities_deleted,
            replace_stats.deal_tasks_deleted,
            replace_stats.contacts_deleted,
        )

    client = ClickUpClient(
        token=clickup_config["api_token"],
        base_url=clickup_config["api_base"],
        cache_dir=Path(cache_dir).resolve() if cache_dir else None,
    )

    users_by_email, users_by_name = await _load_users(session)
    stats = ClickUpImportStats()

    fields = await client.get_fields(clickup_config["deals_list_id"])
    stats.fields_loaded = len(fields)
    logger.info("clickup import: loaded %s custom fields", stats.fields_loaded)

    all_tasks = await client.get_all_tasks(clickup_config["deals_list_id"])
    top_level_tasks = [task for task in all_tasks if not task.get("parent")]
    subtasks = [task for task in all_tasks if task.get("parent")]
    logger.info(
        "clickup import: fetched %s tasks total (%s top-level, %s subtasks)",
        len(all_tasks),
        len(top_level_tasks),
        len(subtasks),
    )

    if limit:
        top_level_tasks = top_level_tasks[:limit]
        selected_ids = {str(task["id"]) for task in top_level_tasks}
        subtasks = [task for task in subtasks if str(task.get("parent")) in selected_ids]
        logger.info("clickup import: limit applied, processing %s top-level tasks", len(top_level_tasks))

    stats.top_level_tasks_seen = len(top_level_tasks)
    stats.subtasks_seen = len(subtasks)

    comments_by_task = await _fetch_comments_for_tasks(client, top_level_tasks, enabled=not skip_comments)
    repo = DealRepository(session)
    company_cache: dict[str, Company] = {}
    imported_deals_by_clickup_id: dict[str, Deal] = {}

    for index, task in enumerate(top_level_tasks, start=1):
        company = await _get_or_create_company(session, task, stats, company_cache)
        if company is None:
            stats.tasks_skipped_no_company += 1
            continue
        deal = await _upsert_deal(session, repo, task, company, users_by_email, users_by_name, stats)
        await _upsert_placeholder_contact(session, task, company, deal, users_by_email, users_by_name, stats)
        imported_deals_by_clickup_id[str(task["id"])] = deal
        assignee_names = [str(assignee.get("username")) for assignee in task.get("assignees") or [] if assignee.get("username")]
        await _ensure_import_summary_activity(session, deal, task, assignee_names, users_by_email, users_by_name, stats)
        await _ensure_progress_activity(session, deal, task, users_by_email, users_by_name, stats)
        await _ensure_comment_activities(
            session,
            deal,
            task,
            comments_by_task.get(str(task["id"]), []),
            users_by_email,
            users_by_name,
            stats,
        )
        if index == len(top_level_tasks) or index % 50 == 0:
            logger.info(
                "clickup import: processed %s/%s deals (created=%s updated=%s companies_created=%s activities_created=%s)",
                index,
                len(top_level_tasks),
                stats.deals_created,
                stats.deals_updated,
                stats.companies_created,
                stats.activities_created,
            )

    if not skip_subtasks:
        for index, subtask in enumerate(subtasks, start=1):
            parent_deal = imported_deals_by_clickup_id.get(str(subtask.get("parent")))
            if not parent_deal:
                continue
            if parent_deal.stage in INACTIVE_STAGES:
                logger.debug(
                    "clickup import: skipping subtask %s — parent deal %s is in inactive stage %s",
                    subtask.get("id"), parent_deal.id, parent_deal.stage,
                )
                continue
            await _upsert_subtask(session, subtask, parent_deal, users_by_email, users_by_name, stats)
            if index == len(subtasks) or index % 100 == 0:
                logger.info(
                    "clickup import: processed %s/%s subtasks (tasks_created=%s tasks_updated=%s)",
                    index,
                    len(subtasks),
                    stats.tasks_created,
                    stats.tasks_updated,
                )

    await session.commit()
    result = {
        "replace": replace_stats.as_response(),
        "import": stats.as_response(),
    }
    logger.info("clickup import: complete in %.1fs", perf_counter() - started_at)
    return result
