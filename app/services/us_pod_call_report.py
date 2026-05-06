import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.clients.resend_client import send_email
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.user import User

logger = logging.getLogger(__name__)

REPORT_TIMEZONE = ZoneInfo("America/Chicago")
LOOKBACK_DAYS = 7

US_POD_REPS = [
    {"name": "Pravalika Jamalpur", "email": "pravalika@beacon.li", "aliases": ["pravalika"]},
    {"name": "Mahesh Pothula", "email": "mahesh@beacon.li", "aliases": ["mahesh"]},
    {"name": "Pulkit Anand", "email": "pulkit@beacon.li", "aliases": ["pulkit"]},
]

US_POD_REPORT_RECIPIENTS = [
    "sehar@beacon.li",
    "rakesh@beacon.li",
    "shahruk@beacon.li",
    "pravalika@beacon.li",
    "mahesh@beacon.li",
    "pulkit@beacon.li",
    "sarthak@beacon.li",
]


@dataclass
class ResolvedRep:
    name: str
    email: str
    user_id: UUID | None
    user_email: str | None
    matched: bool


def default_report_date(now: datetime | None = None) -> date:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference.astimezone(REPORT_TIMEZONE).date() - timedelta(days=1)


def _utc_bounds_for_local_day(day: date) -> tuple[datetime, datetime]:
    local_start = datetime.combine(day, time.min).replace(tzinfo=REPORT_TIMEZONE)
    local_end = local_start + timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc).replace(tzinfo=None),
        local_end.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _activity_local_date(activity: Activity) -> date:
    created_at = activity.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(REPORT_TIMEZONE).date()


