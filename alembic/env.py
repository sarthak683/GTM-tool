import os
from logging.config import fileConfig
from dotenv import load_dotenv

load_dotenv()  # Load .env file before reading environment variables

from sqlalchemy import engine_from_config, pool
from alembic import context
from sqlmodel import SQLModel

# Import all models to register them with SQLModel.metadata
from app.models.company import Company  # noqa: F401
from app.models.contact import Contact  # noqa: F401
from app.models.deal import Deal  # noqa: F401
from app.models.activity import Activity  # noqa: F401
from app.models.assignment_update import AssignmentUpdate  # noqa: F401
from app.models.sourcing_batch import SourcingBatch  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.user_alias import UserAlias  # noqa: F401
from app.models.outreach import OutreachSequence  # noqa: F401
from app.models.signal import Signal  # noqa: F401
from app.models.meeting import Meeting  # noqa: F401
from app.models.battlecard import Battlecard  # noqa: F401
from app.models.custom_demo import CustomDemo  # noqa: F401
from app.models.sales_resource import SalesResource  # noqa: F401
from app.models.data_room_item import DataRoomItem  # noqa: F401
from app.models.company_stage_milestone import CompanyStageMilestone  # noqa: F401
from app.models.angel import AngelInvestor, AngelMapping  # noqa: F401
from app.models.task import Task, TaskComment  # noqa: F401
from app.models.reminder import Reminder  # noqa: F401
from app.models.settings import WorkspaceSettings  # noqa: F401
from app.models.push_subscription import PushSubscription  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

# Override URL from environment variable (set by docker-compose)
sync_url = os.environ.get("SYNC_DATABASE_URL")
if sync_url:
    config.set_main_option("sqlalchemy.url", sync_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# Arbitrary constant identifying "the Alembic upgrade lock" for this database.
# Any two migration runners that use the same key serialize on it.
_MIGRATION_LOCK_KEY = 0x42_EAC0  # "beacon"


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Production runs `alembic upgrade head` from an initContainer on every
        # backend replica, so two upgrade processes can start at the same time
        # (rollouts, scale-ups, node replacements). Alembic itself takes no
        # lock, and concurrent DDL against the same schema can double-apply or
        # deadlock. A session-level advisory lock serializes them: the second
        # runner blocks here, then finds every revision already applied and
        # exits cleanly. The lock releases automatically when the connection
        # closes, even if the migration crashes.
        connection.exec_driver_sql(
            "SELECT pg_advisory_lock(%(key)s)", {"key": _MIGRATION_LOCK_KEY}
        )
        # exec_driver_sql autobegins a transaction. Close it now: if a
        # transaction is already open when Alembic configures, it assumes the
        # caller owns transaction management and never commits — every
        # migration then silently rolls back when the connection closes. The
        # advisory lock is session-level, so it survives this commit and is
        # held until the connection closes.
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
