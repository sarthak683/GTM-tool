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


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
