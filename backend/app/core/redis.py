"""Redis 连接模块

提供 Redis 连接池和便捷方法。
"""

import contextlib
import logging

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# Redis 连接池
redis_pool: redis.ConnectionPool | None = None
redis_client: redis.Redis | None = None


async def init_redis() -> None:
    """初始化 Redis 连接池
    带容错处理：Redis 不可用时记录警告但不阻断服务启动。
    """
    global redis_pool, redis_client
    redis_pool = redis.ConnectionPool.from_url(
        settings.REDIS_URL, max_connections=20, decode_responses=True,
        socket_connect_timeout=1, socket_timeout=1,
    )
    redis_client = redis.Redis(connection_pool=redis_pool)
    try:
        await redis_client.ping()
        logger.info("redis_connected")
    except redis.RedisError:
        # Retain the client: its pool reconnects on the next operation after recovery.
        logger.warning("redis_degraded")


async def close_redis() -> None:
    """关闭 Redis 连接"""
    global redis_client, redis_pool

    if redis_client:
        await redis_client.close()
        redis_client = None

    if redis_pool:
        await redis_pool.disconnect()
        redis_pool = None


def get_redis() -> redis.Redis:
    """获取 Redis 客户端（依赖注入用）"""
    if redis_client is None:
        raise RuntimeError("Redis 未初始化，请先调用 init_redis()")
    return redis_client


async def cache_get(key: str) -> str | None:
    """从缓存获取值"""
    if redis_client is None:
        return None
    try:
        return await redis_client.get(key)
    except Exception:
        return None


async def cache_set(key: str, value: str, expire: int = 3600) -> None:
    """设置缓存值"""
    if redis_client is None:
        return
    with contextlib.suppress(Exception):
        await redis_client.setex(key, expire, value)


async def cache_delete(key: str) -> None:
    """删除缓存"""
    if redis_client is None:
        return
    with contextlib.suppress(Exception):
        await redis_client.delete(key)
