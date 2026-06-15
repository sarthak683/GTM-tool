from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.activity import Activity
from app.models.company import Company
from app.models.deal import Deal
from app.models.task import Task
from app.models.user import User
from app.repositories.deal import DealRepository


STATUS_MAP = {
    "open": "open",
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

PRIORITY_MAP = {
    "urgent": "urgent",
    "high": "high",
    "normal": "normal",
    "low": "low",
}


@dataclass
class ImportStats:
    top_level_tasks_seen: int = 0
    subtasks_seen: int = 0
    companies_created: int = 0
    companies_reused: int = 0
    deals_created: int = 0
    deals_updated: int = 0
    tasks_created: int = 0
    tasks_updated: int = 0
    activities_created: int = 0
    activities_reused: int = 0
    unmatched_assignees: set[str] = field(default_factory=set)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-") or "unknown"


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def parse_epoch_ms(value: Any) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.utcfromtimestamp(int(value) / 1000)
    except (TypeError, ValueError, OSError):
        return None


def parse_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_clickup_status(raw_status: str | None) -> str:
    key = normalize_text(raw_status)
    return STATUS_MAP.get(key, "open")


def parse_priority(raw_priority: str | None) -> str:
    return PRIORITY_MAP.get(normalize_text(raw_priority), "normal")


def extract_company_name(raw_title: str) -> tuple[str, list[str]]:
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


def extract_domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or parsed.path or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def placeholder_domain(company_name: str) -> str:
    return f"{slugify(company_name)}.unknown"


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\r", "\n").strip()
    return text or None


def title_qualifier_tags(raw_title: str, existing_tags: list[str]) -> list[str]:
    _, qualifiers = extract_company_name(raw_title)
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

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{self._base_url}{path}", headers=self._headers, params=params)
            response.raise_for_status()
            payload = response.json()

        if self._cache_dir and cache_name:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = self._cache_dir / cache_name
            cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

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


async def load_users() -> tuple[dict[str, User], dict[str, User]]:
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
    by_email = {normalize_text(user.email): user for user in users if user.email}
    by_name = {normalize_text(user.name): user for user in users if user.name}
    return by_email, by_name


def map_user(assignee: dict[str, Any], users_by_email: dict[str, User], users_by_name: dict[str, User]) -> User | None:
    email = normalize_text(assignee.get("email"))
    if email and email in users_by_email:
        return users_by_email[email]
    name = normalize_text(assignee.get("username"))
    if name and name in users_by_name:
        return users_by_name[name]
    return None


async def get_or_create_company(
    session,
    task: dict[str, Any],
    stats: ImportStats,
    company_cache: dict[str, Company],
) -> Company:
    raw_name = task.get("name") or "Unknown Company"
    company_name, _ = extract_company_name(raw_name)
    associated_url = next((field.get("value") for field in task.get("custom_fields", []) if field.get("name") == "Associated Company"), None)
    domain = extract_domain_from_url(associated_url) or placeholder_domain(company_name)
    cache_key = normalize_text(domain if not domain.endswith(".unknown") else company_name)

    cached = company_cache.get(cache_key)
    if cached:
        stats.companies_reused += 1
        return cached

    stmt = select(Company).where(
        func.lower(Company.domain) == domain.lower()
        if not domain.endswith(".unknown")
        else func.lower(Company.name) == company_name.lower()
    )
    company = (await session.execute(stmt)).scalar_one_or_none()
    if company:
        company_cache[cache_key] = company
        stats.companies_reused += 1
        return company

    company = Company(
        name=company_name,
        domain=domain,
        description=clean_text(task.get("text_content")) or clean_text(task.get("description")),
        enrichment_sources={
            "clickup_import": {
                "source": "clickup",
                "task_name_example": raw_name,
                "associated_company_url": associated_url,
            }
        },
        created_at=parse_epoch_ms(task.get("date_created")) or datetime.utcnow(),
        updated_at=parse_epoch_ms(task.get("date_updated")) or datetime.utcnow(),
    )
    session.add(company)
    await session.flush()
    company_cache[cache_key] = company
    stats.companies_created += 1
    return company


async def upsert_deal(
    session,
    repo: DealRepository,
    task: dict[str, Any],
    company: Company,
    users_by_email: dict[str, User],
    users_by_name: dict[str, User],
    stats: ImportStats,
) -> Deal:
    external_source = "clickup_task"
    external_id = str(task["id"])
    existing = (
        await session.execute(
            select(Deal).where(
                Deal.external_source == external_source,
                Deal.external_source_id == external_id,
            )
        )
    ).scalar_one_or_none()

    assignees = task.get("assignees") or []
    primary_user = None
    for assignee in assignees:
        primary_user = map_user(assignee, users_by_email, users_by_name)
        if primary_user:
            break
    if not primary_user:
        for assignee in assignees:
            identifier = assignee.get("email") or assignee.get("username")
            if identifier:
                stats.unmatched_assignees.add(str(identifier))

    payload = {
        "name": str(task.get("name") or company.name),
        "pipeline_type": "deal",
        "stage": parse_clickup_status(task.get("status", {}).get("status")),
        "priority": parse_priority((task.get("priority") or {}).get("priority")),
        "company_id": company.id,
        "assigned_to_id": primary_user.id if primary_user else None,
        "value": parse_decimal(next((field.get("value") for field in task.get("custom_fields", []) if field.get("name") == "Amount"), None)),
        "close_date_est": (parse_epoch_ms(task.get("due_date")) or None),
        "source": "clickup_import",
        "description": clean_text(task.get("text_content")) or clean_text(task.get("description")),
        "next_step": clean_text(next((field.get("value") for field in task.get("custom_fields", []) if field.get("name") == "Progress Updates"), None)),
        "tags": title_qualifier_tags(str(task.get("name") or ""), [tag.get("name") for tag in task.get("tags") or []]),
        "last_activity_at": parse_epoch_ms(next((field.get("value") for field in task.get("custom_fields", []) if field.get("name") == "Last Activity Date"), None)),
        "stage_entered_at": parse_epoch_ms(task.get("date_updated")) or datetime.utcnow(),
        "created_at": parse_epoch_ms(task.get("date_created")) or datetime.utcnow(),
        "updated_at": parse_epoch_ms(task.get("date_updated")) or datetime.utcnow(),
        "external_source": external_source,
        "external_source_id": external_id,
    }

    if payload["close_date_est"]:
        payload["close_date_est"] = payload["close_date_est"].date()

    if existing:
        if payload.get("email_cc_alias"):
            normalized_alias = repo._slugify_alias(str(payload["email_cc_alias"]))
            if normalized_alias != existing.email_cc_alias:
                payload["email_cc_alias"] = await repo.generate_unique_email_cc_alias(normalized_alias, exclude_id=existing.id)
            else:
                payload["email_cc_alias"] = normalized_alias
        for key, value in payload.items():
            setattr(existing, key, value)
        session.add(existing)
        await session.flush()
        stats.deals_updated += 1
        return existing

    if not payload.get("email_cc_alias"):
        payload["email_cc_alias"] = await repo.generate_unique_email_cc_alias(str(payload.get("name") or "deal"))
    deal = Deal(**payload)
    session.add(deal)
    await session.flush()
    stats.deals_created += 1
    return deal


async def ensure_import_summary_activity(session, deal: Deal, task: dict[str, Any], assignee_names: list[str], users_by_email: dict[str, User], users_by_name: dict[str, User], stats: ImportStats) -> None:
    source = "clickup_task_import"
    external_id = str(task["id"])
    existing = (
        await session.execute(
            select(Activity).where(
                Activity.external_source == source,
                Activity.external_source_id == external_id,
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

    creator = map_user(task.get("creator") or {}, users_by_email, users_by_name)
    activity = Activity(
        deal_id=deal.id,
        type="import_note",
        source="clickup_import",
        medium="other",
        content="\n".join(summary_lines),
        created_by_id=creator.id if creator else None,
        created_at=parse_epoch_ms(task.get("date_created")) or datetime.utcnow(),
        event_metadata={
            "clickup_task_id": task["id"],
            "raw_status": raw_status,
            "raw_name": task.get("name"),
            "clickup_url": task.get("url"),
            "assignees": assignee_names,
        },
        external_source=source,
        external_source_id=external_id,
    )
    session.add(activity)
    stats.activities_created += 1


async def ensure_progress_activity(session, deal: Deal, task: dict[str, Any], users_by_email: dict[str, User], users_by_name: dict[str, User], stats: ImportStats) -> None:
    progress = clean_text(next((field.get("value") for field in task.get("custom_fields", []) if field.get("name") == "Progress Updates"), None))
    if not progress:
        return

    source = "clickup_progress"
    external_id = str(task["id"])
    existing = (
        await session.execute(
            select(Activity).where(
                Activity.external_source == source,
                Activity.external_source_id == external_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        stats.activities_reused += 1
        return

    creator = map_user(task.get("creator") or {}, users_by_email, users_by_name)
    activity = Activity(
        deal_id=deal.id,
        type="note",
        source="clickup_import",
        medium="other",
        content=progress,
        created_by_id=creator.id if creator else None,
        created_at=parse_epoch_ms(next((field.get("value") for field in task.get("custom_fields", []) if field.get("name") == "Last Activity Date"), None))
        or parse_epoch_ms(task.get("date_updated"))
        or datetime.utcnow(),
        event_metadata={"clickup_task_id": task["id"], "kind": "progress_updates"},
        external_source=source,
        external_source_id=external_id,
    )
    session.add(activity)
    stats.activities_created += 1


async def ensure_comment_activities(session, deal: Deal, task: dict[str, Any], comments: list[dict[str, Any]], users_by_email: dict[str, User], users_by_name: dict[str, User], stats: ImportStats) -> None:
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

        user = map_user(comment.get("user") or {}, users_by_email, users_by_name)
        activity = Activity(
            deal_id=deal.id,
            type="comment",
            source="clickup_import",
            medium="other",
            content=clean_text(comment.get("comment_text")) or "Imported ClickUp comment",
            created_by_id=user.id if user else None,
            created_at=parse_epoch_ms(comment.get("date")) or parse_epoch_ms(task.get("date_updated")) or datetime.utcnow(),
            event_metadata={"clickup_task_id": task["id"], "reply_count": comment.get("reply_count", 0)},
            external_source="clickup_comment",
            external_source_id=external_id,
        )
        session.add(activity)
        stats.activities_created += 1


async def upsert_subtask(
    session,
    subtask: dict[str, Any],
    deal: Deal,
    users_by_email: dict[str, User],
    users_by_name: dict[str, User],
    stats: ImportStats,
) -> None:
    system_key = f"clickup_subtask:{subtask['id']}"
    existing = (
        await session.execute(select(Task).where(Task.system_key == system_key))
    ).scalar_one_or_none()

    assignees = subtask.get("assignees") or []
    assignee_user = None
    for assignee in assignees:
        assignee_user = map_user(assignee, users_by_email, users_by_name)
        if assignee_user:
            break

    task_payload = {
        "entity_type": "deal",
        "entity_id": deal.id,
        "task_type": "manual",
        "title": clean_text(subtask.get("name")) or f"Imported ClickUp subtask {subtask['id']}",
        "description": clean_text(subtask.get("text_content")) or clean_text(subtask.get("description")),
        "status": "completed" if normalize_text(subtask.get("status", {}).get("type")) in {"done", "closed"} else "open",
        "priority": parse_priority((subtask.get("priority") or {}).get("priority")),
        "source": "clickup_import",
        "due_at": parse_epoch_ms(subtask.get("due_date")),
        "system_key": system_key,
        "created_by_id": (map_user(subtask.get("creator") or {}, users_by_email, users_by_name) or None).id if map_user(subtask.get("creator") or {}, users_by_email, users_by_name) else None,
        "assigned_role": assignee_user.role if assignee_user else (deal.assigned_to_id and "ae") or "ae",
        "assigned_to_id": assignee_user.id if assignee_user else deal.assigned_to_id,
        "created_at": parse_epoch_ms(subtask.get("date_created")) or datetime.utcnow(),
        "updated_at": parse_epoch_ms(subtask.get("date_updated")) or datetime.utcnow(),
    }

    if existing:
        for key, value in task_payload.items():
            setattr(existing, key, value)
        session.add(existing)
        stats.tasks_updated += 1
        return

    session.add(Task(**task_payload))
    stats.tasks_created += 1


async def fetch_comments_for_tasks(client: ClickUpClient, tasks: list[dict[str, Any]], enabled: bool) -> dict[str, list[dict[str, Any]]]:
    if not enabled:
        return {}

    semaphore = asyncio.Semaphore(8)
    results: dict[str, list[dict[str, Any]]] = {}

    async def worker(task: dict[str, Any]) -> None:
        async with semaphore:
            results[str(task["id"])] = await client.get_comments(str(task["id"]))

    await asyncio.gather(*(worker(task) for task in tasks))
    return results


async def import_clickup(args: argparse.Namespace) -> ImportStats:
    if not settings.CLICKUP_API_TOKEN:
        raise RuntimeError("CLICKUP_API_TOKEN is not configured")
    if not settings.CLICKUP_DEALS_LIST_ID:
        raise RuntimeError("CLICKUP_DEALS_LIST_ID is not configured")

    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else None
    client = ClickUpClient(
        token=settings.CLICKUP_API_TOKEN,
        base_url=settings.CLICKUP_API_BASE,
        cache_dir=cache_dir,
    )

    users_by_email, users_by_name = await load_users()
    stats = ImportStats()

    fields = await client.get_fields(settings.CLICKUP_DEALS_LIST_ID)
    print(f"Loaded {len(fields)} ClickUp custom fields")

    all_tasks = await client.get_all_tasks(settings.CLICKUP_DEALS_LIST_ID)
    top_level_tasks = [task for task in all_tasks if not task.get("parent")]
    subtasks = [task for task in all_tasks if task.get("parent")]

    if args.limit:
        top_level_tasks = top_level_tasks[: args.limit]
        selected_ids = {str(task["id"]) for task in top_level_tasks}
        subtasks = [task for task in subtasks if str(task.get("parent")) in selected_ids]

    stats.top_level_tasks_seen = len(top_level_tasks)
    stats.subtasks_seen = len(subtasks)

    comments_by_task = await fetch_comments_for_tasks(client, top_level_tasks, enabled=not args.skip_comments)

    async with AsyncSessionLocal() as session:
        repo = DealRepository(session)
        company_cache: dict[str, Company] = {}
        imported_deals_by_clickup_id: dict[str, Deal] = {}

        for task in top_level_tasks:
            company = await get_or_create_company(session, task, stats, company_cache)
            deal = await upsert_deal(session, repo, task, company, users_by_email, users_by_name, stats)
            imported_deals_by_clickup_id[str(task["id"])] = deal

            assignee_names = [str(assignee.get("username")) for assignee in task.get("assignees") or [] if assignee.get("username")]
            await ensure_import_summary_activity(session, deal, task, assignee_names, users_by_email, users_by_name, stats)
            await ensure_progress_activity(session, deal, task, users_by_email, users_by_name, stats)
            await ensure_comment_activities(
                session,
                deal,
                task,
                comments_by_task.get(str(task["id"]), []),
                users_by_email,
                users_by_name,
                stats,
            )

        if not args.skip_subtasks:
            for subtask in subtasks:
                parent_deal = imported_deals_by_clickup_id.get(str(subtask.get("parent")))
                if not parent_deal:
                    continue
                await upsert_subtask(session, subtask, parent_deal, users_by_email, users_by_name, stats)

        if args.commit:
            await session.commit()
        else:
            await session.rollback()

    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import Sales CRM deals from ClickUp into Beacon.")
    parser.add_argument("--commit", action="store_true", help="Persist the import. Without this flag, the script runs in dry-run mode.")
    parser.add_argument("--limit", type=int, default=0, help="Limit top-level ClickUp tasks imported (useful for local testing).")
    parser.add_argument("--cache-dir", default="tmp/clickup_import_cache", help="Directory for caching read-only ClickUp API responses.")
    parser.add_argument("--skip-comments", action="store_true", help="Skip importing ClickUp comments as deal activities.")
    parser.add_argument("--skip-subtasks", action="store_true", help="Skip importing ClickUp subtasks as Beacon manual tasks.")
    return parser


async def main() -> None:
    args = build_parser().parse_args()
    stats = await import_clickup(args)
    print("")
    print("ClickUp import summary")
    print(f"  Dry run: {'no' if args.commit else 'yes'}")
    print(f"  Top-level deals seen: {stats.top_level_tasks_seen}")
    print(f"  Subtasks seen: {stats.subtasks_seen}")
    print(f"  Companies created/reused: {stats.companies_created}/{stats.companies_reused}")
    print(f"  Deals created/updated: {stats.deals_created}/{stats.deals_updated}")
    print(f"  Tasks created/updated: {stats.tasks_created}/{stats.tasks_updated}")
    print(f"  Activities created/reused: {stats.activities_created}/{stats.activities_reused}")
    if stats.unmatched_assignees:
        print("  Unmatched ClickUp assignees:")
        for assignee in sorted(stats.unmatched_assignees):
            print(f"    - {assignee}")


if __name__ == "__main__":
    asyncio.run(main())
