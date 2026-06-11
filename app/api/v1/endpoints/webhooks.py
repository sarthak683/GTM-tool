"""
Webhook receiver endpoints (Instantly, Fireflies, RB2B).

Each handler creates an Activity record and updates contact/sequence status.

Instantly webhook events handled:
  email_sent          → log activity
  email_opened        → log activity
  email_link_clicked  → log activity
  email_bounced       → mark contact email invalid, log activity
  reply_received      → update sequence to "replied", log activity with reply content
  lead_unsubscribed   → update contact, log activity
  lead_interested     → update contact label, log activity
  lead_not_interested → update contact label, log activity
  lead_meeting_booked → update sequence_status to "meeting_booked", log activity
"""
import asyncio
import hashlib
import hmac
import logging
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, literal, or_
from sqlmodel import select

logger = logging.getLogger(__name__)

from app.core.dependencies import DBSession
from app.clients.aircall import AircallClient, AircallError
from app.config import settings
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.deal import Deal, DealContact
from app.models.outreach import OutreachSequence
from app.services.tasks import refresh_system_tasks_for_entity
from app.services.tldv_sync import sync_tldv_meeting

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ── Auth / idempotency helpers ────────────────────────────────────────────────

_WEBHOOK_SECRET_HEADER = "x-beacon-webhook-secret"


def _verify_webhook_secret(request: Request, expected: str, source: str = "webhook") -> None:
    """Constant-time compare of the shared-secret header.

    Behaviour by configuration:
      * `expected` is set → the request must carry a matching
        X-Beacon-Webhook-Secret header (constant-time compare); mismatch or
        missing header is rejected with 401.
      * `expected` is empty/unset and REQUIRE_WEBHOOK_SECRETS is True →
        fail-closed: reject with 401 because the source cannot be verified.
      * `expected` is empty/unset and REQUIRE_WEBHOOK_SECRETS is False →
        allow (so local/dev keeps working) but emit a warning so the
        unverified source is visible in logs.
    """
    if not expected:
        if settings.REQUIRE_WEBHOOK_SECRETS:
            logger.warning(
                "Rejecting %s webhook: no shared secret configured and "
                "REQUIRE_WEBHOOK_SECRETS is enabled.",
                source,
            )
            raise HTTPException(status_code=401, detail="Webhook secret not configured")
        logger.warning(
            "%s webhook is UNVERIFIED: no shared secret configured. Set the "
            "corresponding *_WEBHOOK_SECRET to authenticate this source.",
            source,
        )
        return
    provided = request.headers.get(_WEBHOOK_SECRET_HEADER, "") or ""
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


