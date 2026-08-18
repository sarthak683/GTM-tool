"""Nightly cleanup of expired Zippy-generated documents.

Zippy's generated .docx/.xlsx/.pptx files live in Postgres (see
``app.services.zippy_docs.storage``) so both backend replicas can serve them and
so they survive a redeploy. Without a sweeper those rows would accumulate
forever: the download link is only ever the fallback for a failed Google Drive
upload, so once its retention window has passed nobody is going to click it.

Import weight matters here. The worker's ``include`` list does not carry the
document generators, and it should stay that way — this task reaches only for
``zippy_docs.storage``, which imports the model and the session factory and
nothing from python-docx / openpyxl / python-pptx.
"""
from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.tasks._runner import run_async_task as _run_async_task

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.zippy_documents.purge_expired_zippy_documents")
def purge_expired_zippy_documents() -> dict:
    """Delete Zippy documents whose retention window has passed."""
    from app.services.zippy_docs.storage import purge_expired

    try:
        deleted = _run_async_task(purge_expired())
    except Exception:
        logger.exception("Failed to purge expired Zippy documents")
        return {"status": "error", "deleted": 0}
    return {"status": "ok", "deleted": deleted}
