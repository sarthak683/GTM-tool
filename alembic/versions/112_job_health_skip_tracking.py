"""Track "ran but did nothing" separately from "worked" in job_health

Revision ID: 112
Revises: 111
Create Date: 2026-08-15

Every scheduled task that returns {"status": "skipped"} — a disabled feature, a
missing integration token, a self-throttle — was recorded as a success. On
2026-08-15 production showed all 14 jobs green while tl;dv had imported nothing
since April, no pre-meeting brief had been sent in August, and personal inbox
sync had been off since the previous evening. The panel could not have shown
otherwise: "ran" and "worked" were the same field.

last_success_at keeps its meaning (the task did work). last_effective_at is new
and, together with last_skip_reason, lets the panel say *why* a job has been
idle rather than just that it ran.
"""

import sqlalchemy as sa
from alembic import op

revision = "112"
down_revision = "111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # When the task last did real work. Seeded from last_success_at so existing
    # rows don't all read "never" on the first render after deploy.
    op.add_column(
        "job_health",
        sa.Column("last_effective_at", sa.DateTime(), nullable=True),
    )
    op.execute("UPDATE job_health SET last_effective_at = last_success_at")

    # Why the last run did nothing, e.g. "zippy_only_email_sync",
    # "gmail not connected", "disabled". NULL when the run did work.
    op.add_column(
        "job_health",
        sa.Column("last_skip_reason", sa.String(), nullable=True),
    )

    op.add_column(
        "job_health",
        sa.Column("skips_total", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("job_health", "skips_total")
    op.drop_column("job_health", "last_skip_reason")
    op.drop_column("job_health", "last_effective_at")