async def _already_processed_external_event(
    session, *, source: str, external_id: str | None
) -> bool:
    """Return True if an Activity with this (source, external_source_id) already exists."""
    if not external_id:
        return False
    existing = (
        await session.execute(
            select(Activity.id).where(
                Activity.source == source,
                Activity.external_source_id == external_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    return existing is not None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _most_recent_active_deal(session) -> Optional[Deal]:
    result = await session.execute(
        select(Deal)
        .where(Deal.stage.not_in(["closed_won", "closed_lost"]))
        .order_by(Deal.last_activity_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _find_contact_by_email(session, email: str) -> Optional[Contact]:
    """Look up a contact by email address — used to match Instantly events."""
    if not email:
        return None
    result = await session.execute(
        select(Contact).where(Contact.email == email.lower().strip()).limit(1)
    )
    return result.scalar_one_or_none()


async def _find_sequence_by_campaign(
    session, campaign_id: str, contact_id: Optional[UUID]
) -> Optional[OutreachSequence]:
    """Find the outreach sequence for a given Instantly campaign + contact."""
    if not campaign_id:
        return None
    query = select(OutreachSequence).where(
        OutreachSequence.instantly_campaign_id == campaign_id
    )
    if contact_id:
        query = query.where(OutreachSequence.contact_id == contact_id)
    result = await session.execute(query.limit(1))
    return result.scalar_one_or_none()


async def _create_activity(
    session,
    *,
    type_: str,
    source: str,
    content: str,
    payload: dict,
    deal_id: Optional[UUID] = None,
    contact_id: Optional[UUID] = None,
    external_source: Optional[str] = None,
    external_source_id: Optional[str] = None,
    medium: Optional[str] = None,
) -> Activity:
    activity = Activity(
        type=type_,
        source=source,
        content=content,
        medium=medium,
        event_metadata=payload,
        deal_id=deal_id,
        contact_id=contact_id,
        external_source=external_source or source,
        external_source_id=external_source_id,
    )
    session.add(activity)

    if deal_id:
        deal = await session.get(Deal, deal_id)
        if deal:
            deal.last_activity_at = datetime.utcnow()
            session.add(deal)

    await session.commit()
    await session.refresh(activity)
    return activity


def _clean_phone(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


async def _find_contact_by_phone(session, phone: str | None) -> Optional[Contact]:
    clean = _clean_phone(phone)
    if not clean:
        return None

    # Suffix match on the last 9 digits — covers country-code variations.
    # Push the matching into Postgres instead of materializing every
    # phone-bearing contact and looping in Python: strip non-digits with
    # regexp_replace, then reproduce the exact three OR-conditions the old
    # Python used (full equality; candidate ends with incoming's last-9;
    # incoming ends with candidate's last-9). `right()` mirrors Python's [-9:]
    # slice (returns the whole string when the number is shorter than 9).
    suffix = clean[-9:]
    # Candidate digits, with non-digits stripped — equivalent to _clean_phone().
    cand_digits = func.regexp_replace(Contact.phone, r"\D", "", "g")
    cand_last9 = func.right(cand_digits, 9)
    result = await session.execute(
        select(Contact)
        .where(
            Contact.phone.isnot(None),
            cand_digits != "",  # mirror Python's `if not candidate_clean: continue`
            or_(
                cand_digits == clean,                                  # candidate_clean == clean
                func.right(cand_digits, len(suffix)) == suffix,        # candidate_clean.endswith(suffix)
                func.right(literal(clean), func.length(cand_last9))    # clean.endswith(candidate_clean[-9:])
                == cand_last9,
            ),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _find_best_deal_for_contact(session, contact_id: Optional[UUID]) -> Optional[Deal]:
    if not contact_id:
        return None

    result = await session.execute(
        select(Deal)
        .join(DealContact, DealContact.deal_id == Deal.id)
        .where(DealContact.contact_id == contact_id)
        .order_by(
            Deal.stage.not_in(["closed_won", "closed_lost"]).desc(),
            Deal.last_activity_at.desc().nullslast(),
            Deal.updated_at.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


_AIRCALL_CI_EVENTS = {
    "summary.created",
    "topics.created",
    "transcription.created",
    "action_item.created",
    "sentiment.created",
}
# Map each CI event to the single CI signal it announces, so we can fetch only
# that one endpoint instead of all five on every event. The key matches the
# `labels` used in _fetch_aircall_bundle / the keys stored in the ci bundle.
_AIRCALL_CI_EVENT_SIGNAL = {
    "summary.created": "summary",
    "topics.created": "topics",
    "transcription.created": "transcription",
    "action_item.created": "action_items",
    "sentiment.created": "sentiments",
}
_AIRCALL_CI_SIGNALS = ("summary", "topics", "transcription", "action_items", "sentiments")
_AIRCALL_CALL_ENRICH_EVENTS = {
    "call.ended",
    "call.commented",
    "call.tagged",
    "call.voicemail_left",
    "call.comm_assets_generated",
}
_AIRCALL_NON_FATAL_STATUS_CODES = {403, 404}


def _hash_signature(*parts: Any) -> str:
    raw = "|".join(str(part or "").strip() for part in parts if str(part or "").strip())
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12] if raw else ""


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_flatten_text(item) for item in value]
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        parts: list[str] = []
        if isinstance(value.get("utterances"), list):
            utterance_parts = [
                str(item.get("text") or "").strip()
                for item in value.get("utterances") or []
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ]
            if utterance_parts:
                parts.append(" ".join(utterance_parts))
        for key in (
            "content",
            "text",
            "summary",
            "topic",
            "label",
            "feedback",
            "name",
        ):
            part = _flatten_text(value.get(key))
            if part:
                parts.append(part)
        if isinstance(value.get("summary_template_results"), list):
            template_parts = [
                _flatten_text(item.get("content"))
                for item in value.get("summary_template_results") or []
                if isinstance(item, dict)
            ]
            parts.extend(part for part in template_parts if part)
        deduped = list(dict.fromkeys(part for part in parts if part))
        return " ".join(deduped).strip()
    return str(value).strip()


def _normalize_aircall_summary(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    summary_obj = payload.get("summary") if isinstance(payload.get("summary"), dict) else payload
    parts = []
    content = _flatten_text(summary_obj.get("content")) if isinstance(summary_obj, dict) else ""
    if content:
        parts.append(content)
    if isinstance(summary_obj, dict):
        for item in summary_obj.get("summary_template_results") or []:
            if not isinstance(item, dict):
                continue
            item_name = str(item.get("name") or "").strip()
            item_content = _flatten_text(item.get("content"))
            if item_name and item_content:
                parts.append(f"{item_name}: {item_content}")
            elif item_content:
                parts.append(item_content)
    deduped = list(dict.fromkeys(part for part in parts if part))
    return " ".join(deduped).strip() or None


def _normalize_aircall_topics(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    topic_obj = payload.get("topic") if isinstance(payload.get("topic"), dict) else payload
    raw_topics = []
    if isinstance(topic_obj, dict):
        raw_topics = topic_obj.get("content") or topic_obj.get("topics") or []
    elif isinstance(payload.get("topics"), list):
        raw_topics = payload.get("topics") or []
    normalized: list[str] = []
    for item in raw_topics:
        if isinstance(item, dict):
            value = item.get("name") or item.get("label") or item.get("content") or item.get("topic")
        else:
            value = item
        text = _flatten_text(value)
        if text:
            normalized.append(text)
    return list(dict.fromkeys(normalized))


def _normalize_aircall_action_items(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw_items = payload.get("action_items") or payload.get("items") or payload.get("content") or []
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    if isinstance(raw_items, str):
        raw_items = [raw_items]

    normalized: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            value = item.get("content") or item.get("text") or item.get("title")
        else:
            value = item
        text = _flatten_text(value)
        if text:
            normalized.append(text)
    return list(dict.fromkeys(normalized))


def _normalize_aircall_transcription(payload: Any) -> tuple[str | None, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return None, []
    transcription_obj = payload.get("transcription") if isinstance(payload.get("transcription"), dict) else payload
    content = transcription_obj.get("content") if isinstance(transcription_obj, dict) else None
    utterances = []
    if isinstance(content, dict):
        for item in content.get("utterances") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            utterances.append(
                {
                    "text": text,
                    "participant_type": item.get("participant_type"),
                    "user_id": item.get("user_id"),
                    "phone_number": item.get("phone_number"),
                    "start_time": item.get("start_time"),
                    "end_time": item.get("end_time"),
                }
            )
    transcript_text = _flatten_text(content or transcription_obj.get("content"))
    return transcript_text or None, utterances


def _normalize_aircall_sentiments(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw_sentiments = payload.get("sentiments") or payload.get("content") or payload.get("sentiment") or []
    if isinstance(raw_sentiments, dict):
        raw_sentiments = [raw_sentiments]
    if isinstance(raw_sentiments, str):
        raw_sentiments = [raw_sentiments]

    normalized: list[str] = []
    for item in raw_sentiments:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("name") or item.get("sentiment") or "").strip()
            score = item.get("score")
            if label and score is not None:
                normalized.append(f"{label}:{score}")
            elif label:
                normalized.append(label)
            else:
                text = _flatten_text(item)
                if text:
                    normalized.append(text)
        else:
            text = _flatten_text(item)
            if text:
                normalized.append(text)
    return list(dict.fromkeys(normalized))


def _build_aircall_ci_content(
    *,
    summary_text: str | None,
    topics: list[str],
    action_items: list[str],
    transcript_text: str | None,
    sentiments: list[str],
    recording_url: str | None,
    comment_text: str | None,
) -> str:
    lines = ["Aircall conversation intelligence is ready."]
    if summary_text:
        lines.append(f"Summary: {summary_text}")
    if topics:
        lines.append(f"Topics: {', '.join(topics[:6])}")
    if action_items:
        lines.append(f"Action items: {'; '.join(action_items[:4])}")
    if sentiments:
        lines.append(f"Sentiment: {', '.join(sentiments[:3])}")
    if comment_text:
        lines.append(f"Latest note: {comment_text[:180]}")
    if transcript_text:
        lines.append(f"Transcript: {transcript_text[:280]}")
    if recording_url:
        lines.append("Recording available.")
    return "\n".join(lines)


def _build_aircall_external_id(
    *,
    event: str,
    call_id: str | None,
    comment_text: str,
    tags: list[str],
    ci_bundle: dict[str, Any],
) -> str | None:
    if not call_id:
        return None
    if event in _AIRCALL_CI_EVENTS or event == "call.comm_assets_generated":
        return f"aircall:ci:{call_id}"
    if event == "call.commented":
        signature = _hash_signature(comment_text, *(ci_bundle.get("action_items") or []))
        return f"aircall:{event}:{call_id}:{signature or 'latest'}"
    if event == "call.tagged":
        signature = _hash_signature(*tags)
        return f"aircall:{event}:{call_id}:{signature or 'latest'}"
    return f"aircall:{event}:{call_id}"


async def _get_activity_by_external_id(
    session,
    *,
    external_source: str,
    external_source_id: str | None,
) -> Activity | None:
    if not external_source_id:
        return None
    result = await session.execute(
        select(Activity)
        .where(
            Activity.external_source == external_source,
            Activity.external_source_id == external_source_id,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _safe_aircall_fetch(label: str, call_id: int, coro) -> Any:
    try:
        return await coro
    except AircallError as exc:
        if exc.status_code not in _AIRCALL_NON_FATAL_STATUS_CODES:
            logger.warning("Aircall %s fetch failed for call_id=%s: %s", label, call_id, exc)
        return None
    except Exception as exc:  # pragma: no cover - defensive for provider payloads
        logger.warning("Aircall %s fetch crashed for call_id=%s: %s", label, call_id, exc)
        return None


def _merge_aircall_ci_bundle(
    base: dict[str, Any] | None, incoming: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge a freshly-fetched (possibly partial) CI bundle onto a stored one.

    All five CI events for a call share one Activity row (external id
    `aircall:ci:{call_id}`), so each late-arriving signal must layer onto the
    bundle already persisted instead of clobbering the others. Only non-empty
    incoming values overwrite; `raw` is merged key-by-key.
    """
    if not incoming:
        return dict(base) if base else {}
    merged: dict[str, Any] = dict(base) if base else {}
    for key in ("summary", "topics", "transcription", "utterances", "action_items", "sentiments"):
        value = incoming.get(key)
        if value:
            merged[key] = value
    incoming_raw = incoming.get("raw")
    if isinstance(incoming_raw, dict):
        merged_raw = dict(merged.get("raw") or {})
        for raw_key, raw_value in incoming_raw.items():
            if raw_value is not None:
                merged_raw[raw_key] = raw_value
        if merged_raw:
            merged["raw"] = merged_raw
    return merged


async def _fetch_aircall_bundle(
    client: AircallClient,
    call_id: int,
    event: str,
    *,
    ci_signals: tuple[str, ...] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Fetch call details and/or conversation-intelligence signals.

    `ci_signals` controls which CI endpoints are hit. When None, the set is
    derived from the event (one signal for a CI event, all five for
    `call.comm_assets_generated`). The returned ci_bundle only contains the
    fetched signals, so it can be merged onto an existing bundle without
    requiring the other four endpoints to be re-fetched per event.
    """
    fetched_call: dict[str, Any] | None = None
    ci_bundle: dict[str, Any] = {}

    should_fetch_call = event in _AIRCALL_CALL_ENRICH_EVENTS or event in _AIRCALL_CI_EVENTS

    if ci_signals is None:
        if event in _AIRCALL_CI_EVENTS:
            signal = _AIRCALL_CI_EVENT_SIGNAL.get(event)
            ci_signals = (signal,) if signal else ()
        elif event == "call.comm_assets_generated":
            ci_signals = _AIRCALL_CI_SIGNALS
        else:
            ci_signals = ()
    wanted = tuple(s for s in _AIRCALL_CI_SIGNALS if s in ci_signals)

    _ci_fetchers = {
        "summary": client.get_call_summary,
        "topics": client.get_call_topics,
        "transcription": client.get_call_transcription,
        "action_items": client.get_call_action_items,
        "sentiments": client.get_call_sentiments,
    }

    labels: list[str] = []
    coroutines = []
    if should_fetch_call:
        labels.append("call")
        coroutines.append(_safe_aircall_fetch("call", call_id, client.get_call(call_id)))
    for signal in wanted:
        labels.append(signal)
        coroutines.append(_safe_aircall_fetch(signal, call_id, _ci_fetchers[signal](call_id)))

    if not coroutines:
        return None, {}

    results = await asyncio.gather(*coroutines)
    result_map = dict(zip(labels, results))

    call_payload = result_map.get("call")
    if isinstance(call_payload, dict):
        fetched_call = call_payload.get("call") if isinstance(call_payload.get("call"), dict) else call_payload

    summary_text = _normalize_aircall_summary(result_map.get("summary")) if "summary" in result_map else None
    topics = _normalize_aircall_topics(result_map.get("topics")) if "topics" in result_map else []
    if "transcription" in result_map:
        transcript_text, utterances = _normalize_aircall_transcription(result_map.get("transcription"))
    else:
        transcript_text, utterances = None, []
    action_items = _normalize_aircall_action_items(result_map.get("action_items")) if "action_items" in result_map else []
    sentiments = _normalize_aircall_sentiments(result_map.get("sentiments")) if "sentiments" in result_map else []

    if summary_text or topics or transcript_text or action_items or sentiments:
        ci_bundle = {
            "summary": summary_text,
            "topics": topics,
            "transcription": transcript_text,
            "utterances": utterances,
            "action_items": action_items,
            "sentiments": sentiments,
            "raw": {
                key: result_map.get(key)
                for key in ("summary", "topics", "transcription", "action_items", "sentiments")
                if key in result_map
            },
        }

    return fetched_call, ci_bundle


async def _summarize_aircall_signal(*, transcript_text: str | None, comment_text: str | None, summary_text: str | None) -> str | None:
    if summary_text:
        return summary_text
    source_text = " ".join(part for part in [transcript_text or "", comment_text or ""] if part).strip()
    if len(source_text) < 40 or not settings.claude_api_key:
        return None

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.claude_api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": (
                    "Summarize this sales call in one short sentence (max 18 words). "
                    "Focus on buyer intent, risk, or next step.\n\n"
                    f"{source_text[:2500]}"
                ),
            }],
        )
        return response.content[0].text.strip()
    except Exception as exc:  # pragma: no cover - external API fallback
        logger.warning("Aircall call summary failed: %s", exc)
        return None


def _infer_aircall_outcome(
    *,
    event: str,
    missed_reason: str | None,
    voicemail_url: str | None,
    answered_at: Any,
    duration: Any,
) -> str | None:
    if event in {"call.missed"}:
        return "missed"
    if event in {"call.voicemail_left"}:
        return "voicemail"
    if missed_reason:
        return "missed"
    if voicemail_url and event in {"call.comm_assets_generated", "transcription.created", "summary.created", "topics.created", "action_item.created", "sentiment.created"}:
        return "voicemail"
    if event in {"call.answered"}:
        return "answered"
    if answered_at or (duration or 0) > 0:
        return "answered"
    # call.ended with duration=0 and no answered_at = no answer (outbound) or hung up before answer
    if event == "call.ended" and not answered_at and (duration or 0) == 0:
        return "missed"
    return None


# ── Instantly webhook ──────────────────────────────────────────────────────────

@router.post("/instantly")
async def instantly_webhook(request: Request, session: DBSession) -> dict:
    """
    Receive Instantly.ai webhook events and sync them into the CRM.

    Instantly sends the lead email in the payload — we use that to find
    the matching Contact and OutreachSequence, then update statuses and
    log an Activity.
    """
    _verify_webhook_secret(request, settings.INSTANTLY_WEBHOOK_SECRET, source="instantly")
    payload: Dict[str, Any] = await request.json()

    # ── Parse common fields from Instantly payload ─────────────────────────────
    # Instantly v2 uses snake_case event types; v1 used camelCase
    event_type = (
        payload.get("event_type")
        or payload.get("eventType")
        or "email_event"
    )
    lead_email = (
        payload.get("lead_email")
        or payload.get("to_email")
        or payload.get("email")
        or ""
    ).lower().strip()

    campaign_id = (
        payload.get("campaign_id")
        or payload.get("campaignId")
        or ""
    )
    subject = payload.get("subject") or payload.get("emailSubject") or "(no subject)"
    reply_body = payload.get("reply_text") or payload.get("body") or ""
    step_number = payload.get("step") or payload.get("step_number")

    # Instantly payloads include a unique event/message identifier; we use it
    # for idempotency so retried deliveries don't duplicate activities.
    event_external_id = str(
        payload.get("event_id")
        or payload.get("eventId")
        or payload.get("message_id")
        or payload.get("messageId")
        or ""
    ).strip() or None

    if event_external_id and await _already_processed_external_event(
        session, source="instantly", external_id=event_external_id
    ):
        return {
            "status": "duplicate",
            "event_type": event_type,
            "event_id": event_external_id,
        }

    # ── Find contact by email ──────────────────────────────────────────────────
    contact = await _find_contact_by_email(session, lead_email)
    contact_id = contact.id if contact else None

    # ── Find linked outreach sequence ──────────────────────────────────────────
    sequence = await _find_sequence_by_campaign(session, campaign_id, contact_id)

    # Only attach the activity to a deal if that deal actually links to this
    # contact. Previously we fell back to "most-recent-active-deal" workspace-wide,
    # which polluted unrelated deal timelines with every Instantly event.
    deal = await _find_best_deal_for_contact(session, contact_id) if contact_id else None
    deal_id = deal.id if deal else None

    # ── Backfill campaign linkage ─────────────────────────────────────────────
    if contact and campaign_id:
        if not contact.instantly_campaign_id:
            contact.instantly_campaign_id = campaign_id
            session.add(contact)
        if not sequence and contact.company_id:
            now_for_seq = datetime.utcnow()
            sequence = OutreachSequence(
                contact_id=contact.id,
                company_id=contact.company_id,
                persona=contact.persona,
                status="launched",
                instantly_campaign_id=campaign_id,
                instantly_campaign_status="active",
                launched_at=now_for_seq,
            )
            session.add(sequence)
            await session.flush()

    now = datetime.utcnow()

    email_subject = None
    email_sender = None
    email_recipient = None

    # ── Route by event type ────────────────────────────────────────────────────

    if event_type == "email_sent":
        step_note = f" (step {step_number})" if step_number else ""
        content = f"Email sent{step_note}: {subject} → {lead_email}"
        activity_type = "email"
        email_subject = subject
        email_sender = payload.get("email_account") or ""
        email_recipient = lead_email
        # Move the contact from "queued_instantly" to "sent" so the pipeline
        # board, progress tracker, and follow-up tasks see live sequence state.
        # Only advance if we haven't already moved past "sent" (reply, bounce, etc.)
        if contact and (contact.sequence_status or "") in {"queued_instantly", "ready", "", None}:
            contact.sequence_status = "sent"
            contact.instantly_status = "pushed"
            contact.updated_at = now
            session.add(contact)
        if sequence and (sequence.status or "") in {"launched", "", None}:
            sequence.status = "sent"
            sequence.updated_at = now
            session.add(sequence)

    elif event_type == "email_opened":
        content = f"Email opened: {subject} by {lead_email}"
        activity_type = "email"
        email_subject = subject
        email_sender = payload.get("email_account") or payload.get("from_email") or ""
        email_recipient = lead_email
        if contact:
            contact.email_open_count = (contact.email_open_count or 0) + 1
            contact.email_last_opened_at = now
            contact.updated_at = now
            session.add(contact)

    elif event_type in ("link_clicked", "email_link_clicked"):
        content = f"Link clicked in email: {subject} by {lead_email}"
        activity_type = "email"
        email_subject = subject
        email_sender = payload.get("email_account") or payload.get("from_email") or ""
        email_recipient = lead_email
        if contact:
            contact.email_click_count = (contact.email_click_count or 0) + 1
            contact.updated_at = now
            session.add(contact)

    elif event_type == "email_bounced":
        content = f"Email bounced: {lead_email} — {subject}"
        activity_type = "email"
        email_subject = subject
        email_sender = payload.get("email_account") or payload.get("from_email") or ""
        email_recipient = lead_email
        # Mark the contact's email as invalid
        if contact:
            contact.email_verified = False
            contact.instantly_status = "bounced"
            contact.sequence_status = "bounced"
            contact.updated_at = now
            session.add(contact)
        if sequence:
            sequence.instantly_campaign_status = "error"
            sequence.updated_at = now
            session.add(sequence)

    elif event_type == "reply_received":
        content = (
            f"Reply from {lead_email}: {subject}"
            + (f"\n\n{reply_body[:1000]}" if reply_body else "")
        )
        activity_type = "email"
        email_subject = subject
        email_sender = lead_email
        email_recipient = payload.get("email_account") or payload.get("to_email") or ""
        # Update contact + sequence to "replied"
        if contact:
            contact.sequence_status = "replied"
            contact.instantly_status = "replied"
            contact.updated_at = now
            session.add(contact)
        if sequence:
            sequence.status = "replied"
            sequence.instantly_campaign_status = "completed"
            sequence.updated_at = now
            session.add(sequence)

        # Claude-powered sentiment & intent classification so reps can
        # distinguish "yes, let's talk" from "remove me" without opening
        # the email. Persisted onto the Activity below via ai_summary +
        # event_metadata.reply_sentiment. Failures here are non-fatal —
        # the raw reply is still logged.
        try:
            from app.services.reply_sentiment import classify_reply

            verdict = await classify_reply(
                subject=subject,
                body=reply_body,
                sender=lead_email,
                prospect_title=contact.title if contact else None,
                company_name=None,
            )
            if verdict:
                # Stash on the payload so it lands in Activity.event_metadata
                payload = {**payload, "reply_sentiment": verdict}
                # Re-tag sequence_status if the classifier is confident —
                # catches Instantly's "positive reply" signal without waiting
                # for the rep to manually promote the status.
                if contact and verdict.get("intent") == "interested" and contact.sequence_status != "meeting_booked":
                    contact.sequence_status = "interested"
                    session.add(contact)
                if contact and verdict.get("intent") == "unsubscribe":
                    contact.sequence_status = "unsubscribed"
                    contact.instantly_status = "unsubscribed"
                    session.add(contact)
                # Bell notification: when the classifier sees a positive
                # reply expressing interest, suggest creating a Deal. The
                # rep accepts / dismisses from the navbar bell — we never
                # auto-create. Dedup on the message id so a re-delivered
                # webhook can't spawn duplicates.
                if (
                    contact
                    and verdict.get("sentiment") == "positive"
                    and verdict.get("intent") == "interested"
                    and event_external_id
                ):
                    assignee_id = contact.sdr_id or contact.assigned_to_id
                    if assignee_id:
                        try:
                            from app.services.notifications import create_notification

                            contact_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip() or (contact.email or "Prospect")
                            company_name = None
                            if contact.company_id:
                                from app.models.company import Company

                                co = (await session.execute(
                                    select(Company).where(Company.id == contact.company_id)
                                )).scalar_one_or_none()
                                if co:
                                    company_name = co.name
                            await create_notification(
                                session,
                                user_id=assignee_id,
                                type="meeting_booked_suggest_deal",
                                title=f"Positive reply from {contact_name}",
                                body=verdict.get("one_line") or "Prospect wants to talk — create a deal?",
                                action_payload={
                                    "contact_id": str(contact.id),
                                    "contact_name": contact_name,
                                    "company_id": str(contact.company_id) if contact.company_id else None,
                                    "company_name": company_name,
                                    "reply_summary": verdict.get("one_line"),
                                    "reply_intent": verdict.get("intent"),
                                    "reply_sentiment": verdict.get("sentiment"),
                                    "suggested_response": verdict.get("suggested_response"),
                                    "reply_event_id": event_external_id,
                                },
                                dedup_key=f"meeting_booked:{contact.id}:{event_external_id}",
                            )
                        except Exception as exc:
                            logger.warning("notification create failed for reply %s: %s", event_external_id, exc)
        except Exception as exc:
            logger.warning("reply_sentiment classification failed: %s", exc)

    elif event_type == "lead_unsubscribed":
        content = f"{lead_email} unsubscribed"
        activity_type = "email"
        if contact:
            contact.sequence_status = "unsubscribed"
            contact.instantly_status = "unsubscribed"
            contact.updated_at = now
            session.add(contact)

    elif event_type == "lead_interested":
        content = f"{lead_email} marked as Interested in Instantly"
        activity_type = "email"
        if contact:
            contact.sequence_status = "interested"
            contact.updated_at = now
            session.add(contact)

    elif event_type == "lead_not_interested":
        content = f"{lead_email} marked as Not Interested in Instantly"
        activity_type = "email"
        if contact:
            contact.sequence_status = "not_interested"
            # Stamp the EMAIL-sourced marker too — this is what the email-outcome
            # red dot + email analytics key off, so a genuine email negative
            # (human-marked in Instantly) correctly shows as a negative reply.
            contact.instantly_status = "not_interested"
            contact.updated_at = now
            session.add(contact)

    elif event_type == "lead_meeting_booked":
        content = f"Meeting booked with {lead_email}"
        activity_type = "meeting"
        if contact:
            contact.sequence_status = "meeting_booked"
            contact.updated_at = now
            session.add(contact)
        if sequence:
            sequence.status = "meeting_booked"
            sequence.updated_at = now
            session.add(sequence)

    elif event_type == "lead_meeting_completed":
        content = f"Meeting completed with {lead_email}"
        activity_type = "meeting"
        if contact:
            contact.sequence_status = "meeting_completed"
            contact.updated_at = now
            session.add(contact)

    elif event_type == "lead_closed":
        content = f"{lead_email} marked as Closed in Instantly"
        activity_type = "email"
        if contact:
            contact.sequence_status = "closed"
            contact.instantly_status = "closed"
            contact.updated_at = now
            session.add(contact)

    elif event_type == "lead_out_of_office":
        content = f"{lead_email} marked as Out of Office in Instantly"
        activity_type = "email"
        if contact:
            # OOO is transient — never let it DOWNGRADE a terminal email outcome
            # (not_interested/unsubscribed/bounced). An OOO firing seconds after a
            # genuine "not interested" used to clobber the negative marker and make
            # the prospect silently lose its red dot.
            if (contact.instantly_status or "") not in ("not_interested", "unsubscribed", "bounced"):
                contact.instantly_status = "out_of_office"
                contact.updated_at = now
                session.add(contact)

    elif event_type == "lead_wrong_person":
        content = f"{lead_email} marked as Wrong Person in Instantly"
        activity_type = "email"
        if contact:
            contact.sequence_status = "wrong_person"
            contact.instantly_status = "wrong_person"
            contact.email_verified = False
            contact.updated_at = now
            session.add(contact)

    elif event_type == "lead_neutral":
        content = f"{lead_email} marked as Neutral in Instantly"
        activity_type = "email"
        if contact:
            contact.instantly_status = "neutral"
            contact.updated_at = now
            session.add(contact)

    elif event_type == "auto_reply_received":
        reply_body = payload.get("reply_text") or payload.get("body") or ""
        content = f"Auto-reply from {lead_email}: {subject}" + (f"\n\n{reply_body[:500]}" if reply_body else "")
        activity_type = "email"
        if contact:
            contact.instantly_status = "auto_replied"
            contact.updated_at = now
            session.add(contact)

    elif event_type == "account_error":
        content = f"Account error for campaign: {payload.get('error_message') or 'Unknown error'}"
        activity_type = "email"
        if sequence:
            sequence.instantly_campaign_status = "error"
            sequence.updated_at = now
            session.add(sequence)

    elif event_type == "campaign_completed":
        content = f"Campaign completed for {lead_email}"
        activity_type = "email"
        if contact:
            contact.sequence_status = "completed"
            contact.updated_at = now
            session.add(contact)
        if sequence:
            sequence.status = "completed"
            sequence.instantly_campaign_status = "completed"
            sequence.updated_at = now
            session.add(sequence)

    else:
        # Unknown event — log it anyway for observability
        content = f"Instantly event [{event_type}]: {subject} → {lead_email}"
        activity_type = "email"

    # ── Commit status changes before creating activity ─────────────────────────
    if session.dirty:
        await session.commit()

    # ── Create activity record ─────────────────────────────────────────────────
    activity = await _create_activity(
        session,
        type_=activity_type,
        source="instantly",
        content=content,
        payload=payload,
        deal_id=deal_id,
        contact_id=contact_id,
        external_source="instantly",
        external_source_id=event_external_id,
        # Stamp the channel like the instantly_sync poller does — its dedup
        # counts existing email rows by medium, so webhook-logged email events
        # must carry medium="email" too or the poller mints duplicates.
        medium="email" if activity_type == "email" else None,
    )

    if email_subject:
        activity.email_subject = email_subject
    if email_sender:
        activity.email_from = email_sender
    if email_recipient:
        activity.email_to = email_recipient
    if email_subject or email_sender or email_recipient:
        session.add(activity)
        await session.commit()

    # If Claude classified a reply, surface the one-line summary as the
    # Activity's ai_summary so it renders inline in the timeline without the
    # rep having to dig into event_metadata.
    reply_verdict = payload.get("reply_sentiment") if isinstance(payload, dict) else None
    if reply_verdict and reply_verdict.get("one_line"):
        sentiment = reply_verdict.get("sentiment", "neutral")
        intent = reply_verdict.get("intent", "other")
        activity.ai_summary = (
            f"[{sentiment.upper()} · {intent}] {reply_verdict['one_line']}"
        )
        session.add(activity)
        await session.commit()

    # ── Refresh system tasks based on new email signal ─────────────────────────
    if contact_id:
        await refresh_system_tasks_for_entity(session, "contact", contact_id)
    if deal_id:
        await refresh_system_tasks_for_entity(session, "deal", deal_id)
    await session.commit()

    return {
        "status": "ok",
        "event_type": event_type,
        "activity_id": str(activity.id),
        "contact_id": str(contact_id) if contact_id else None,
        "sequence_id": str(sequence.id) if sequence else None,
    }


# ── Fireflies webhook ──────────────────────────────────────────────────────────

@router.post("/fireflies")
async def fireflies_webhook(request: Request, session: DBSession) -> dict:
    _verify_webhook_secret(request, settings.FIREFLIES_WEBHOOK_SECRET, source="fireflies")
    payload: Dict[str, Any] = await request.json()
    title = payload.get("title", "Meeting transcript")
    deal_id: Optional[UUID] = None
    if raw_id := payload.get("deal_id"):
        try:
            deal_id = UUID(str(raw_id))
        except ValueError:
            pass
    if not deal_id:
        deal = await _most_recent_active_deal(session)
        deal_id = deal.id if deal else None
    activity = await _create_activity(
        session,
        type_="transcript",
        source="fireflies",
        content=payload.get("summary") or f"Transcript ready: {title}",
        payload=payload,
        deal_id=deal_id,
    )
    if payload.get("ai_summary"):
        activity.ai_summary = payload["ai_summary"]
        session.add(activity)
        await session.commit()
    return {"status": "ok", "activity_id": str(activity.id)}


# ── tl;dv webhook ─────────────────────────────────────────────────────────────

@router.post("/tldv")
async def tldv_webhook(request: Request, session: DBSession) -> dict:
    _verify_webhook_secret(request, settings.TLDV_WEBHOOK_SECRET, source="tldv")
    payload: Dict[str, Any] = await request.json()
    event = str(payload.get("event") or "").strip()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    meeting_id = str(data.get("meetingId") or data.get("id") or "").strip()

    if event not in {"MeetingReady", "TranscriptReady"} or not meeting_id:
        return {"status": "ignored", "event": event, "meeting_id": meeting_id or None}

    result = await sync_tldv_meeting(
        session,
        meeting_id=meeting_id,
        preloaded_meeting=data if event == "MeetingReady" else None,
        preloaded_transcript=data if event == "TranscriptReady" else None,
    )
    return {
        "status": "ok",
        "event": event,
        **result,
    }


# ── Aircall webhook ────────────────────────────────────────────────────────────

@router.post("/aircall")
async def aircall_webhook(request: Request, session: DBSession) -> dict:
    """
    Receive Aircall webhook events and sync them into the CRM as Activity records.

    Aircall sends a `event` field plus a `data` object containing the call details.
    We match the call to a Contact by phone number, then log an Activity.

    Events handled:
      call.created       → log "Call started"
      call.answered      → update activity outcome to "answered"
      call.ended         → log full summary with duration + recording URL
      call.voicemail_left → log voicemail activity
      call.commented     → sync agent note to CRM activity
      call.missed        → log missed call
    """
    _verify_webhook_secret(request, settings.AIRCALL_WEBHOOK_SECRET, source="aircall")
    payload: Dict[str, Any] = await request.json()

    event = str(payload.get("event", "") or "")
    data: Dict[str, Any] = payload.get("data", {}) or {}
    call_data: Dict[str, Any] = data.get("call") if isinstance(data.get("call"), dict) else data

    aircall_call_id = str(
        call_data.get("id")
        or data.get("call_id")
        or (data.get("call", {}) or {}).get("id")
        or ""
    ) or None
    direction = call_data.get("direction", "outbound")
    raw_digits = (
        call_data.get("raw_digits")
        or call_data.get("to")
        or call_data.get("from")
        or data.get("phone_number")
        or ""
    )
    duration = call_data.get("duration") or data.get("duration")
    answered_at = call_data.get("answered_at") or data.get("answered_at")
    missed_reason = call_data.get("missed_call_reason") or data.get("missed_call_reason")
    recording_url = (
        call_data.get("recording")
        or call_data.get("recording_short_url")
        or call_data.get("asset")
        or data.get("recording")
        or None
    )
    voicemail_url = call_data.get("voicemail") or call_data.get("voicemail_short_url") or data.get("voicemail") or None

    agent = call_data.get("user") or data.get("user") or {}
    agent_name = agent.get("name") or agent.get("email") or "Agent"
    agent_email = agent.get("email") or ""

    number_obj = call_data.get("number") or data.get("number") or {}
    number_digits = number_obj.get("digits", "")
    number_name = number_obj.get("name", "")

    comment_text = ""
    comments = call_data.get("comments") or data.get("comments") or []
    if comments:
        comment_text = str(comments[-1].get("content") or "").strip()
    tags = [str(tag.get("name") or "").strip() for tag in (call_data.get("tags") or data.get("tags") or []) if str(tag.get("name") or "").strip()]

    fetched_call: Dict[str, Any] | None = None
    ci_bundle: dict[str, Any] = {}
    parsed_call_id: int | None = None
    aircall_client: AircallClient | None = None
    if aircall_call_id:
        try:
            parsed_call_id = int(aircall_call_id)
        except ValueError:
            logger.warning("Aircall call id %s could not be parsed as an integer", aircall_call_id)
    if parsed_call_id is not None:
        aircall_client = AircallClient()
        # Fetch only the call payload here (cheap, single request) so the
        # enrichment below + the dedup signature stay identical. The expensive
        # conversation-intelligence endpoints are deferred until AFTER the
        # idempotency lookup, so we never re-fetch CI the stored row already has.
        fetched_call, _ = await _fetch_aircall_bundle(
            aircall_client, parsed_call_id, event, ci_signals=()
        )

    if fetched_call:
        duration = fetched_call.get("duration") or duration
        answered_at = fetched_call.get("answered_at") or answered_at
        missed_reason = fetched_call.get("missed_call_reason") or missed_reason
        recording_url = (
            fetched_call.get("recording")
            or fetched_call.get("recording_short_url")
            or fetched_call.get("asset")
            or recording_url
        )
        voicemail_url = fetched_call.get("voicemail") or fetched_call.get("voicemail_short_url") or voicemail_url
        if not raw_digits:
            raw_digits = fetched_call.get("raw_digits") or fetched_call.get("to") or fetched_call.get("from") or raw_digits
        if not comment_text:
            fetched_comments = fetched_call.get("comments") or []
            if fetched_comments:
                comment_text = str(fetched_comments[-1].get("content") or "").strip()
        if not tags:
            tags = [str(tag.get("name") or "").strip() for tag in (fetched_call.get("tags") or []) if str(tag.get("name") or "").strip()]
        if not agent_email:
            fetched_user = fetched_call.get("user") if isinstance(fetched_call.get("user"), dict) else {}
            agent_email = str(fetched_user.get("email") or "").strip()
        if agent_name == "Agent":
            fetched_user = fetched_call.get("user") if isinstance(fetched_call.get("user"), dict) else {}
            agent_name = fetched_user.get("name") or fetched_user.get("email") or agent_name

    # ── Idempotency lookup BEFORE the expensive CI fetch ──────────────────────
    # Compute the dedup key now (comment_text/tags already enriched above, so the
    # signature is identical to before) and load any existing Activity for this
    # call. All five CI events share one row (aircall:ci:{call_id}); reading the
    # CI bundle already persisted on that row lets us skip re-fetching the four
    # signals we already have and fetch only the one this event announces.
    external_source_id = _build_aircall_external_id(
        event=event,
        call_id=aircall_call_id,
        comment_text=comment_text,
        tags=tags,
        ci_bundle=ci_bundle,
    )
    existing_activity = await _get_activity_by_external_id(
        session,
        external_source="aircall",
        external_source_id=external_source_id,
    )
    stored_ci_bundle: dict[str, Any] = {}
    if existing_activity and isinstance(existing_activity.event_metadata, dict):
        stored = existing_activity.event_metadata.get("conversation_intelligence")
        if isinstance(stored, dict):
            stored_ci_bundle = stored

    # Deferred CI fetch: only for CI / comm-asset events, and only the signal(s)
    # not already stored. A late-arriving CI event for an existing call fetches
    # just its own signal; the others are reused from the persisted bundle.
    if parsed_call_id is not None and aircall_client is not None and (
        event in _AIRCALL_CI_EVENTS or event == "call.comm_assets_generated"
    ):
        if event in _AIRCALL_CI_EVENTS:
            signal = _AIRCALL_CI_EVENT_SIGNAL.get(event)
            wanted = (signal,) if signal else ()
        else:  # call.comm_assets_generated — backfill anything still missing
            wanted = _AIRCALL_CI_SIGNALS
        # Skip signals the stored bundle already carries; fetch only the rest.
        missing = tuple(s for s in wanted if not stored_ci_bundle.get(s))
        if missing:
            _, fresh_ci = await _fetch_aircall_bundle(
                aircall_client, parsed_call_id, event, ci_signals=missing
            )
        else:
            fresh_ci = {}
        ci_bundle = _merge_aircall_ci_bundle(stored_ci_bundle, fresh_ci)
    elif stored_ci_bundle:
        # Non-CI event re-touching an existing CI row: keep what was stored.
        ci_bundle = stored_ci_bundle

    contact = await _find_contact_by_phone(session, raw_digits)
    contact_id = contact.id if contact else None
    deal = await _find_best_deal_for_contact(session, contact_id)
    deal_id = deal.id if deal else None

    conversation_summary = ci_bundle.get("summary") if isinstance(ci_bundle, dict) else None
    transcript_text = ci_bundle.get("transcription") if isinstance(ci_bundle, dict) else None
    action_items = ci_bundle.get("action_items") if isinstance(ci_bundle, dict) else []
    topics = ci_bundle.get("topics") if isinstance(ci_bundle, dict) else []
    sentiments = ci_bundle.get("sentiments") if isinstance(ci_bundle, dict) else []

    call_outcome = _infer_aircall_outcome(
        event=event,
        missed_reason=missed_reason,
        voicemail_url=voicemail_url,
        answered_at=answered_at,
        duration=duration,
    )
    activity_type = "call"
    source = "aircall"
    ai_summary: Optional[str] = None

    if event in _AIRCALL_CI_EVENTS or event == "call.comm_assets_generated":
        ai_summary = await _summarize_aircall_signal(
            transcript_text=transcript_text,
            comment_text=comment_text,
            summary_text=conversation_summary,
        )
        content = _build_aircall_ci_content(
            summary_text=conversation_summary or ai_summary,
            topics=topics or [],
            action_items=action_items or [],
            transcript_text=transcript_text,
            sentiments=sentiments or [],
            recording_url=recording_url,
            comment_text=comment_text or None,
        )
        activity_type = "transcript"
    elif event == "call.created":
        label = "inbound" if direction == "inbound" else "outbound"
        content = f"📞 {label.capitalize()} call {'' if direction == 'inbound' else 'to '}{raw_digits} via {number_name or number_digits}"
        if agent_name:
            content += f" — {agent_name}"
    elif event == "call.answered":
        content = f"✅ Call answered — {agent_name} connected with {raw_digits}"
    elif event == "call.ended":
        mins = (duration or 0) // 60
        secs = (duration or 0) % 60
        duration_str = f"{mins}m {secs}s" if mins else f"{secs}s"
        if call_outcome == "missed":
            if direction == "outbound":
                content = f"📵 No answer — outbound call to {raw_digits} (0s)"
            else:
                content = f"📵 Missed call from {raw_digits}"
            if missed_reason:
                content += f" ({missed_reason})"
        elif call_outcome == "voicemail":
            content = f"📬 Voicemail left by {raw_digits}"
        else:
            content = f"📞 Call ended — {duration_str} with {raw_digits}"
            if agent_name:
                content += f" ({agent_name})"
            if recording_url:
                content += " · Recording available"
            if comment_text:
                content += f" · Note: {comment_text[:160]}"
            if tags:
                content += f" · Tags: {', '.join(tags)}"
            ai_summary = await _summarize_aircall_signal(
                transcript_text=transcript_text,
                comment_text=comment_text,
                summary_text=conversation_summary,
            )
    elif event == "call.voicemail_left":
        content = f"📬 Voicemail left by {raw_digits}"
        if voicemail_url:
            recording_url = voicemail_url
            content += " · Voicemail recording available"
    elif event == "call.missed":
        content = f"📵 Missed call from {raw_digits}"
        if agent_name:
            content += f" (assigned to {agent_name})"
    elif event == "call.commented":
        content = f"💬 Call note by {agent_name}: {comment_text}" if comment_text else f"💬 Call note added by {agent_name}"
        activity_type = "note"
        ai_summary = await _summarize_aircall_signal(
            transcript_text=None,
            comment_text=comment_text,
            summary_text=conversation_summary,
        )
    elif event == "call.tagged":
        content = f"🏷 Call tagged: {', '.join(tags)}" if tags else "Call tagged"
    else:
        content = f"Aircall event [{event}] — {raw_digits or 'unknown number'}"

    enriched_payload = dict(payload)
    if fetched_call:
        enriched_payload["fetched_call"] = fetched_call
    if ci_bundle:
        enriched_payload["conversation_intelligence"] = ci_bundle

    # external_source_id + existing_activity were resolved before the CI fetch
    # above (the idempotency gate); reuse them here for the upsert.
    activity = existing_activity or Activity(
        source=source,
        external_source="aircall",
        external_source_id=external_source_id,
    )
    activity.type = activity_type
    activity.medium = "call"
    activity.content = content
    activity.ai_summary = ai_summary
    activity.event_metadata = enriched_payload
    activity.deal_id = deal_id
    activity.contact_id = contact_id
    activity.call_id = aircall_call_id
    activity.call_duration = duration
    activity.call_outcome = call_outcome
    activity.recording_url = recording_url
    activity.aircall_user_name = agent_name or None
    session.add(activity)
    await session.commit()
    await session.refresh(activity)

    # ── Mirror Aircall outcome onto the Contact row ───────────────────────────
    # Previously Aircall data only lived on the Activity timeline, so the
    # Prospecting list view didn't show that the rep had actually attempted a
    # call via Aircall. Now the same fields a manual call logger sets
    # (`call_status`, `call_last_at`, optional `call_notes`) are updated on the
    # contact, so filtering "assigned to me, no call yet" works regardless of
    # which channel the rep used.
    if contact and call_outcome and event in {
        "call.ended", "call.answered", "call.missed", "call.voicemail_left"
    }:
        # Translate Aircall outcome -> the rep-facing call_status enum used on
        # the Contact. Manual call UI uses: attempted | connected | voicemail |
        # callback. Aircall gives us: answered | missed | voicemail.
        outcome_to_status = {
            "answered": "connected",
            "missed": "attempted",
            "voicemail": "voicemail",
        }
        mapped = outcome_to_status.get(call_outcome)
        if mapped:
            contact.call_status = mapped
            contact.call_last_at = datetime.utcnow()
            # Don't overwrite rep-authored notes with the Aircall summary;
            # only auto-populate when the contact has nothing yet, so the rep
            # still has context at a glance on the list view.
            summary = ai_summary or conversation_summary
            if summary and not (contact.call_notes or "").strip():
                contact.call_notes = summary[:500]
            session.add(contact)
            await session.commit()

    if contact_id:
        await refresh_system_tasks_for_entity(session, "contact", contact_id)
    if deal_id:
        await refresh_system_tasks_for_entity(session, "deal", deal_id)
    await session.commit()

    logger.info(
        "Aircall webhook: event=%s call_id=%s contact=%s deal=%s outcome=%s",
        event, aircall_call_id,
        str(contact_id) if contact_id else "unmatched",
        str(deal_id) if deal_id else "unmatched",
        call_outcome,
    )

    return {
        "status": "ok",
        "event": event,
        "activity_id": str(activity.id),
        "contact_id": str(contact_id) if contact_id else None,
        "deal_id": str(deal_id) if deal_id else None,
        "matched": contact_id is not None,
    }


# ── RB2B webhook ───────────────────────────────────────────────────────────────

@router.post("/rb2b")
async def rb2b_webhook(request: Request, session: DBSession) -> dict:
    _verify_webhook_secret(request, settings.RB2B_WEBHOOK_SECRET, source="rb2b")
    payload: Dict[str, Any] = await request.json()
    visitor = payload.get("name", "Unknown visitor")
    company_name = payload.get("company_name", "Unknown company")
    pages = payload.get("pages_visited", [])
    pages_str = ", ".join(pages) if isinstance(pages, list) else str(pages)
    activity = await _create_activity(
        session,
        type_="visit",
        source="rb2b",
        content=f"{visitor} from {company_name} visited: {pages_str or 'website'}",
        payload=payload,
    )
    return {"status": "ok", "activity_id": str(activity.id)}
