"""Let tasks outlive their sequence step/enrollment on edit or delete.

Editing or deleting a Sequence replaces/removes its SequenceStep rows.
Historical Task rows must survive that (task history is never deleted just
because the template it came from changed) — so tasks.step_id and
tasks.enrollment_id need ON DELETE SET NULL instead of the default RESTRICT,
which was blocking any Sequence edit/delete once a task had ever been
created against one of its steps.

Revision ID: 141_cadence_task_fk_set_null
Revises: 140_outreach_sequence_tables
Create Date: 2026-09-24
"""
from alembic import op

revision = "141"
down_revision = "140"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("fk_tasks_step_id", "tasks", type_="foreignkey")
    op.create_foreign_key(
        "fk_tasks_step_id", "tasks", "sequence_steps", ["step_id"], ["id"], ondelete="SET NULL"
    )
    op.drop_constraint("fk_tasks_enrollment_id", "tasks", type_="foreignkey")
    op.create_foreign_key(
        "fk_tasks_enrollment_id", "tasks", "enrollments", ["enrollment_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_tasks_step_id", "tasks", type_="foreignkey")
    op.create_foreign_key("fk_tasks_step_id", "tasks", "sequence_steps", ["step_id"], ["id"])
    op.drop_constraint("fk_tasks_enrollment_id", "tasks", type_="foreignkey")
    op.create_foreign_key("fk_tasks_enrollment_id", "tasks", "enrollments", ["enrollment_id"], ["id"])
