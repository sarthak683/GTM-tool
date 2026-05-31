"""Deal repository — board queries, contact management, cascade-delete."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.activity import Activity
from app.models.company_stage_milestone import CompanyStageMilestone
from app.models.contact import Contact
from app.models.company import Company
from app.models.deal import (
    ALL_STAGES, Deal, DealContact, DealContactRead, DealRead,
    compute_meddpicc_score,
)
from app.models.user import User
from app.repositories.base import BaseRepository
from app.services.deal_flags import compute_deal_flags


# Local-part tokens that mark a non-human / marketing / transactional sender.
# Kept self-contained here (rather than importing the email-sync module, which
# pulls in Celery tasks) so the engagement signal is defended at read time even
# for emails that were attached by older code before the sync-side fix landed.
_NOISE_EMAIL_LOCAL_PARTS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "postmaster", "bounce", "notification", "notifications", "newsletter",
    "marketing", "campaign", "broadcast", "register", "registration",
    "reservations", "events", "event", "updates", "news", "billing",
    "invoice", "receipts", "accounts", "techcomms", "unsubscribe", "alerts",
}
_NOISE_SUBDOMAIN_PREFIXES = {
    "email", "emails", "mail", "mailer", "mailing", "e", "em", "send",
    "sending", "sender", "smtp", "mg", "sg", "news", "newsletter",
    "marketing", "mkt", "campaign", "campaigns", "reply", "replies",
    "bounce", "bounces", "notify", "notification", "notifications", "click",
    "clicks", "link", "links", "list", "lists", "cmail", "ccsend", "events",
}


def _is_noise_email_from(addr: str | None) -> bool:
    """True when an email sender is a bulk/automated/marketing source.

    Mirrors the personal-email-sync bulk heuristic: the FROM local part is a
    known non-human token, or the FROM domain is a dedicated ESP sending
    subdomain (3+ labels, ESP-style head label). Such a sender is never a real
    buyer 'client touch' nor a rep 'seller touch'.
    """
    addr = (addr or "").strip().lower()
    if "@" not in addr:
        return False
    local, _, domain = addr.partition("@")
    if local in _NOISE_EMAIL_LOCAL_PARTS:
        return True
    if any(m in local for m in ("noreply", "no-reply", "donotreply", "mailer-daemon", "newsletter")):
        return True
    labels = [p for p in domain.split(".") if p]
    if len(labels) >= 3:
        head = labels[0]
        if head in _NOISE_SUBDOMAIN_PREFIXES or re.fullmatch(r"(e|em|t|tp|mg|sg|p|cm|mta)\d+", head):
            return True
    return False


def _apply_flag_fields(read: DealRead, qualification: Any) -> None:
    """Populate the flag-matrix fields on a DealRead from raw qualification JSONB."""
    summary = compute_deal_flags(qualification)
    read.flags = summary["flags"]
    read.forecast_category = summary["forecast_category"]
    read.flag_green_count = summary["green_count"]
    read.flag_yellow_count = summary["yellow_count"]
    read.flag_red_count = summary["red_count"]
    read.flag_blockers = summary["flag_blockers"]
    read.flag_yellows = summary["flag_yellows"]


class DealRepository(BaseRepository[Deal]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Deal, session)

    @staticmethod
    def _slugify_alias(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
        return slug or "deal"

    async def generate_unique_email_cc_alias(self, name: str, *, exclude_id: UUID | None = None) -> str:
        base = self._slugify_alias(name)
        candidate = base
        suffix = 2

        while True:
            stmt = select(Deal.id).where(Deal.email_cc_alias == candidate)
            if exclude_id:
                stmt = stmt.where(Deal.id != exclude_id)
            existing = (await self.session.execute(stmt)).first()
            if not existing:
                return candidate
            candidate = f"{base}-{suffix}"
            suffix += 1

    async def create(self, data: dict) -> Deal:
        if not data.get("email_cc_alias"):
            data["email_cc_alias"] = await self.generate_unique_email_cc_alias(str(data.get("name") or "deal"))
        return await super().create(data)

    async def update(self, obj: Deal, data: dict) -> Deal:
        if "email_cc_alias" in data and data["email_cc_alias"]:
            normalized = self._slugify_alias(str(data["email_cc_alias"]))
            if normalized != obj.email_cc_alias:
                data["email_cc_alias"] = await self.generate_unique_email_cc_alias(normalized, exclude_id=obj.id)
            else:
                data["email_cc_alias"] = normalized
        return await super().update(obj, data)

    @staticmethod
    def _email_domain(value: str | None) -> str:
        email = (value or "").strip().lower()
        if "@" not in email:
            return ""
        return email.split("@", 1)[1]

    @classmethod
    def _is_internal_email(cls, value: str | None) -> bool:
        domain = cls._email_domain(value)
        shared_domain = cls._email_domain(settings.GMAIL_SHARED_INBOX)
        return bool(domain and domain in {"beacon.li", shared_domain})

    @classmethod
    def _is_seller_touch(cls, row) -> bool:
        if row.type == "email":
            return cls._is_internal_email(row.email_from) and not _is_noise_email_from(row.email_from)
        return row.type in {"call", "meeting", "transcript", "note"} or row.source in {"aircall", "tldv", "google_calendar"}

    @classmethod
    def _is_client_touch(cls, row) -> bool:
        if row.type == "email":
            # A non-internal sender alone isn't enough — marketing/event/no-reply
            # blasts (e.g. terrapinn@tp2.terrapinn.com) are non-internal too. Only
            # count an inbound email as genuine client engagement when it's from a
            # real human sender, not a bulk/automated source.
            return (
                bool(row.email_from)
                and not cls._is_internal_email(row.email_from)
                and not _is_noise_email_from(row.email_from)
            )
        # A rep-logged manual call is the seller's OUTBOUND action (it already
        # counts as a seller touch) — not the buyer engaging. Don't let it light
        # up the "Client" engagement card as if the buyer initiated contact.
        # Meetings/transcripts (buyer was present) and Aircall records still count.
        if row.type == "call" and (row.source or "").strip().lower() == "manual":
            return False
        return bool(row.contact_id) or row.type in {"call", "meeting", "transcript"}

    # Friendly source names for the engagement tooltip — so reps see "via
    # tl;dv" / "via Aircall" instead of raw enum values.
    _FRIENDLY_SOURCE = {
        "tldv": "tl;dv",
        "aircall": "Aircall",
        "google_calendar": "Google Calendar",
        "gmail": "Gmail",
        "personal_email_sync": "Gmail",
        "instantly": "Instantly",
        "manual": "manual log",
        "instantly_sync": "Instantly",
    }

    @staticmethod
    def _clean_snippet(value: str | None, *, limit: int = 160) -> str:
        """Collapse whitespace and truncate to a tooltip-sized, case-preserved
        snippet of what actually happened."""
        if not value:
            return ""
        text = " ".join(str(value).split())
        if len(text) > limit:
            text = text[: limit - 1].rstrip() + "…"
        return text

    @classmethod
    def _signal_detail(cls, row) -> str:
        """A short human snippet of what happened — AI summary first (richest,
        e.g. a call/meeting recap), then free-text content, then email subject."""
        for value in (
            getattr(row, "ai_summary", None),
            getattr(row, "content", None),
            getattr(row, "email_subject", None),
        ):
            snippet = cls._clean_snippet(value)
            if snippet:
                return snippet
        return ""

    @classmethod
    def _signal_label(cls, row) -> dict:
        """Return a human-readable signal object for an activity row."""
        activity_type = row.type or "activity"
        source = row.source or ""

        if activity_type == "email":
            sender = (row.email_from or "").strip()
            label = f"Email · {sender}" if sender else "Email"
        elif activity_type == "call":
            if source == "aircall":
                label = "Aircall · call"
            else:
                label = "Call"
        elif activity_type == "meeting":
            if source == "google_calendar":
                label = "Calendar · meeting"
            else:
                label = "Meeting"
        elif activity_type == "transcript":
            if source == "tldv":
                label = "tl;dv · transcript"
            else:
                label = "Call transcript"
        elif activity_type == "note":
            label = "Note logged"
        else:
            label = activity_type.replace("_", " ").capitalize()

        return {
            "type": activity_type,
            "source": source,
            "label": label,
            # Friendly source + the actual content snippet, so the tooltip can
            # show what really happened instead of a tautological "it's recent".
            "source_label": cls._FRIENDLY_SOURCE.get(str(source).strip().lower(), ""),
            "detail": cls._signal_detail(row),
        }

    @staticmethod
    def _normalize_signal_text(value: str | None) -> str:
        return (value or "").strip().lower()

    @classmethod
    def _metadata_text(cls, metadata: Any) -> list[str]:
        if not isinstance(metadata, dict):
            return []
        collected: list[str] = []
        for key in (
            "summary",
            "content",
            "text",
            "transcription",
            "thread_latest_message_text",
            "thread_context_excerpt",
            "google_doc_transcript",
            "follow_up_email_draft",
        ):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                collected.append(cls._normalize_signal_text(value))

        for entry in metadata.get("topics") or []:
            if isinstance(entry, dict):
                value = entry.get("name") or entry.get("label") or entry.get("topic")
            else:
                value = entry
            if isinstance(value, str) and value.strip():
                collected.append(cls._normalize_signal_text(value))

        for entry in metadata.get("action_items") or metadata.get("items") or []:
            if isinstance(entry, dict):
                value = entry.get("text") or entry.get("title") or entry.get("content")
            else:
                value = entry
            if isinstance(value, str) and value.strip():
                collected.append(cls._normalize_signal_text(value))

        ci = metadata.get("conversation_intelligence")
        if isinstance(ci, dict):
            for key in ("summary", "transcription"):
                value = ci.get(key)
                if isinstance(value, str) and value.strip():
                    collected.append(cls._normalize_signal_text(value))
            for bucket in ("topics", "action_items", "sentiments"):
                for entry in ci.get(bucket) or []:
                    if isinstance(entry, str) and entry.strip():
                        collected.append(cls._normalize_signal_text(entry))
        return collected

    @classmethod
    def _activity_signal_text(cls, row) -> str:
        parts = [
            cls._normalize_signal_text(getattr(row, "ai_summary", None)),
            cls._normalize_signal_text(getattr(row, "content", None)),
            cls._normalize_signal_text(getattr(row, "email_subject", None)),
            cls._normalize_signal_text(getattr(row, "email_from", None)),
            *cls._metadata_text(getattr(row, "event_metadata", None)),
        ]
        return " ".join(part for part in parts if part)

    @staticmethod
    def _contains_any(text: str, terms: tuple[str, ...] | list[str]) -> bool:
        return any(term in text for term in terms)

    @classmethod
    def _infer_engagement_reason(cls, rows: list[Any], *, side: str) -> str | None:
        if not rows:
            return None

        recent = sorted(rows, key=lambda row: row.created_at, reverse=True)[:6]
        latest = recent[0]
        latest_source = (latest.source or "").strip().lower()
        latest_type = (latest.type or "").strip().lower()
        text = " ".join(cls._activity_signal_text(row) for row in recent)

        pricing_terms = ("pricing", "commercial", "quote", "proposal", "budget", "package")
        poc_terms = ("poc", "pilot", "proof of concept")
        meeting_terms = ("meeting", "demo", "workshop", "discovery", "next steps", "follow-up")
        security_terms = ("security", "legal", "procurement", "msa", "dpa", "infosec")
        positive_terms = ("agreed", "approved", "move forward", "confirmed", "interested", "ready")

        if side == "client":
            if cls._contains_any(text, poc_terms) and cls._contains_any(text, positive_terms):
                return "Client is moving forward on POC"
            if cls._contains_any(text, pricing_terms):
                return "Client is engaging on pricing"
            if cls._contains_any(text, security_terms):
                return "Client is discussing security or legal"
            if latest_source == "instantly" and cls._contains_any(text, ("reply", "replied", "interested")):
                return "Prospect replied to outreach"
            if latest_source in {"tldv", "aircall"} or latest_type in {"call", "meeting", "transcript"}:
                return "Client conversation captured recent next steps"
            if latest_type == "email":
                return "Client replied on the conversation"
            return "Client activity is recent"

        if cls._contains_any(text, poc_terms) and cls._contains_any(text, positive_terms):
            return "Rep is advancing POC next steps"
        if cls._contains_any(text, pricing_terms):
            return "Rep is working the pricing thread"
        if cls._contains_any(text, security_terms):
            return "Rep is handling security or legal follow-up"
        if latest_source == "instantly":
            if cls._contains_any(text, ("reply", "replied", "interested")):
                return "Rep outreach has live buyer engagement"
            return "Rep sequence is active"
        if latest_source in {"tldv", "aircall"} or latest_type in {"call", "meeting", "transcript"}:
            return "Rep has fresh conversation intel"
        if latest_type == "email":
            return "Rep sent a recent follow-up"
        if latest_type == "note":
            return "Rep logged a recent deal update"
        return "Rep activity is recent"

    async def _build_engagement_maps(
        self, deal_ids: list[UUID]
    ) -> tuple[
        dict[UUID, datetime],
        dict[UUID, datetime],
        dict[UUID, dict],
        dict[UUID, dict],
        dict[UUID, str],
        dict[UUID, str],
    ]:
        if not deal_ids:
            return {}, {}, {}, {}, {}, {}

        activity_rows = (
            await self.session.execute(
                select(
                    Activity.deal_id,
                    Activity.created_at,
                    Activity.type,
                    Activity.source,
                    Activity.email_from,
                    Activity.contact_id,
                    Activity.email_subject,
                    Activity.content,
                    Activity.ai_summary,
                    Activity.event_metadata,
                    Activity.call_outcome,
                ).where(
                    Activity.deal_id.in_(deal_ids)
                )
            )
        ).all()

        seller_engagement: dict[UUID, datetime] = {}
        client_engagement: dict[UUID, datetime] = {}
        seller_signal: dict[UUID, dict] = {}
        client_signal: dict[UUID, dict] = {}
        seller_rows: dict[UUID, list[Any]] = {}
        client_rows: dict[UUID, list[Any]] = {}
        for row in activity_rows:
            if not row.deal_id:
                continue
            if self._is_seller_touch(row):
                seller_rows.setdefault(row.deal_id, []).append(row)
                current = seller_engagement.get(row.deal_id)
                if current is None or row.created_at > current:
                    seller_engagement[row.deal_id] = row.created_at
                    seller_signal[row.deal_id] = self._signal_label(row)
            if self._is_client_touch(row):
                client_rows.setdefault(row.deal_id, []).append(row)
                current = client_engagement.get(row.deal_id)
                if current is None or row.created_at > current:
                    client_engagement[row.deal_id] = row.created_at
                    client_signal[row.deal_id] = self._signal_label(row)
        seller_reason = {
            deal_id: self._infer_engagement_reason(rows, side="seller")
            for deal_id, rows in seller_rows.items()
        }
        client_reason = {
            deal_id: self._infer_engagement_reason(rows, side="client")
            for deal_id, rows in client_rows.items()
        }
        for deal_id, reason in seller_reason.items():
            if deal_id in seller_signal and reason:
                seller_signal[deal_id]["reason"] = reason
        for deal_id, reason in client_reason.items():
            if deal_id in client_signal and reason:
                client_signal[deal_id]["reason"] = reason
        return seller_engagement, client_engagement, seller_signal, client_signal, seller_reason, client_reason

    # ── Board query ──────────────────────────────────────────────────────────

    async def board(self, pipeline_type: str = "deal") -> dict[str, list[DealRead]]:
        """Return deals grouped by stage, with company_name, assigned_rep_name, contact_count."""
        contact_count_sub = (
            select(
                DealContact.deal_id,
                func.count().label("cnt"),
            )
            .group_by(DealContact.deal_id)
            .subquery()
        )

        stmt = (
            select(
                Deal,
                Company.name.label("company_name"),
                User.name.label("assigned_rep_name"),
                func.coalesce(contact_count_sub.c.cnt, 0).label("contact_count"),
            )
            .outerjoin(Company, Deal.company_id == Company.id)
            .outerjoin(User, Deal.assigned_to_id == User.id)
            .outerjoin(contact_count_sub, Deal.id == contact_count_sub.c.deal_id)
            .where(Deal.pipeline_type == pipeline_type)
            .order_by(Deal.close_date_est.asc().nulls_last(), Deal.created_at.desc())
        )

        result = await self.session.execute(stmt)
        rows = result.all()
        seller_engagement, client_engagement, seller_signal, client_signal, seller_reason, client_reason = await self._build_engagement_maps([deal.id for deal, *_ in rows if deal.id])

        board: dict[str, list[DealRead]] = {}
        now = datetime.utcnow()
        for deal, company_name, rep_name, cc in rows:
            read = DealRead.model_validate(deal)
            read.company_name = company_name
            read.assigned_rep_name = rep_name
            read.contact_count = cc or 0
            read.meddpicc_score = compute_meddpicc_score(deal.qualification)
            _apply_flag_fields(read, deal.qualification)
            read.seller_engagement_at = seller_engagement.get(deal.id)
            read.client_engagement_at = client_engagement.get(deal.id)
            read.seller_engagement_signal = seller_signal.get(deal.id)
            read.client_engagement_signal = client_signal.get(deal.id)
            read.seller_engagement_reason = seller_reason.get(deal.id)
            read.client_engagement_reason = client_reason.get(deal.id)
            # Compute days_in_stage live so reps always see real-time staleness
            if deal.stage_entered_at:
                read.days_in_stage = (now - deal.stage_entered_at).days
            elif deal.created_at:
                read.days_in_stage = (now - deal.created_at).days
            board.setdefault(deal.stage, []).append(read)

        return board

    # ── Single deal with joins ───────────────────────────────────────────────

    async def get_with_joins(self, deal_id: UUID) -> Optional[DealRead]:
        contact_count_sub = (
            select(
                DealContact.deal_id,
                func.count().label("cnt"),
            )
            .where(DealContact.deal_id == deal_id)
            .group_by(DealContact.deal_id)
            .subquery()
        )

        stmt = (
            select(
                Deal,
                Company.name.label("company_name"),
                User.name.label("assigned_rep_name"),
                func.coalesce(contact_count_sub.c.cnt, 0).label("contact_count"),
            )
            .outerjoin(Company, Deal.company_id == Company.id)
            .outerjoin(User, Deal.assigned_to_id == User.id)
            .outerjoin(contact_count_sub, Deal.id == contact_count_sub.c.deal_id)
            .where(Deal.id == deal_id)
        )

        row = (await self.session.execute(stmt)).first()
        if not row:
            return None

        deal, company_name, rep_name, cc = row
        seller_engagement, client_engagement, seller_signal, client_signal, seller_reason, client_reason = await self._build_engagement_maps([deal.id])
        read = DealRead.model_validate(deal)
        read.company_name = company_name
        read.assigned_rep_name = rep_name
        read.contact_count = cc or 0
        read.meddpicc_score = compute_meddpicc_score(deal.qualification)
        _apply_flag_fields(read, deal.qualification)
        read.seller_engagement_at = seller_engagement.get(deal.id)
        read.client_engagement_at = client_engagement.get(deal.id)
        read.seller_engagement_signal = seller_signal.get(deal.id)
        read.client_engagement_signal = client_signal.get(deal.id)
        read.seller_engagement_reason = seller_reason.get(deal.id)
        read.client_engagement_reason = client_reason.get(deal.id)
        # Compute days_in_stage live
        if deal.stage_entered_at:
            read.days_in_stage = (datetime.utcnow() - deal.stage_entered_at).days
        elif deal.created_at:
            read.days_in_stage = (datetime.utcnow() - deal.created_at).days
        return read

    # ── Contact management ───────────────────────────────────────────────────

    async def list_contacts(self, deal_id: UUID) -> list[DealContactRead]:
        stmt = (
            select(DealContact, Contact)
            .join(Contact, DealContact.contact_id == Contact.id)
            .where(DealContact.deal_id == deal_id)
            .order_by(DealContact.added_at.desc())
        )
        rows = (await self.session.execute(stmt)).all()
        result = []
        for dc, contact in rows:
            result.append(DealContactRead(
                deal_id=dc.deal_id,
                contact_id=dc.contact_id,
                role=dc.role,
                added_at=dc.added_at,
                first_name=contact.first_name,
                last_name=contact.last_name,
                email=contact.email,
                title=contact.title,
                persona=contact.persona,
            ))
        return result

    async def add_contact(self, deal_id: UUID, contact_id: UUID, role: Optional[str] = None) -> DealContact:
        dc = DealContact(deal_id=deal_id, contact_id=contact_id, role=role, added_at=datetime.utcnow())
        self.session.add(dc)
        await self.session.commit()
        await self.session.refresh(dc)
        return dc

    async def remove_contact(self, deal_id: UUID, contact_id: UUID) -> bool:
        stmt = select(DealContact).where(
            DealContact.deal_id == deal_id,
            DealContact.contact_id == contact_id,
        )
        dc = (await self.session.execute(stmt)).scalar_one_or_none()
        if not dc:
            return False
        await self.session.delete(dc)
        await self.session.commit()
        return True

    # ── Cascade delete ───────────────────────────────────────────────────────

    async def delete_with_cascade(self, deal_id: UUID) -> None:
        """Delete deal, its activities, contact links, and dependent milestones."""
        for act in (
            await self.session.execute(
                select(Activity).where(Activity.deal_id == deal_id)
            )
        ).scalars().all():
            await self.session.delete(act)

        for milestone in (
            await self.session.execute(
                select(CompanyStageMilestone).where(CompanyStageMilestone.deal_id == deal_id)
            )
        ).scalars().all():
            await self.session.delete(milestone)

        for dc in (
            await self.session.execute(
                select(DealContact).where(DealContact.deal_id == deal_id)
            )
        ).scalars().all():
            await self.session.delete(dc)

        deal = await self.get(deal_id)
        if deal:
            await self.session.delete(deal)

        await self.session.commit()
