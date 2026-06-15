"""Add shared tasks and task comments."""

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("task_type", sa.String(), nullable=False, server_default="manual"),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(), nullable=False, server_default="medium"),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("recommended_action", sa.String(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("action_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("system_key", sa.String(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_to_id", sa.Uuid(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["assigned_to_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_entity_type", "tasks", ["entity_type"])
    op.create_index("ix_tasks_entity_id", "tasks", ["entity_id"])
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])
    op.create_index("ix_tasks_system_key", "tasks", ["system_key"])
    op.create_index("ix_tasks_created_by_id", "tasks", ["created_by_id"])
    op.create_index("ix_tasks_assigned_to_id", "tasks", ["assigned_to_id"])

    op.create_table(
        "task_comments",
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_comments_task_id", "task_comments", ["task_id"])
    op.create_index("ix_task_comments_created_by_id", "task_comments", ["created_by_id"])
    op.create_index("ix_task_comments_created_at", "task_comments", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_task_comments_created_at", table_name="task_comments")
    op.drop_index("ix_task_comments_created_by_id", table_name="task_comments")
    op.drop_index("ix_task_comments_task_id", table_name="task_comments")
    op.drop_table("task_comments")

    op.drop_index("ix_tasks_assigned_to_id", table_name="tasks")
    op.drop_index("ix_tasks_created_by_id", table_name="tasks")
    op.drop_index("ix_tasks_system_key", table_name="tasks")
    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_index("ix_tasks_entity_id", table_name="tasks")
    op.drop_index("ix_tasks_entity_type", table_name="tasks")
    op.drop_table("tasks")
