"""Zippy generated-document storage: durability, honest failure, no static mount.

The bug these cover: generated .docx/.xlsx/.pptx files were written to a
directory inside the backend container and published through a StaticFiles
mount. Production runs two replicas with no shared volume, so a link made by one
pod 404'd on the other, and every restart wiped the files. These tests pin the
replacement — bytes in Postgres behind a capability token — and, just as
importantly, pin that a dead link now explains itself instead of 404ing blank.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.services.zippy_docs import base as docs_base
from app.services.zippy_docs import storage
from app.services.zippy_docs.base import GeneratedDocument


def _run(coro):
    return asyncio.run(coro)


# ── Scratch path, not a published directory ──────────────────────────────────


def test_build_output_path_returns_no_public_url():
    """A generator must not be handed a /zippy_outputs/ link any more.

    The old second return value was the served static path. Anything non-empty
    here would flow straight into the artifact chip and become a dead link.
    """
    path, url = docs_base.build_output_path("MOM", "Acme Corp")
    assert url == ""
    assert path.suffix == ".docx"
    assert "MOM" in path.name and "Acme_Corp" in path.name


def test_scratch_dir_is_created_lazily_not_at_import():
    """The mkdir moved out of import time so a read-only FS can't break boot."""
    source = Path(docs_base.__file__).read_text()
    # The only mkdir left must be inside ensure_output_dir().
    assert "ensure_output_dir" in source
    lines = source.splitlines()
    mkdir_lines = [i for i, ln in enumerate(lines) if ".mkdir(" in ln]
    assert mkdir_lines, "expected a mkdir inside ensure_output_dir"
    for i in mkdir_lines:
        preceding = "\n".join(lines[max(0, i - 25) : i])
        assert "def ensure_output_dir" in preceding, (
            "mkdir must only run inside ensure_output_dir(), not at import time"
        )


def test_build_output_path_creates_the_scratch_dir_on_demand():
    path, _ = docs_base.build_output_path("draft", "Someone")
    assert path.parent.is_dir()


def test_scratch_dir_is_not_the_old_served_storage_dir():
    assert "storage/zippy_outputs" not in str(docs_base.ZIPPY_OUTPUT_DIR)


# ── Storage helpers ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "filename,expected_fragment",
    [
        ("a.docx", "wordprocessingml.document"),
        ("a.xlsx", "spreadsheetml.sheet"),
        ("a.pptx", "presentationml.presentation"),
        ("a.pdf", "application/pdf"),
        ("a.weird", "application/octet-stream"),
        ("", "application/octet-stream"),
    ],
)
def test_content_type_for(filename, expected_fragment):
    assert expected_fragment in storage.content_type_for(filename)


def test_tokens_are_unguessable_and_unique():
    """The download route is unauthenticated, so the token is the credential."""
    tokens = {storage.new_token() for _ in range(50)}
    assert len(tokens) == 50
    # token_urlsafe(32) -> 256 bits -> ~43 chars.
    assert all(len(t) >= 40 for t in tokens)


def test_document_url_is_relative():
    """Absolute URLs here get mangled by the frontend's API_BASE prefixing."""
    url = storage.document_url("abc123")
    assert url == "/api/v1/zippy/documents/abc123"
    assert not url.startswith("http")


def test_retention_days_reads_env_override(monkeypatch):
    monkeypatch.setenv("ZIPPY_DOC_RETENTION_DAYS", "7")
    assert storage.retention_days() == 7


def test_retention_days_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("ZIPPY_DOC_RETENTION_DAYS", "not-a-number")
    assert storage.retention_days() == storage.DEFAULT_RETENTION_DAYS


def test_retention_days_defaults_from_settings(monkeypatch):
    monkeypatch.delenv("ZIPPY_DOC_RETENTION_DAYS", raising=False)
    assert storage.retention_days() >= 1


def test_store_document_rejects_empty_payload():
    with pytest.raises(ValueError):
        _run(storage.store_document(filename="x.docx", data=b""))


def test_store_document_rejects_oversized_payload():
    oversized = b"x" * (storage.MAX_DOCUMENT_BYTES + 1)
    with pytest.raises(ValueError, match="storage cap"):
        _run(storage.store_document(filename="x.docx", data=oversized))


# ── persist_generated_document: the seam that makes a doc durable ────────────


def _fake_doc(tmp_path: Path, payload: bytes = b"PK\x03\x04 fake docx") -> GeneratedDocument:
    path = tmp_path / "MOM-Acme-2026-08-17-abc123.docx"
    path.write_bytes(payload)
    return GeneratedDocument(
        filename=path.name,
        path=str(path),
        url="",
        kind="mom",
        summary="test",
        created_at=datetime.utcnow(),
    )


