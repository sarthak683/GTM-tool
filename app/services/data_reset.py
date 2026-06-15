from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.angel import AngelMapping
from app.models.battlecard import Battlecard
from app.models.company import Company
from app.models.company_stage_milestone import CompanyStageMilestone
from app.models.contact import Contact
from app.models.custom_demo import CustomDemo
from app.models.deal import Deal, DealContact
from app.models.meeting import Meeting
from app.models.outreach import OutreachSequence
from app.models.reminder import Reminder
from app.models.signal import Signal
from app.models.sourcing_batch import SourcingBatch
from app.models.task import Task, TaskComment
from app.repositories.company import CompanyRepository
from app.services.account_sourcing import refresh_company_prospecting_fields


def _count_deleted(result: Any) -> int:
    return int(result.rowcount or 0)


async def reset_account_sourcing_data(session: AsyncSession) -> dict[str, int]:
    company_ids = list(
        (
            await session.execute(
                select(Company.id).where(Company.sourcing_batch_id.isnot(None))
            )
        ).scalars().all()
    )
    if not company_ids:
        batch_result = await session.execute(delete(SourcingBatch))
        await session.commit()
        return {
            "companies_deleted": 0,
            "batches_deleted": _count_deleted(batch_result),
            "signals_deleted": 0,
            "meetings_deleted": 0,
            "custom_demos_deleted": 0,
        }

    deal_ids = list(
        (
            await session.execute(
                select(Deal.id).where(Deal.company_id.in_(company_ids))
            )
        ).scalars().all()
    )

    custom_demo_filter = [CustomDemo.company_id.in_(company_ids)]
    meeting_filter = [Meeting.company_id.in_(company_ids)]
    if deal_ids:
        custom_demo_filter.append(CustomDemo.deal_id.in_(deal_ids))
        meeting_filter.append(Meeting.deal_id.in_(deal_ids))

    signals_result = await session.execute(delete(Signal).where(Signal.company_id.in_(company_ids)))
    meetings_result = await session.execute(delete(Meeting).where(or_(*meeting_filter)))
    demos_result = await session.execute(delete(CustomDemo).where(or_(*custom_demo_filter)))
    await session.commit()

    repo = CompanyRepository(session)
    deleted_companies = 0
    for company_id in company_ids:
        await repo.delete_with_cascade(company_id)
        deleted_companies += 1

    batch_result = await session.execute(delete(SourcingBatch))
    await session.commit()

    return {
        "companies_deleted": deleted_companies,
        "batches_deleted": _count_deleted(batch_result),
        "signals_deleted": _count_deleted(signals_result),
        "meetings_deleted": _count_deleted(meetings_result),
        "custom_demos_deleted": _count_deleted(demos_result),
    }


async def reset_prospecting_data(session: AsyncSession) -> dict[str, int]:
    sequence_result = await session.execute(delete(OutreachSequence))
    activity_result = await session.execute(delete(Activity).where(Activity.contact_id.isnot(None)))
    reminders_result = await session.execute(delete(Reminder).where(Reminder.contact_id.isnot(None)))
    contact_result = await session.execute(delete(Contact))
    await session.commit()

    companies = list((await session.execute(select(Company))).scalars().all())
    refreshed_companies = 0
    for company in companies:
        cache = company.enrichment_cache if isinstance(company.enrichment_cache, dict) else {}
        cache.pop("committee_coverage", None)
        cache.pop("prospecting_priorities", None)
        company.enrichment_cache = cache or None
        refresh_company_prospecting_fields(company, contacts=[])
        session.add(company)
        refreshed_companies += 1
    await session.commit()

    return {
        "contacts_deleted": _count_deleted(contact_result),
        "outreach_sequences_deleted": _count_deleted(sequence_result),
        "activities_deleted": _count_deleted(activity_result),
        "reminders_deleted": _count_deleted(reminders_result),
        "companies_refreshed": refreshed_companies,
    }


async def reset_workspace_data(session: AsyncSession) -> dict[str, int]:
    # Delete leaf/child tables first to avoid FK constraint violations.
    # Order: child rows → parent rows.
    task_comments_result = await session.execute(delete(TaskComment))
    tasks_result = await session.execute(delete(Task))
    deal_contacts_result = await session.execute(delete(DealContact))
    milestones_result = await session.execute(delete(CompanyStageMilestone))
    angel_mappings_result = await session.execute(delete(AngelMapping))
    custom_demos_result = await session.execute(delete(CustomDemo))
    meetings_result = await session.execute(delete(Meeting))
    signals_result = await session.execute(delete(Signal))
    activities_result = await session.execute(delete(Activity))
    sequences_result = await session.execute(delete(OutreachSequence))
    reminders_result = await session.execute(delete(Reminder))
    deals_result = await session.execute(delete(Deal))
    contacts_result = await session.execute(delete(Contact))
    companies_result = await session.execute(delete(Company))
    batches_result = await session.execute(delete(SourcingBatch))
    battlecards_result = await session.execute(delete(Battlecard))
    await session.commit()

    return {
        "companies_deleted": _count_deleted(companies_result),
        "contacts_deleted": _count_deleted(contacts_result),
        "deals_deleted": _count_deleted(deals_result),
        "activities_deleted": _count_deleted(activities_result),
        "outreach_sequences_deleted": _count_deleted(sequences_result),
        "reminders_deleted": _count_deleted(reminders_result),
        "signals_deleted": _count_deleted(signals_result),
        "meetings_deleted": _count_deleted(meetings_result),
        "custom_demos_deleted": _count_deleted(custom_demos_result),
        "batches_deleted": _count_deleted(batches_result),
        "battlecards_deleted": _count_deleted(battlecards_result),
        "tasks_deleted": _count_deleted(tasks_result),
        "task_comments_deleted": _count_deleted(task_comments_result),
        "deal_contacts_deleted": _count_deleted(deal_contacts_result),
        "milestones_deleted": _count_deleted(milestones_result),
        "angel_mappings_deleted": _count_deleted(angel_mappings_result),
    }
