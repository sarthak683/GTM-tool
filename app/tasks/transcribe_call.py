"""
Celery task: transcribe a recorded call and classify its disposition.

Lifecycle:
  1. Pop audio bytes from Redis (keyed by recording id; written by the
     API pod on upload).
  2. Write to a fresh /tmp file inside the worker pod.
  3. Whisper transcription via the existing OpenAI client.
  4. Claude disposition classification (see app/services/call_disposition_ai.py).
  5. Update the CallRecording row to `ready`.
  6. ALWAYS delete the Redis key + temp file, even on failure, so audio
     never persists beyond this task's lifetime.

Failure modes degrade gracefully: a missing OpenAI key marks the row
`failed` with a clear reason; a missing Claude key keeps the transcript
but leaves ai_disposition NULL so the rep falls back to manual entry.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


# Shared between the API (writer) and this task (reader). Same prefix +
# format string so the two pods agree without a runtime dependency
# beyond Redis.
REDIS_AUDIO_KEY = "call_rec_audio:{id}"


@celery_app.task(
    name="app.tasks.transcribe_call.transcribe_call_task",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def transcribe_call_task(self, recording_id: str) -> dict:
    """Celery sync entrypoint — runs the async pipeline on a fresh loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run(UUID(recording_id)))
    except Exception as exc:
        logger.exception(f"transcribe_call_task failed for {recording_id}: {exc}")
        # Best-effort fail-state write so the UI can surface a useful
        # error instead of spinning forever.
        try:
            loop.run_until_complete(
                _mark_failed(UUID(recording_id), f"task crashed: {exc}")
            )
        except Exception:
            pass
        # Don't retry — re-running whisper on the same audio after a
        # crash is unlikely to help and burns API budget. The rep can
        # always re-record.
        return {"status": "failed", "recording_id": recording_id, "error": str(exc)}
    finally:
        loop.close()


