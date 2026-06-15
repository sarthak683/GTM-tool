"""Add job_health table for the admin scheduled-job monitor.

Revision ID: 079
Revises: 078
Create Date: 2026-06-01

One row per beat-scheduled task, upserted by a Celery task_postrun signal, so
the admin "System Health" panel can show last-run / status / staleness and a
silently-dead scheduler (reports, syncs, reminders) surfaces immediately.
"""

from alembic import op
import sqlalchemy as sa


revision = "079"
down_revision = "078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_health",
        sa.Column("task_name", sa.String(), primary_key=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_duration_ms", sa.Integer(), nullable=True),
        sa.Column("runs_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failures_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("job_health")
