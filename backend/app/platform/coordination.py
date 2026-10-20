"""Redis single-flight is an optimization; database reservation/fencing is authoritative."""

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager

import redis.asyncio as redis

from app.core.config import settings

RENEW = "if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('expire',KEYS[1],ARGV[2]) else return 0 end"
RELEASE = "if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"


@asynccontextmanager
async def singleflight(key: str, owner: str):
    """Only call after owning the durable command. Redis loss never authorizes a second owner."""
    client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    acquired = False
    renewer = None
    ttl = settings.AI_COMMAND_LEASE_SECONDS
    try:
        with contextlib.suppress(redis.RedisError):
            acquired = bool(await client.set(f"platform:flight:{key}", owner, nx=True, ex=ttl))

        async def renew():
            while True:
                await asyncio.sleep(max(1, ttl // 3))
                try:
                    if not await client.eval(RENEW, 1, f"platform:flight:{key}", owner, ttl):
                        return
                except redis.RedisError:
                    return

        if acquired:
            renewer = asyncio.create_task(renew())
        yield
    finally:
        if renewer:
            renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewer
        if acquired:
            with contextlib.suppress(redis.RedisError):
                await client.eval(RELEASE, 1, f"platform:flight:{key}", owner)
        await client.aclose()


async def notify_event(interview_id: str, event: dict) -> None:
    client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    try:
        await client.publish(f"platform:events:{interview_id}", json.dumps(event, ensure_ascii=False))
    except redis.RedisError:
        pass
    finally:
        await client.aclose()
