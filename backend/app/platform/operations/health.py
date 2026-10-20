"""Role-specific health checks this instance, not any worker in the cluster."""

import asyncio
import socket
from datetime import timedelta

import redis.asyncio as redis

from app.core.config import settings
from app.core.database import async_session_maker, engine, validate_migrations
from app.platform.ai.guard import database_now
from app.platform.models import InstanceHeartbeat


def instance_id(role):
    return role + ":" + socket.gethostname()


async def pulse(role, *, ready, progress=False):
    async with async_session_maker() as db, db.begin():
        now = await database_now(db)
        row = await db.get(InstanceHeartbeat, instance_id(role))
        if row is None:
            row = InstanceHeartbeat(id=instance_id(role), role=role)
            db.add(row)
        row.last_seen, row.ready = now, ready
        if progress:
            row.last_progress = now


async def readiness(role=None):
    role = role or settings.SERVICE_ROLE
    capabilities = {"facts": False, "async_jobs": False, "realtime": False}
    try:
        async with engine.connect() as conn:
            await validate_migrations(conn)
        capabilities["facts"] = True
    except Exception:
        return {"status": "unready", "role": role, "capabilities": capabilities}, 503
    async def available(url):
        client = redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        try:
            return bool(await client.ping())
        except redis.RedisError:
            return False
        finally:
            await client.aclose()

    capabilities["async_jobs"], capabilities["realtime"] = await asyncio.gather(
        available(settings.CELERY_BROKER_URL), available(settings.REDIS_URL))
    if role != "backend":
        async with async_session_maker() as db:
            now = await database_now(db)
            row = await db.get(InstanceHeartbeat, instance_id(role))
            alive = bool(row and row.ready and row.last_seen > now - timedelta(seconds=60)
                         and row.last_progress and row.last_progress > now - timedelta(seconds=120))
        if not alive or (role in {"worker", "dispatcher"} and not capabilities["async_jobs"]):
            return {"status": "unready", "role": role, "capabilities": capabilities}, 503
    return {"status": "ok" if all(capabilities.values()) else "degraded", "role": role, "capabilities": capabilities}, 200


async def main():
    import sys

    _, status = await asyncio.wait_for(readiness(), timeout=10)
    sys.exit(0 if status == 200 else 1)


if __name__ == "__main__":
    asyncio.run(main())
