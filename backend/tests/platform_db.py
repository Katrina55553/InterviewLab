"""Opt-in real PostgreSQL tests use a unique disposable schema, never public tables."""

import importlib.util
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@dataclass
class PlatformDatabase:
    engine: AsyncEngine
    sessions: async_sessionmaker
    dialect: str


def postgres_test_url() -> str | None:
    value = os.environ.get("PLATFORM_TEST_DATABASE_URL")
    if value and not value.startswith("postgresql+asyncpg://"):
        raise ValueError("PLATFORM_TEST_DATABASE_URL must use postgresql+asyncpg://")
    return value


def _migrate(connection):
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "migrations"))
    for entry in reversed(list(scripts.walk_revisions())):
        spec = importlib.util.spec_from_file_location("platform_test_" + entry.revision, entry.path)
        revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(revision)
        with Operations.context(MigrationContext.configure(connection)):
            revision.upgrade()


@asynccontextmanager
async def isolated_database(kind: str, tmp_path):
    admin = None
    schema = None
    if kind == "postgresql":
        url = postgres_test_url()
        if not url:
            raise ValueError("Real PostgreSQL tests need PLATFORM_TEST_DATABASE_URL")
        schema = "platform_test_" + uuid.uuid4().hex
        admin = create_async_engine(url, poolclass=NullPool)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}}, poolclass=NullPool)
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_migrate)
        yield PlatformDatabase(engine, async_sessionmaker(engine, expire_on_commit=False), kind)
    finally:
        await engine.dispose()
        if admin is not None:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()
