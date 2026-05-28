"""Add call_recordings table.

Revision ID: 072
Revises: 071
Create Date: 2026-05-27

Stores the metadata + transcript + AI-suggested disposition for
in-browser call recordings (rep speaks on phone in speakerphone, laptop
mic captures both sides). Audio bytes are NOT persisted — the Celery
transcription task holds them in /tmp for ~10-30 seconds and deletes
after Whisper returns. The transcript is the long-lived record.

Lifecycle states (`status`):
  uploaded     — POST landed, audio is on disk in worker /tmp
  transcribing — Whisper call in flight
  classifying  — Claude disposition classification in flight
  ready        — transcript + ai_disposition populated, audio deleted
  failed       — transcription or classification errored (failure_reason set)
"""

from alembic import op
import sqlalchemy as sa


revision = "072"
down_revision = "071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_recordings",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "contact_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_by_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="uploaded"),
        # Captured the moment the rep clicked Record after acknowledging
        # the consent checkbox. Persisted for legal/compliance audit.
        sa.Column("consent_acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("audio_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("audio_size_bytes", sa.Integer(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        # AI outputs — all nullable so a partial run is still queryable.
        sa.Column("ai_disposition", sa.String(), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index(
        op.f("ix_call_recordings_contact_id_created_at"),
        "call_recordings",
        ["contact_id", "created_at"],
    )
    op.create_index(
        op.f("ix_call_recordings_status"),
        "call_recordings",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_call_recordings_status"), table_name="call_recordings")
    op.drop_index(op.f("ix_call_recordings_contact_id_created_at"), table_name="call_recordings")
    op.drop_table("call_recordings")
