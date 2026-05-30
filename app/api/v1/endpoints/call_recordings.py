"""
Call recording endpoints — rep records a manual call (phone on
speakerphone, laptop mic captures both sides), uploads the audio for
transcription + AI disposition classification.

Audio is NOT persisted long-term. The POST writes the bytes to a temp
path on the worker pod's filesystem, queues a Celery task, and returns
the recording row. The task transcribes, classifies, deletes the temp
file, and updates the row to `ready`. The frontend polls the GET
endpoint until status is `ready` (or `failed`).

  POST  /api/v1/calls/recordings/                  — multipart audio upload
  GET   /api/v1/calls/recordings/?contact_id=...   — list a contact's recordings
  GET   /api/v1/calls/recordings/{id}              — poll for transcription status
  PATCH /api/v1/calls/recordings/{id}              — edit transcript / disposition / summary
  POST  /api/v1/calls/recordings/{id}/retry        — re-queue transcription after a failure
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlmodel import select

from app.core.dependencies import CurrentUser, DBSession
from app.models.call_recording import CALL_RECORDING_STATUSES, CallRecording, CallRecordingRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls/recordings", tags=["call-recordings"])


# Reject pathologically large uploads at the boundary so a runaway tab
# can't fill disk. 60 minutes of audio/webm @ ~16kbps mono opus is
# ~7 MB; cap at 50 MB which covers ~6 hours and still bounds the blast
# radius if a browser misbehaves.
_MAX_AUDIO_BYTES = 50 * 1024 * 1024


@router.post("/", response_model=CallRecordingRead, status_code=201)
async def upload_call_recording(
    session: DBSession,
    user: CurrentUser,
    audio: UploadFile = File(...),
    contact_id: Optional[UUID] = Form(default=None),
    deal_id: Optional[UUID] = Form(default=None),
    consent_acknowledged_at: Optional[datetime] = Form(default=None),
    duration_seconds: Optional[int] = Form(default=None),
):
    """Receive a recorded audio blob, persist to /tmp, queue transcription.

    The audio file lives on the worker filesystem only for the duration
    of the Celery task (~10-30s). The task is responsible for deleting it
    in both the success and failure paths.
    """
    # A recording must attach to a contact OR a deal. The deal detail recorder
    # passes deal_id (contact optional); the prospect drawer passes contact_id.
    if contact_id is None and deal_id is None:
        raise HTTPException(400, "A recording must be linked to a contact or a deal.")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "Empty audio payload.")
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            413,
            f"Audio too large: {len(audio_bytes)} bytes (max {_MAX_AUDIO_BYTES}).",
        )

    # Write to a named temp file under /tmp. The Celery worker shares
    # the same image and mounts the same /tmp namespace inside its own
    # pod — so we pass the *path* through Celery and the worker reads
    # from its own /tmp. In K8s these are separate pods, so /tmp is NOT
    # shared. The fix: stash the audio in the DB temporarily as bytea,
    # or stream-upload via Redis. We use Redis for the audio handoff
    # (small, bounded) so the API and worker pods don't need shared
    # storage.
    #
    # Below: we write to /tmp in the API pod, then immediately stream
    # the bytes into Redis under a short-TTL key. The Celery task reads
    # from Redis. Audio leaves Redis (and memory) the moment the task
    # finishes — both success and failure paths delete the key.
    suffix = os.path.splitext(audio.filename or "")[1].lower() or ".webm"
    fd, tmp_path = tempfile.mkstemp(prefix="call_rec_", suffix=suffix, dir="/tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio_bytes)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # The column is TIMESTAMP WITHOUT TIME ZONE — asyncpg refuses to
    # insert a tz-aware datetime into it, and the frontend sends an ISO
    # string with a "Z" suffix (becomes tz-aware after FastAPI parses
    # it). Convert to UTC then strip the tzinfo so the DB sees a naive
    # UTC datetime — same convention used elsewhere in the codebase.
    from datetime import timezone
    consent_ts = consent_acknowledged_at or datetime.utcnow()
    if consent_ts.tzinfo is not None:
        consent_ts = consent_ts.astimezone(timezone.utc).replace(tzinfo=None)

    recording = CallRecording(
        contact_id=contact_id,
        deal_id=deal_id,
        created_by_id=user.id,
        status="uploaded",
        consent_acknowledged_at=consent_ts,
        audio_size_bytes=len(audio_bytes),
        audio_duration_seconds=duration_seconds,
    )
    session.add(recording)
    await session.commit()
    await session.refresh(recording)

    # Hand the audio off to the worker via Redis so the API + worker
    # pods don't need a shared filesystem. The Celery task pops the key.
    from app.celery_app import celery_app
    from app.tasks.transcribe_call import transcribe_call_task, REDIS_AUDIO_KEY

    try:
        from redis import Redis
        from app.config import settings
        r = Redis.from_url(settings.REDIS_URL)
        # 30 minute TTL — far longer than any task takes. The task
        # explicitly deletes the key when it finishes.
        with open(tmp_path, "rb") as f:
            r.setex(REDIS_AUDIO_KEY.format(id=recording.id), 30 * 60, f.read())
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    transcribe_call_task.delay(str(recording.id))
    return recording


@router.get("/", response_model=list[CallRecordingRead])
async def list_call_recordings(
    session: DBSession,
    _user: CurrentUser,
    contact_id: Optional[UUID] = Query(default=None, description="Scope to a contact's recordings."),
    deal_id: Optional[UUID] = Query(default=None, description="Scope to a deal's recordings (AE deal recorder)."),
    limit: int = Query(default=50, le=200),
):
    """List recordings for a contact OR a deal, newest first. Used by the call
    drawer (contact) and the deal detail recorder (deal) to surface prior
    transcripts for context."""
    if contact_id is None and deal_id is None:
        raise HTTPException(400, "Provide contact_id or deal_id.")
    stmt = select(CallRecording).where(CallRecording.deleted_at.is_(None))
    if contact_id is not None:
        stmt = stmt.where(CallRecording.contact_id == contact_id)
    if deal_id is not None:
        stmt = stmt.where(CallRecording.deal_id == deal_id)
    stmt = stmt.order_by(CallRecording.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{recording_id}", response_model=CallRecordingRead)
async def get_call_recording(
    recording_id: UUID,
    session: DBSession,
    _user: CurrentUser,
):
    """Poll a recording's transcription status."""
    result = await session.execute(
        select(CallRecording).where(CallRecording.id == recording_id)
    )
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(404, "Recording not found.")
    return recording


