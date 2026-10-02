"""Create sequences, sequence_steps, enrollments tables; extend tasks.

Revision ID: 140
Revises: 139
Create Date: 2026-09-24

The new, reusable multi-contact Outreach Sequence system (see
app/models/sequence.py). Replaces the old per-contact "Outreach Sequence"
(outreach_sequences/outreach_steps, migration 002) — those tables and their
historical data are left untouched, just no longer read from or written to.

tasks gets three new nullable columns so a cadence-sourced task can carry
its enrollment/step context: source (already existed as a free string —
"cadence" is just a new value, no schema change needed for it), plus
enrollment_id and step_id.
"""

import sqlalchemy as sa
from alembic import op


revision = "140"
down_revision = "139"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sequences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sequences_owner_id"), "sequences", ["owner_id"])

    op.create_table(
        "sequence_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sequence_id", sa.Uuid(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("day_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("send_via", sa.String(), nullable=True),
        sa.Column("instantly_campaign_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["sequence_id"], ["sequences.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sequence_steps_sequence_id"), "sequence_steps", ["sequence_id"])

    op.create_table(
        "enrollments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_id", sa.Uuid(), nullable=False),
        sa.Column("enrolled_by", sa.Uuid(), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(), nullable=False),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["sequence_id"], ["sequences.id"]),
        sa.ForeignKeyConstraint(["enrolled_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_enrollments_contact_id"), "enrollments", ["contact_id"])
    op.create_index(op.f("ix_enrollments_sequence_id"), "enrollments", ["sequence_id"])
    # One ACTIVE enrollment per contact — partial unique index, not enforced
    # for completed/removed rows (a contact can run several sequences over
    # time, just never two at once).
    op.execute(
        """
        CREATE UNIQUE INDEX ix_enrollments_one_active_per_contact
        ON enrollments (contact_id)
        WHERE status = 'active'
        """
    )

    op.add_column("tasks", sa.Column("enrollment_id", sa.Uuid(), nullable=True))
    op.add_column("tasks", sa.Column("step_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_tasks_enrollment_id", "tasks", "enrollments", ["enrollment_id"], ["id"])
    op.create_foreign_key("fk_tasks_step_id", "tasks", "sequence_steps", ["step_id"], ["id"])
    op.create_index(op.f("ix_tasks_enrollment_id"), "tasks", ["enrollment_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_tasks_enrollment_id"), table_name="tasks")
    op.drop_constraint("fk_tasks_step_id", "tasks", type_="foreignkey")
    op.drop_constraint("fk_tasks_enrollment_id", "tasks", type_="foreignkey")
    op.drop_column("tasks", "step_id")
    op.drop_column("tasks", "enrollment_id")

    op.execute("DROP INDEX IF EXISTS ix_enrollments_one_active_per_contact")
    op.drop_index(op.f("ix_enrollments_sequence_id"), table_name="enrollments")
    op.drop_index(op.f("ix_enrollments_contact_id"), table_name="enrollments")
    op.drop_table("enrollments")

    op.drop_index(op.f("ix_sequence_steps_sequence_id"), table_name="sequence_steps")
    op.drop_table("sequence_steps")

    op.drop_index(op.f("ix_sequences_owner_id"), table_name="sequences")
    op.drop_table("sequences")
