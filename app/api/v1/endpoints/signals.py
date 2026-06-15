from typing import List
from uuid import UUID

from fastapi import APIRouter

from app.core.dependencies import CurrentUser, DBSession
from app.models.signal import Signal, SignalCreate, SignalRead
from app.repositories.company import CompanyRepository
from app.repositories.signal import SignalRepository

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/company/{company_id}", response_model=List[SignalRead])
async def get_company_signals(company_id: UUID, session: DBSession, current_user: CurrentUser):
    repo = SignalRepository(session)
    return await repo.list(
        Signal.company_id == company_id,
        order_by=Signal.created_at.desc(),
    )


@router.post("/company/{company_id}/refresh")
async def refresh_company_signals(company_id: UUID, session: DBSession, current_user: CurrentUser):
    """Re-fetch buying signals from Google News RSS. Deduplicates by title."""
    company = await CompanyRepository(session).get_or_raise(company_id)

    from app.clients.news import NewsClient
    news_data = await NewsClient().get_company_signals(company.name, company.domain) or {}

    repo = SignalRepository(session)
    created = 0

    all_articles = (
        [(a, "funding") for a in news_data.get("funding_signals", [])]
        + [(a, "pr") for a in news_data.get("pr_signals", [])]
        + [(a, "news") for a in news_data.get("articles", [])]
    )

    for article, sig_type in all_articles:
        title = article.get("title") or ""
        if not title or await repo.exists_by_title(company_id, title):
            continue
        signal = Signal(
            company_id=company_id,
            signal_type=sig_type,
            source="google_news",
            title=title,
            url=article.get("url"),
            summary=article.get("summary") or article.get("description"),
            published_at=article.get("published_at"),
        )
        session.add(signal)
        created += 1

    await session.commit()
    return {"company_id": str(company_id), "signals_created": created}


@router.post("/", response_model=SignalRead, status_code=201)
async def create_signal(payload: SignalCreate, session: DBSession, current_user: CurrentUser):
    return await SignalRepository(session).create(payload.model_dump())


@router.delete("/{signal_id}", status_code=204)
async def delete_signal(signal_id: UUID, session: DBSession, current_user: CurrentUser):
    repo = SignalRepository(session)
    signal = await repo.get_or_raise(signal_id)
    await repo.delete(signal)