def test_persist_rewrites_url_and_removes_scratch_file(tmp_path, monkeypatch):
    captured = {}

    async def fake_store(*, filename, data, kind="", user_id=None, **kw):
        captured.update(filename=filename, data=data, kind=kind, user_id=user_id)
        return "TOKEN123"

    monkeypatch.setattr(storage, "store_document", fake_store)
    doc = _fake_doc(tmp_path)
    scratch = Path(doc.path)

    token = _run(storage.persist_generated_document(doc, user_id="not-a-uuid"))

    assert token == "TOKEN123"
    assert doc.url == "/api/v1/zippy/documents/TOKEN123"
    assert captured["data"] == b"PK\x03\x04 fake docx"
    assert captured["kind"] == "mom"
    assert not scratch.exists(), "scratch file must not linger on the pod's disk"


def test_persist_failure_clears_url_rather_than_leaving_a_dead_link(tmp_path, monkeypatch):
    """A storage failure must not advertise a URL that would 404.

    The frontend only renders the chip when `url` is truthy, and the Google
    Drive link (which the UI prefers anyway) is unaffected — so an empty url is
    the honest outcome, not a broken one.
    """
    async def boom(**kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(storage, "store_document", boom)
    doc = _fake_doc(tmp_path)
    doc.drive_url = "https://docs.google.com/document/d/xyz"
    scratch = Path(doc.path)

    token = _run(storage.persist_generated_document(doc))

    assert token is None
    assert doc.url == ""
    assert doc.drive_url == "https://docs.google.com/document/d/xyz"
    assert not scratch.exists()


def test_persist_handles_a_missing_scratch_file(tmp_path, monkeypatch):
    doc = _fake_doc(tmp_path)
    Path(doc.path).unlink()
    assert _run(storage.persist_generated_document(doc)) is None
    assert doc.url == ""


def test_persist_handles_a_document_with_no_path():
    doc = GeneratedDocument(
        filename="x.docx", path="", url="/zippy_outputs/x.docx",
        kind="mom", summary="", created_at=datetime.utcnow(),
    )
    assert _run(storage.persist_generated_document(doc)) is None
    assert doc.url == ""


def test_coerce_uuid_accepts_uuid_string_and_rejects_junk():
    from uuid import UUID, uuid4

    real = uuid4()
    assert storage._coerce_uuid(real) == real
    assert storage._coerce_uuid(str(real)) == real
    assert isinstance(storage._coerce_uuid(str(real)), UUID)
    assert storage._coerce_uuid("nope") is None
    assert storage._coerce_uuid(None) is None


# ── The tool seam persists every document ────────────────────────────────────


def test_doc_to_artifact_is_async_and_persists(monkeypatch):
    """Every generator funnels through here, so this is where durability lands."""
    import inspect

    from app.services import zippy_tools

    assert inspect.iscoroutinefunction(zippy_tools._doc_to_artifact)

    calls = []

    async def fake_persist(doc, *, user_id=None):
        calls.append((doc, user_id))
        doc.url = "/api/v1/zippy/documents/TOK"
        return "TOK"

    monkeypatch.setattr(zippy_tools, "persist_generated_document", fake_persist)
    doc = GeneratedDocument(
        filename="MOM.docx", path="/tmp/MOM.docx", url="",
        kind="mom", summary="s", created_at=datetime.utcnow(),
    )
    artifact = _run(zippy_tools._doc_to_artifact(doc, user_id=None))

    assert len(calls) == 1
    assert artifact["url"] == "/api/v1/zippy/documents/TOK"


def test_every_doc_artifact_call_site_is_awaited():
    source = Path("app/services/zippy_tools.py").read_text()
    assert "_doc_to_artifact(doc)" not in source, (
        "a call site was left un-awaited — its document would never be stored"
    )
    assert source.count("await _doc_to_artifact(doc, user_id=user_id)") == 7


# ── Download route ───────────────────────────────────────────────────────────


class _Row:
    def __init__(self, **kw):
        self.token = kw.get("token", "tok")
        self.filename = kw.get("filename", "MOM-Acme.docx")
        self.content_type = kw.get(
            "content_type",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.data = kw.get("data", b"BYTES")
        self.created_at = kw.get("created_at", datetime.utcnow())
        self.expires_at = kw.get("expires_at", datetime.utcnow() + timedelta(days=30))


def _patch_load(monkeypatch, row):
    from app.api.v1.endpoints import zippy as zippy_ep

    async def fake_load(token):
        return row

    monkeypatch.setattr(zippy_ep, "load_document", fake_load)


def test_download_returns_the_bytes_with_a_download_header(client, monkeypatch):
    _patch_load(monkeypatch, _Row(data=b"PK\x03\x04hello"))
    resp = client.get("/api/v1/zippy/documents/sometoken")

    assert resp.status_code == 200
    assert resp.content == b"PK\x03\x04hello"
    assert "wordprocessingml.document" in resp.headers["content-type"]
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "MOM-Acme.docx" in disposition


def test_download_needs_no_authorization_header(client, monkeypatch):
    """A transcript anchor can't send the JWT — requiring it would 401 always."""
    _patch_load(monkeypatch, _Row())
    resp = client.get("/api/v1/zippy/documents/sometoken")
    assert resp.status_code == 200


def test_download_handles_non_ascii_filenames(client, monkeypatch):
    _patch_load(monkeypatch, _Row(filename="MOM-Café-Zürich.docx"))
    resp = client.get("/api/v1/zippy/documents/sometoken")
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    disposition.encode("latin-1")  # must be a header the browser accepts


def test_unknown_token_explains_itself_instead_of_a_bare_404(client, monkeypatch):
    _patch_load(monkeypatch, None)
    resp = client.get(
        "/api/v1/zippy/documents/nope", headers={"Accept": "text/html"}
    )
    assert resp.status_code == 404
    body = resp.text.lower()
    assert "isn" in body and "available" in body
    assert "generate it again" in body


def test_expired_document_returns_410_and_says_it_expired(client, monkeypatch):
    _patch_load(
        monkeypatch,
        _Row(
            created_at=datetime.utcnow() - timedelta(days=90),
            expires_at=datetime.utcnow() - timedelta(days=60),
        ),
    )
    resp = client.get(
        "/api/v1/zippy/documents/oldtoken", headers={"Accept": "text/html"}
    )
    assert resp.status_code == 410
    assert "expired" in resp.text.lower()
    assert "generate it again" in resp.text.lower()


def test_expired_page_escapes_the_stored_filename(client, monkeypatch):
    """The filename reaches the page from a user-supplied client name."""
    _patch_load(
        monkeypatch,
        _Row(
            filename="<img src=x onerror=alert(1)>.docx",
            created_at=datetime.utcnow() - timedelta(days=90),
            expires_at=datetime.utcnow() - timedelta(days=60),
        ),
    )
    resp = client.get(
        "/api/v1/zippy/documents/oldtoken", headers={"Accept": "text/html"}
    )
    assert resp.status_code == 410
    assert "<img src=x" not in resp.text
    assert "&lt;img" in resp.text


def test_api_clients_get_json_not_html(client, monkeypatch):
    _patch_load(monkeypatch, None)
    resp = client.get(
        "/api/v1/zippy/documents/nope", headers={"Accept": "application/json"}
    )
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert "detail" in resp.json()


# ── Legacy /zippy_outputs links ──────────────────────────────────────────────


def test_static_mount_is_gone():
    from app.main import app

    from starlette.staticfiles import StaticFiles

    mounted = [
        r for r in app.routes
        if isinstance(getattr(r, "app", None), StaticFiles)
    ]
    assert not mounted, "the ephemeral per-pod static mount must not come back"
    # And main.py no longer even imports it, so it can't be re-added by accident.
    assert "from fastapi.staticfiles import" not in Path("app/main.py").read_text()


def test_legacy_link_explains_the_loss_instead_of_404ing(client):
    resp = client.get(
        "/zippy_outputs/mom-Acme-2026-05-28-abc123.docx",
        headers={"Accept": "text/html"},
    )
    assert resp.status_code == 410
    body = resp.text.lower()
    assert "no longer available" in body
    assert "generate it again" in body


def test_legacy_link_never_reads_from_disk(client, tmp_path, monkeypatch):
    """No filesystem access means no path-traversal surface."""
    resp = client.get(
        "/zippy_outputs/../../../../etc/passwd", headers={"Accept": "text/html"}
    )
    assert resp.status_code in (404, 410)
    assert "root:" not in resp.text


def test_legacy_link_returns_json_for_api_clients(client):
    resp = client.get(
        "/zippy_outputs/old.docx", headers={"Accept": "application/json"}
    )
    assert resp.status_code == 410
    assert resp.json()["detail"]


# ── Cleanup task wiring ──────────────────────────────────────────────────────


def test_purge_task_is_registered_and_scheduled():
    from app.celery_app import celery_app

    name = "app.tasks.zippy_documents.purge_expired_zippy_documents"
    assert "app.tasks.zippy_documents" in celery_app.conf.include
    schedules = celery_app.conf.beat_schedule
    assert any(entry["task"] == name for entry in schedules.values())


def test_purge_task_does_not_drag_the_doc_generators_into_the_worker():
    """The worker's include list has no zippy_docs; importing the task mustn't add it.

    Checked in a clean subprocess, because anything the rest of this test
    session has already imported would mask a real regression.
    """
    import subprocess
    import sys

    script = (
        "import sys;"
        "import app.tasks.zippy_documents;"
        "print(','.join(m for m in ('docx','openpyxl','pptx') if m in sys.modules))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", (
        f"purge task pulled heavy document libraries into the worker: {proc.stdout}"
    )


def test_importing_the_app_writes_nothing_to_disk(tmp_path):
    """The scratch-dir mkdir must not run at import time.

    It used to, which meant every process that imported the app — the Celery
    worker included — had to be able to write to that path, and a read-only root
    filesystem would have failed boot rather than failing the one call that
    actually needed a file. Pointed at a guaranteed-fresh directory so a leftover
    from another test can't make this pass by accident.
    """
    import os
    import subprocess
    import sys

    fresh = tmp_path / "never-created"
    env = {**os.environ, "ZIPPY_OUTPUT_DIR": str(fresh)}
    script = (
        "import app.main;"
        "import app.tasks.zippy_documents;"
        "import app.services.zippy_docs.base as b;"
        "print(b.ZIPPY_OUTPUT_DIR.exists())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("False")
    assert not fresh.exists()
