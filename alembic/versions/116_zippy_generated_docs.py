"""Durable storage for Zippy-generated documents (zippy_generated_docs)

Revision ID: 116
Revises: 115
Create Date: 2026-08-17

Zippy's document generators wrote their .docx/.xlsx/.pptx output to
``/app/storage/zippy_outputs`` inside the backend container and published it
through a ``StaticFiles`` mount. Production runs two backend replicas with no
shared volume between them, which made that arrangement wrong in two ways at
once: the file existed only on the pod that generated it, so a download link
404'd whenever the request was balanced onto the other replica; and the
container's writable layer is ephemeral, so every restart, redeploy or
reschedule destroyed every document. Both prod pods' output directories were
empty minutes after a routine deploy — nothing generated before it survived.

The bytes cannot simply be streamed instead. The link is not an immediate
download: it is an artifact chip persisted in ``zippy_messages.artifacts`` and
clicked whenever the user scrolls back to that turn. Nor can the file be
regenerated on demand — every generator rewrites a Drive template through a
Claude call, and the inputs that drove it (transcript, attendees, context) are
tool arguments that were never persisted, so a re-run would cost another model
call and produce a different document.

So this table holds the bytes. Postgres is already shared by both replicas and
already backed up; a ReadWriteMany PVC would have solved the same problem while
coupling the fix to storage provisioning, for a feature that emits a handful of
few-hundred-KB files.

Notes:
  - ``token`` is a capability, not an id. The download route is reached by a
    plain anchor in the chat transcript, which cannot carry the app's bearer
    token, so the route is unauthenticated and this 256-bit string is the only
    credential. UNIQUE both to enforce that and because the route looks up by it.
  - ``user_id`` is deliberately NOT a foreign key. It records provenance for
    audit and cleanup; a nullable FK would only add a way for deleting a user to
    fail on a stale document row.
  - ``expires_at`` is indexed because the nightly purge
    (app.tasks.zippy_documents) scans on it. Retention defaults to 30 days;
    expired rows are reported to the user as expired, not as a bare 404.
  - No backfill is possible. The files this replaces are already gone.
"""
from alembic import op
import sqlalchemy as sa

revision = "116"
down_revision = "115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zippy_generated_docs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False, server_default=""),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column(
            "content_type",
            sa.String(),
            nullable=False,
            server_default="application/octet-stream",
        ),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_zippy_generated_docs_token"),
    )
    # No separate index on `token`: the UNIQUE constraint above already creates
    # a btree that the download lookup uses. A second one would just be a
    # duplicate to maintain on every insert.
    op.create_index(
        "ix_zippy_generated_docs_user_id", "zippy_generated_docs", ["user_id"]
    )
    op.create_index(
        "ix_zippy_generated_docs_created_at", "zippy_generated_docs", ["created_at"]
    )
    op.create_index(
        "ix_zippy_generated_docs_expires_at", "zippy_generated_docs", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_zippy_generated_docs_expires_at", table_name="zippy_generated_docs"
    )
    op.drop_index(
        "ix_zippy_generated_docs_created_at", table_name="zippy_generated_docs"
    )
    op.drop_index(
        "ix_zippy_generated_docs_user_id", table_name="zippy_generated_docs"
    )
    op.drop_table("zippy_generated_docs")
