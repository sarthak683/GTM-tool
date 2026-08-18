"""Pure predicates over deal/activity state — no session, no writes.

Lifted out of app/services/tasks.py, which had grown to 4455 lines with these
read-only helpers interleaved with the code that writes system tasks. Keeping
them apart matters because the two have very different risk: everything here is
a pure function of its arguments, so it can be read, reasoned about and tested
without a database.

app/services/ai_task_emitter.py used to import `_stage_reached` lazily inside a
function to dodge a circular import with tasks.py. That cycle does not exist
against this module, so the lazy import there can now become a normal one.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable

from app.models.activity import Activity
from app.models.contact import Contact
from app.models.deal import DEAL_STAGES
from app.services.activity_signal_classifier import classify_activity_text


STAGE_INDEX = {stage: idx for idx, stage in enumerate(DEAL_STAGES)}


def _normalize(text: str | None) -> str:
    return (text or "").strip().lower()


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _has_blocker_signal(texts: list[str]) -> bool:
    return any(classify_activity_text(text).blocker == "present" for text in texts if text)


def _stage_reached(current_stage: str | None, target_stage: str) -> bool:
    if not current_stage:
        return False
    return STAGE_INDEX.get(current_stage, -1) >= STAGE_INDEX.get(target_stage, 999)


def _activity_signal_text(activity: Activity) -> str:
    metadata = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
    metadata_text: list[str] = []
    for key in ("summary", "content", "text", "transcription", "thread_latest_message_text", "thread_context_excerpt", "google_doc_transcript"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            metadata_text.append(_normalize(value))

    for comment in metadata.get("comments") or []:
        if isinstance(comment, dict):
            value = comment.get("content")
            if isinstance(value, str) and value.strip():
                metadata_text.append(_normalize(value))

    fetched_call = metadata.get("fetched_call") if isinstance(metadata.get("fetched_call"), dict) else {}
    for comment in fetched_call.get("comments") or []:
        if isinstance(comment, dict):
            value = comment.get("content")
            if isinstance(value, str) and value.strip():
                metadata_text.append(_normalize(value))
    tag_names = [str(tag.get("name") or "").strip() for tag in fetched_call.get("tags") or [] if isinstance(tag, dict)]
    if tag_names:
        metadata_text.append(_normalize(" ".join(tag_names)))

    for entry in metadata.get("topics") or []:
        if isinstance(entry, dict):
            value = entry.get("name") or entry.get("label") or entry.get("topic")
        else:
            value = entry
        if isinstance(value, str) and value.strip():
            metadata_text.append(_normalize(value))

    for entry in metadata.get("action_items") or metadata.get("items") or []:
        if isinstance(entry, dict):
            value = entry.get("text") or entry.get("title") or entry.get("content")
        else:
            value = entry
        if isinstance(value, str) and value.strip():
            metadata_text.append(_normalize(value))

    conversation_intelligence = metadata.get("conversation_intelligence") if isinstance(metadata.get("conversation_intelligence"), dict) else {}
    if conversation_intelligence:
        for key in ("summary", "transcription"):
            value = conversation_intelligence.get(key)
            if isinstance(value, str) and value.strip():
                metadata_text.append(_normalize(value))
        for entry in conversation_intelligence.get("topics") or []:
            if isinstance(entry, str) and entry.strip():
                metadata_text.append(_normalize(entry))
        for entry in conversation_intelligence.get("action_items") or []:
            if isinstance(entry, str) and entry.strip():
                metadata_text.append(_normalize(entry))
        for entry in conversation_intelligence.get("sentiments") or []:
            if isinstance(entry, str) and entry.strip():
                metadata_text.append(_normalize(entry))

    return " ".join(
        filter(
            None,
            [
                _normalize(activity.ai_summary),
                _normalize(activity.content),
                _normalize(activity.email_subject),
                *metadata_text,
            ],
        )
    )


def _activity_has_conversation_intelligence(activity: Activity) -> bool:
    metadata = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
    bundle = metadata.get("conversation_intelligence") if isinstance(metadata.get("conversation_intelligence"), dict) else {}
    return bool(
        bundle
        and (
            bundle.get("summary")
            or bundle.get("transcription")
            or bundle.get("topics")
            or bundle.get("action_items")
            or bundle.get("sentiments")
        )
    )


def _health_bucket(score: int) -> str:
    if score >= 70:
        return "green"
    if score >= 40:
        return "yellow"
    return "red"


def _email_domain(value: str | None) -> str:
    email = (value or "").strip().lower()
    if "@" not in email:
        return ""
    return email.split("@", 1)[1]


def _has_security_stakeholder(contacts: list[Contact]) -> bool:
    for contact in contacts:
        signal = " ".join(
            filter(
                None,
                [
                    _normalize(contact.title),
                    _normalize(contact.persona),
                    _normalize(contact.persona_type),
                ],
            )
        )
        if _contains_any(signal, ["security", "procurement", "legal", "compliance", "it"]):
            return True
    return False


def _has_internal_email_after(
    activities: list[Activity],
    *,
    after: datetime | None,
    internal_domain: str,
) -> bool:
    if not after or not internal_domain:
        return False
    return any(
        activity.type == "email"
        and _email_domain(activity.email_from) == internal_domain
        and activity.created_at >= after
        for activity in activities
    )


def _has_external_email_after(
    activities: list[Activity],
    *,
    after: datetime | None,
    internal_domain: str,
) -> bool:
    if not after:
        return False
    return any(
        activity.type == "email"
        and _email_domain(activity.email_from) != internal_domain
        and activity.created_at >= after
        for activity in activities
    )


def _recent_buyer_thread_texts(
    activities: list[Activity],
    *,
    max_items: int = 5,
) -> list[str]:
    texts: list[str] = []
    seen_threads: set[str] = set()
    for activity in activities:
        if activity.type != "email":
            continue
        metadata = activity.event_metadata if isinstance(activity.event_metadata, dict) else {}
        thread_id = str(metadata.get("gmail_thread_id") or activity.email_message_id or "").strip()
        if thread_id:
            if thread_id in seen_threads:
                continue
            seen_threads.add(thread_id)
        text = _activity_signal_text(activity)
        if text:
            texts.append(text)
        if len(texts) >= max_items:
            break
    return texts


def _text_contains_any(texts: list[str], terms: Iterable[str]) -> bool:
    return any(_contains_any(text, terms) for text in texts)


def _latest_matching_activity(
    activities: list[Activity],
    predicate: Callable[[Activity], bool],
) -> Activity | None:
    for activity in activities:
        if predicate(activity):
            return activity
    return None


def _detect_competitor_signal(text: str) -> str | None:
    known = ["rocketlane", "arrows", "guidecx", "monday", "asana", "wrike", "clickup"]
    for name in known:
        if name in text:
            return name.title()
    if _contains_any(text, ["competitor", "alternative", "vs ", "other vendor"]):
        return "Competitor"
    return None
