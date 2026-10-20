"""Only platform_ tables belong to this migration history; legacy data is separate."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.core.database import Base
from app.platform import models  # noqa: F401

try:
    from app.platform import media_models  # noqa: F401
except ModuleNotFoundError as exc:
    if exc.name != "app.platform.media_models":
        raise

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return name.startswith("platform_") and name != "platform_alembic_version"
    return True


def options():
    return {
        "target_metadata": target_metadata,
        "include_object": include_object,
        "compare_type": True,
        "version_table": "platform_alembic_version",
    }


def run_migrations_offline():
    context.configure(url=settings.DATABASE_URL, literal_binds=True, dialect_opts={"paramstyle": "named"}, **options())
    with context.begin_transaction():
        context.run_migrations()


def run_migrations(connection):
    context.configure(connection=connection, **options())
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = settings.DATABASE_URL
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
