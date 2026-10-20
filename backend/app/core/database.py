"""数据库连接模块

使用 SQLAlchemy async engine 和 async session maker。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)

# 创建异步引擎
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # SQL parameters may contain private resume/chat content.
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,  # 自动检测断开的连接
    pool_recycle=300,
    connect_args={"timeout": 10},
)

# 创建异步会话工厂
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


async def get_db() -> AsyncSession:
    """获取数据库会话（依赖注入用）"""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Validate migration state; application startup never mutates database schema."""
    async with engine.connect() as conn:
        await validate_migrations(conn)
    logger.info("平台数据库迁移检查通过")


def migration_heads() -> set[str]:
    from pathlib import Path

    from alembic.script import ScriptDirectory

    return set(ScriptDirectory(str(Path(__file__).resolve().parents[2] / "migrations")).get_heads())


async def validate_migrations(conn) -> None:
    from sqlalchemy import text

    versions = set((await conn.execute(text("SELECT version_num FROM platform_alembic_version"))).scalars())
    if versions != migration_heads():
        raise RuntimeError("数据库尚未迁移，请先运行 python -m app.platform.cli migrate")


async def close_db() -> None:
    """关闭数据库连接"""
    await engine.dispose()