class CallRecordingPatch(BaseModel):
    """Editable fields. All optional — send only what's changing."""
    transcript: Optional[str] = None
    ai_disposition: Optional[str] = None
    ai_summary: Optional[str] = None


@router.patch("/{recording_id}", response_model=CallRecordingRead)
async def update_call_recording(
    recording_id: UUID,
    payload: CallRecordingPatch,
    session: DBSession,
    _user: CurrentUser,
):
    """Rep-side corrections. Common case: Whisper misheard a name or
    product term — rep edits the transcript and saves. We do NOT re-run
    the AI classifier here; the rep is the source of truth at this point."""
    recording = (await session.execute(
        select(CallRecording).where(CallRecording.id == recording_id)
    )).scalar_one_or_none()
    if not recording:
        raise HTTPException(404, "Recording not found.")

    if payload.transcript is not None:
        recording.transcript = payload.transcript
    if payload.ai_disposition is not None:
        recording.ai_disposition = payload.ai_disposition or None
    if payload.ai_summary is not None:
        recording.ai_summary = payload.ai_summary or None

    recording.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(recording)
    return recording


@router.delete("/{recording_id}", status_code=204)
async def delete_call_recording(
    recording_id: UUID,
    session: DBSession,
    user: CurrentUser,
):
    """Soft-delete a recording: it disappears from the lists but the row is
    retained for audit, stamped with who deleted it and when."""
    recording = (await session.execute(
        select(CallRecording).where(CallRecording.id == recording_id)
    )).scalar_one_or_none()
    if not recording:
        raise HTTPException(404, "Recording not found.")
    if recording.deleted_at is None:
        now = datetime.utcnow()
        recording.deleted_at = now
        recording.deleted_by_id = user.id
        recording.updated_at = now
        await session.commit()
    return None


@router.post("/{recording_id}/retry", response_model=CallRecordingRead)
async def retry_call_recording(
    recording_id: UUID,
    session: DBSession,
    _user: CurrentUser,
):
    """Re-queue a failed transcription. Only works if the audio is still
    in Redis (30-min TTL from upload). After that, the rep has to
    re-record — the audio is genuinely gone by design."""
    recording = (await session.execute(
        select(CallRecording).where(CallRecording.id == recording_id)
    )).scalar_one_or_none()
    if not recording:
        raise HTTPException(404, "Recording not found.")
    if recording.status != "failed":
        raise HTTPException(
            409,
            f"Retry is only valid for failed recordings (status={recording.status!r}).",
        )

    from app.config import settings
    from app.tasks.transcribe_call import REDIS_AUDIO_KEY, transcribe_call_task
    from redis import Redis

    # Confirm the audio is still around before bumping status — otherwise
    # we'd flip the row back to "transcribing" only to fail again immediately.
    r = Redis.from_url(settings.REDIS_URL)
    if not r.exists(REDIS_AUDIO_KEY.format(id=recording.id)):
        raise HTTPException(
            410,
            "Audio expired (>30 min since upload). Please re-record the call.",
        )

    recording.status = "uploaded"
    recording.failure_reason = None
    recording.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(recording)

    transcribe_call_task.delay(str(recording.id))
    return recording


# Kept around so import-by-name doesn't accidentally drop a status value.
_assert_known_statuses = {s for s in CALL_RECORDING_STATUSES}
