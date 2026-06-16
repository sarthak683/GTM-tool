"""Dismiss all open system-generated tasks (manual-tasks-only switch-over).

Revision ID: 085
Revises: 084
Create Date: 2026-06-08

The product is moving to "manual tasks only": going forward the task list
contains only tasks a human created (task_type='manual'). Code-side, every
automated generator is gated behind settings.ENABLE_SYSTEM_TASKS (see
app/services/tasks.py::refresh_system_tasks_for_entity). This migration cleans
up the rows those generators already wrote so reps don't see orphaned
auto-tasks after the switch.

We DISMISS rather than hard-delete:
  - preserves history / audit trail and any FK references (comments, etc.),
  - keeps the change reversible in spirit (rows remain, just out of the
    open queue),
  - matches how the app already retires stale system tasks (status='dismissed').

Only OPEN system tasks are touched. Manual tasks (task_type='manual') and
already-closed system tasks are left exactly as they are.

Idempotent: re-running matches zero rows once the open system tasks are gone.
No schema change. downgrade() is intentionally a no-op — we do not resurrect
auto-generated tasks (they'd just be regenerated/owned by the live flag, and
the whole point of the change is to stop showing them).
"""
from alembic import op
import sqlalchemy as sa


revision = "085"
down_revision = "084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE tasks
               SET status = 'dismissed',
                   completed_at = COALESCE(completed_at, now()),
                   updated_at = now()
             WHERE task_type = 'system'
               AND status = 'open'
            """
        )
    )


def downgrade() -> None:
    # Intentional no-op: dismissed auto-tasks are not restored.
    pass