async def _run(recording_id: UUID) -> dict:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.config import settings
    from app.models.call_recording import CallRecording

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    tmp_path: Optional[str] = None
    redis_key = REDIS_AUDIO_KEY.format(id=recording_id)
    try:
        # ── 1. Pop audio from Redis ────────────────────────────────────
        from redis import Redis

        r = Redis.from_url(settings.REDIS_URL)
        audio_bytes = r.get(redis_key)
        if not audio_bytes:
            async with SessionLocal() as session:
                await _set_failed(session, recording_id, "audio expired or missing in Redis")
            return {"status": "failed", "recording_id": str(recording_id), "error": "audio missing"}

        # Always delete the key once we've materialized the bytes. If we
        # crash after this point, the audio is gone — that's intentional
        # (no audio retention).
        r.delete(redis_key)

        # ── 2. Write to a temp file Whisper SDK can read ──────────────
        fd, tmp_path = tempfile.mkstemp(prefix="call_rec_", suffix=".webm", dir="/tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(audio_bytes)

        # ── 3. Whisper transcription ──────────────────────────────────
        async with SessionLocal() as session:
            await _set_status(session, recording_id, "transcribing")

        transcript = await _transcribe_with_whisper(tmp_path)
        if transcript is None:
            async with SessionLocal() as session:
                await _set_failed(session, recording_id, "Whisper transcription returned no text")
            return {"status": "failed", "recording_id": str(recording_id)}

        # ── 4. Claude disposition classification ──────────────────────
        async with SessionLocal() as session:
            recording = await _get_recording(session, recording_id)
            if recording is None:
                logger.warning(f"Recording {recording_id} vanished mid-task")
                return {"status": "missing", "recording_id": str(recording_id)}
            recording.status = "classifying"
            recording.transcript = transcript
            recording.updated_at = datetime.utcnow()
            await session.commit()

            # Fetch contact context for a better classification prompt. Deal
            # recordings may have no contact — the classifier just runs with
            # less context in that case.
            contact_name = contact_title = company_name = None
            try:
                from app.models.contact import Contact
                from app.models.company import Company

                contact = (await session.execute(
                    select(Contact).where(Contact.id == recording.contact_id)
                )).scalar_one_or_none() if recording.contact_id else None
                if contact:
                    contact_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip() or None
                    contact_title = contact.title
                    if contact.company_id:
                        company = (await session.execute(
                            select(Company).where(Company.id == contact.company_id)
                        )).scalar_one_or_none()
                        if company:
                            company_name = company.name
            except Exception:
                # Context is a nice-to-have; never fail the classifier
                # over a JOIN issue.
                logger.exception("failed to fetch contact context for AI classifier")

        from app.services.call_disposition_ai import classify_call_transcript

        verdict = await classify_call_transcript(
            transcript=transcript,
            contact_name=contact_name,
            contact_title=contact_title,
            company_name=company_name,
        )

        # ── 5. Persist final state ────────────────────────────────────
        async with SessionLocal() as session:
            recording = await _get_recording(session, recording_id)
            if recording is None:
                return {"status": "missing", "recording_id": str(recording_id)}
            recording.status = "ready"
            recording.transcript = transcript
            if verdict:
                recording.ai_disposition = verdict["disposition"]
                recording.ai_confidence = verdict["confidence"]
                recording.ai_summary = verdict["summary"]
            # If the classifier failed but the transcript succeeded, leave
            # ai_* NULL — the rep gets the transcript and falls back to
            # manual disposition. That's still useful.
            recording.updated_at = datetime.utcnow()
            await session.commit()

        return {
            "status": "ready",
            "recording_id": str(recording_id),
            "transcript_chars": len(transcript),
            "ai_disposition": verdict["disposition"] if verdict else None,
        }

    finally:
        # ── 6. Always clean up ────────────────────────────────────────
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        # Belt-and-suspenders Redis delete in case step 1's delete
        # raced past the failed return path.
        try:
            from redis import Redis
            r = Redis.from_url(settings.REDIS_URL)
            r.delete(redis_key)
        except Exception:
            pass
        await engine.dispose()


async def _transcribe_with_whisper(audio_path: str) -> Optional[str]:
    """Call OpenAI Whisper on the audio file. Returns transcript text or None."""
    from app.config import settings

    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — cannot transcribe")
        return None

    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.error("openai package not installed in worker image")
        return None

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    # whisper-1 takes a file-like object; we open the temp file and let
    # the SDK stream the upload.
    try:
        with open(audio_path, "rb") as audio_file:
            result = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                # English-only for now — Beacon's primary market. If
                # multi-language calls become a need, drop this param
                # and Whisper auto-detects (slightly slower).
                language="en",
                response_format="text",
            )
        # response_format="text" returns a plain string, not a dict.
        text = result if isinstance(result, str) else getattr(result, "text", None)
        if not text:
            logger.warning("Whisper returned empty text")
            return None
        return text.strip()
    except Exception as exc:
        logger.exception(f"Whisper call failed: {exc}")
        return None


# ── Small session helpers ────────────────────────────────────────────

async def _get_recording(session, recording_id: UUID):
    from app.models.call_recording import CallRecording
    return (await session.execute(
        select(CallRecording).where(CallRecording.id == recording_id)
    )).scalar_one_or_none()


async def _set_status(session, recording_id: UUID, status: str) -> None:
    recording = await _get_recording(session, recording_id)
    if recording is None:
        return
    recording.status = status
    recording.updated_at = datetime.utcnow()
    await session.commit()


async def _set_failed(session, recording_id: UUID, reason: str) -> None:
    recording = await _get_recording(session, recording_id)
    if recording is None:
        return
    recording.status = "failed"
    recording.failure_reason = reason
    recording.updated_at = datetime.utcnow()
    await session.commit()


async def _mark_failed(recording_id: UUID, reason: str) -> None:
    """Standalone version of _set_failed for the crash-recovery path."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.config import settings

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with SessionLocal() as session:
            await _set_failed(session, recording_id, reason[:2000])
    finally:
        await engine.dispose()