def _normalize(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _is_connected_call(activity: Activity) -> bool:
    outcome = _normalize(activity.call_outcome).replace("-", "_")
    if outcome in {"connected", "answered", "completed", "success"}:
        return True
    if outcome in {"missed", "no_answer", "not_answered", "voicemail", "failed", "busy"}:
        return False
    return bool(activity.call_duration and activity.call_duration >= 60)


def _outcome_bucket(activity: Activity) -> str:
    outcome = _normalize(activity.call_outcome).replace("-", "_")
    if outcome in {"connected", "answered", "completed", "success"}:
        return "connected"
    if outcome == "callback":
        return "callback"
    if outcome in {"voicemail", "left_voicemail"}:
        return "voicemail"
    if outcome in {"missed", "no_answer", "not_answered", "busy"}:
        return "not_answered"
    if outcome == "failed":
        return "failed"
    return "unknown"


async def _resolve_reps(session: AsyncSession) -> list[ResolvedRep]:
    users = (
        await session.execute(select(User).where(User.is_active == True))  # noqa: E712
    ).scalars().all()
    by_email = {_normalize(user.email): user for user in users}
    by_name = {_normalize(user.name): user for user in users}

    resolved: list[ResolvedRep] = []
    for rep in US_POD_REPS:
        expected_email = _normalize(rep["email"])
        user = by_email.get(expected_email)
        if not user:
            for alias in rep["aliases"]:
                alias_norm = _normalize(alias)
                user = next(
                    (
                        candidate
                        for name, candidate in by_name.items()
                        if name.startswith(alias_norm) or alias_norm in name
                    ),
                    None,
                )
                if user:
                    break
        resolved.append(
            ResolvedRep(
                name=str(rep["name"]),
                email=str(rep["email"]),
                user_id=user.id if user else None,
                user_email=user.email if user else None,
                matched=user is not None,
            )
        )
    return resolved


def _activity_rep_id(
    activity: Activity,
    *,
    rep_ids: set[UUID],
    rep_ids_by_aircall_name: dict[str, UUID],
    deal_owner: dict[UUID, UUID | None],
    contact_owner: dict[UUID, UUID | None],
) -> UUID | None:
    if activity.created_by_id in rep_ids:
        return activity.created_by_id

    aircall_name = _normalize(activity.aircall_user_name)
    if aircall_name:
        direct_match = rep_ids_by_aircall_name.get(aircall_name)
        if direct_match:
            return direct_match
        for rep_name, rep_id in rep_ids_by_aircall_name.items():
            if rep_name and (rep_name in aircall_name or aircall_name in rep_name):
                return rep_id

    if activity.deal_id and deal_owner.get(activity.deal_id) in rep_ids:
        return deal_owner.get(activity.deal_id)
    if activity.contact_id and contact_owner.get(activity.contact_id) in rep_ids:
        return contact_owner.get(activity.contact_id)
    return activity.created_by_id if activity.created_by_id in rep_ids else None


async def _load_owner_maps(
    session: AsyncSession,
    activities: list[Activity],
) -> tuple[dict[UUID, UUID | None], dict[UUID, UUID | None]]:
    deal_ids = {activity.deal_id for activity in activities if activity.deal_id}
    contact_ids = {activity.contact_id for activity in activities if activity.contact_id}

    deal_owner: dict[UUID, UUID | None] = {}
    if deal_ids:
        deal_rows = (
            await session.execute(select(Deal.id, Deal.assigned_to_id).where(Deal.id.in_(deal_ids)))
        ).all()
        deal_owner = {row.id: row.assigned_to_id for row in deal_rows}

    contact_owner: dict[UUID, UUID | None] = {}
    if contact_ids:
        contact_rows = (
            await session.execute(
                select(Contact.id, Contact.assigned_to_id).where(Contact.id.in_(contact_ids))
            )
        ).all()
        contact_owner = {row.id: row.assigned_to_id for row in contact_rows}

    return deal_owner, contact_owner


async def build_us_pod_call_report(
    session: AsyncSession,
    report_date: date | None = None,
) -> dict[str, Any]:
    target_date = report_date or default_report_date()
    start_date = target_date - timedelta(days=LOOKBACK_DAYS - 1)
    start_utc, _ = _utc_bounds_for_local_day(start_date)
    _, end_utc = _utc_bounds_for_local_day(target_date)

    reps = await _resolve_reps(session)
    rep_ids = {rep.user_id for rep in reps if rep.user_id}
    rep_ids_by_aircall_name = {
        _normalize(rep.name): rep.user_id
        for rep in reps
        if rep.user_id
    }

    activities = (
        await session.execute(
            select(Activity)
            .where(
                or_(
                    func.lower(Activity.type) == "call",
                    func.lower(Activity.medium) == "call",
                ),
                Activity.created_at >= start_utc,
                Activity.created_at < end_utc,
            )
            .order_by(Activity.created_at.asc())
        )
    ).scalars().all()

    deal_owner, contact_owner = await _load_owner_maps(session, activities)
    daily_counts: dict[UUID, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    target_metrics: dict[UUID, dict[str, Any]] = {}

    for rep in reps:
        if rep.user_id:
            target_metrics[rep.user_id] = {
                "calls": 0,
                "connected_calls": 0,
                "voicemail": 0,
                "not_answered": 0,
                "callback": 0,
                "failed": 0,
                "unknown_outcome": 0,
                "duration_seconds": 0,
                "unique_contacts": set(),
                "unique_deals": set(),
            }

    for activity in activities:
        rep_id = _activity_rep_id(
            activity,
            rep_ids=rep_ids,
            rep_ids_by_aircall_name=rep_ids_by_aircall_name,
            deal_owner=deal_owner,
            contact_owner=contact_owner,
        )
        if not rep_id or rep_id not in rep_ids:
            continue

        activity_day = _activity_local_date(activity)
        if start_date <= activity_day <= target_date:
            daily_counts[rep_id][activity_day] += 1

        if activity_day != target_date:
            continue

        metrics = target_metrics[rep_id]
        metrics["calls"] += 1
        metrics["duration_seconds"] += activity.call_duration or 0
        if activity.contact_id:
            metrics["unique_contacts"].add(activity.contact_id)
        if activity.deal_id:
            metrics["unique_deals"].add(activity.deal_id)

        bucket = _outcome_bucket(activity)
        if bucket == "connected" or _is_connected_call(activity):
            metrics["connected_calls"] += 1
        if bucket == "callback":
            metrics["callback"] += 1
        elif bucket == "voicemail":
            metrics["voicemail"] += 1
        elif bucket == "not_answered":
            metrics["not_answered"] += 1
        elif bucket == "failed":
            metrics["failed"] += 1
        elif bucket == "unknown":
            metrics["unknown_outcome"] += 1

    rows: list[dict[str, Any]] = []
    for rep in reps:
        metrics = target_metrics.get(rep.user_id) if rep.user_id else None
        day_counts = daily_counts.get(rep.user_id, {}) if rep.user_id else {}
        total_7d = sum(
            day_counts.get(start_date + timedelta(days=offset), 0)
            for offset in range(LOOKBACK_DAYS)
        )
        avg_7d = round(total_7d / LOOKBACK_DAYS, 1)
        calls = int(metrics["calls"]) if metrics else 0
        flags: list[str] = []
        if not rep.matched:
            flags.append("user not found")
        if calls == 0:
            flags.append("0 calls logged")
        elif avg_7d > 0 and calls < avg_7d * 0.5:
            flags.append("below 50% of 7-day average")
        if metrics and metrics["unknown_outcome"] > max(2, calls // 2):
            flags.append("many calls missing outcome")

        rows.append(
            {
                "rep_name": rep.name,
                "rep_email": rep.email,
                "user_id": str(rep.user_id) if rep.user_id else None,
                "matched_user_email": rep.user_email,
                "calls": calls,
                "connected_calls": int(metrics["connected_calls"]) if metrics else 0,
                "voicemail": int(metrics["voicemail"]) if metrics else 0,
                "not_answered": int(metrics["not_answered"]) if metrics else 0,
                "callback": int(metrics["callback"]) if metrics else 0,
                "failed": int(metrics["failed"]) if metrics else 0,
                "unknown_outcome": int(metrics["unknown_outcome"]) if metrics else 0,
                "duration_minutes": round((metrics["duration_seconds"] if metrics else 0) / 60, 1),
                "unique_contacts": len(metrics["unique_contacts"]) if metrics else 0,
                "unique_deals": len(metrics["unique_deals"]) if metrics else 0,
                "avg_calls_last_7_days": avg_7d,
                "flags": flags,
            }
        )

    report = {
        "report_date": target_date.isoformat(),
        "timezone": "America/Chicago",
        "lookback_days": LOOKBACK_DAYS,
        "recipients": US_POD_REPORT_RECIPIENTS,
        "rows": rows,
    }
    report["subject"] = _report_subject(report)
    report["body"] = _render_report_text(report)
    return report


def _report_subject(report: dict[str, Any]) -> str:
    return f"US Pod Daily Call Report - {report['report_date']}"


def _render_report_text(report: dict[str, Any]) -> str:
    lines = [
        f"US Pod Daily Call Report - {report['report_date']}",
        f"Reporting timezone: {report['timezone']}",
        "",
        "Rep                 Calls  Connected  VM  No answer  Callback  Failed  Unknown  7d avg  Talk min  Contacts  Deals  Flags",
        "------------------  -----  ---------  --  ---------  --------  ------  -------  ------  --------  --------  -----  -----",
    ]
    for row in report["rows"]:
        flags = ", ".join(row["flags"]) if row["flags"] else "-"
        lines.append(
            f"{row['rep_name'][:18]:18}  "
            f"{row['calls']:>5}  "
            f"{row['connected_calls']:>9}  "
            f"{row['voicemail']:>2}  "
            f"{row['not_answered']:>9}  "
            f"{row['callback']:>8}  "
            f"{row['failed']:>6}  "
            f"{row['unknown_outcome']:>7}  "
            f"{row['avg_calls_last_7_days']:>6}  "
            f"{row['duration_minutes']:>8}  "
            f"{row['unique_contacts']:>8}  "
            f"{row['unique_deals']:>5}  "
            f"{flags}"
        )

    lines.extend(
        [
            "",
            "Counting logic:",
            "- Includes activities where type or medium is call.",
            "- Credits the user who logged/made the call first, then Aircall user name, then deal/contact owner fallback.",
            "- Uses the America/Chicago calendar day so the report matches US pod working days.",
        ]
    )
    return "\n".join(lines)


async def send_us_pod_call_report_email(
    session: AsyncSession,
    report_date: date | None = None,
) -> dict[str, Any]:
    report = await build_us_pod_call_report(session, report_date)
    send_results = []
    for recipient in US_POD_REPORT_RECIPIENTS:
        result = await send_email(
            to=recipient,
            subject=report["subject"],
            body=report["body"],
            from_name="Beacon Sales Ops",
        )
        send_results.append({"to": recipient, **result})

    report["send_results"] = send_results
    logger.info("US pod call report sent for %s to %d recipients", report["report_date"], len(send_results))
    return report
