"""Persistent logical-node results. Expiry never authorizes an automatic AI retry."""

import asyncio
import contextlib
import hashlib
import json
import time
from datetime import timedelta

import redis.asyncio as redis
from sqlalchemy import update

from app.core.config import settings
from app.platform.ai.guard import credential_scope, database_now, lock_scope
from app.platform.contracts import PlatformError, error_payload, trace_id, traced
from app.platform.models import AiFlight, new_id
from app.platform.operations.faults import checkpoint
from app.platform.operations.telemetry import record


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


async def notify(key):
    client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=.1, socket_timeout=.1)
    try:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(client.publish("platform:flight:" + key, "changed"), timeout=.1)
    finally:
        await client.aclose()


async def wait_notification(key, seconds):
    """Notifications reduce waiting; a missed message always falls back to PG."""
    started = time.monotonic()
    client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=.1, socket_timeout=.2)
    try:
        async with asyncio.timeout(seconds), client.pubsub() as subscription:
            await subscription.subscribe("platform:flight:" + key)
            while True:
                if await subscription.get_message(ignore_subscribe_messages=True, timeout=seconds):
                    return
    except (redis.RedisError, TimeoutError, OSError):
        await asyncio.sleep(max(0, seconds - (time.monotonic() - started)))
    finally:
        await client.aclose()


async def run(sessions, context, config, inputs, invoke):
    if not context or not context.get("operation_id"):
        return await invoke()
    base = [context.get(k) for k in ("user_id", "resource_id", "operation_id", "snapshot_version")]
    frozen = {**config}
    if frozen.get("encrypted_key"):
        frozen["encrypted_key"] = credential_scope(config)
    base.extend([frozen, inputs])
    generation = context.get("generation", 0)
    key = digest([base, generation])
    previous = digest([base, generation - 1]) if generation else None
    owner = new_id()
    deadline = time.monotonic() + settings.AI_TIMEOUT_SECONDS
    while True:
        async with sessions() as db, db.begin():
            await lock_scope(db, "flight:" + digest(base))
            now = await database_now(db)
            # Only explicit application-linked retries can reuse a previous known result.
            for old_generation in range(generation, -1, -1):
                old_key = digest([base, old_generation])
                old = await db.get(AiFlight, old_key)
                if old and old.status == "completed":
                    await record("flight_hit", scene=config.get("scene", "other"))
                    return old.result["value"]
            row = await db.get(AiFlight, key)
            if not row:
                db.add(AiFlight(id=key, operation_id=context["operation_id"], generation=generation,
                    previous_id=previous, status="running", owner_token=owner,
                    lease_until=now + timedelta(seconds=settings.AI_COMMAND_LEASE_SECONDS), trace_id=trace_id()))
                break
            if row.status != "running" or row.lease_until <= now:
                if row.status == "running":
                    row.status, row.owner_token = "unknown", None
                    row.error = error_payload(PlatformError("AI_RESULT_UNKNOWN", "执行者退出，需确认后创建新尝试", outcome="unknown"))
                failure = dict(row.error or {})
            else:
                failure = None
        if failure is not None:
            raise PlatformError(failure.get("code", "AI_RESULT_UNKNOWN"), failure.get("message", "调用结果未知"),
                                outcome=failure.get("outcome", "unknown"), retry_policy=failure.get("retry_policy", "manual"))
        if time.monotonic() >= deadline:
            raise PlatformError("AI_OVERLOADED", "等待已有节点结果超时，本次未发送")
        # PG polling remains authoritative even when Redis and all notifications disappear.
        await wait_notification(key, min(.2, max(0, deadline - time.monotonic())))

    async def heartbeat():
        while True:
            await asyncio.sleep(max(1, settings.AI_COMMAND_LEASE_SECONDS / 3))
            async with sessions() as db, db.begin():
                now = await database_now(db)
                changed = await db.execute(update(AiFlight).where(AiFlight.id == key, AiFlight.owner_token == owner,
                    AiFlight.status == "running", AiFlight.lease_until > now).values(
                    lease_until=now + timedelta(seconds=settings.AI_COMMAND_LEASE_SECONDS)))
                if changed.rowcount != 1:
                    return

    task = asyncio.create_task(heartbeat())
    try:
        with traced(flight_id=key):
            result = await invoke()
        await checkpoint("before_flight_save")
        async with sessions() as db, db.begin():
            now = await database_now(db)
            changed = await db.execute(update(AiFlight).where(AiFlight.id == key, AiFlight.owner_token == owner,
                AiFlight.status == "running", AiFlight.lease_until > now).values(status="completed", result={"value": result}, owner_token=None))
            if changed.rowcount != 1:
                raise PlatformError("AI_RESULT_UNKNOWN", "节点租约丢失，迟到结果未接受", outcome="unknown")
        await checkpoint("after_flight_save")
        await notify(key)
        return result
    except BaseException as exc:
        error = exc if isinstance(exc, PlatformError) else PlatformError("AI_RESULT_UNKNOWN", "节点执行中断，请确认后重试", outcome="unknown")
        with contextlib.suppress(Exception):
            async with sessions() as db, db.begin():
                now = await database_now(db)
                await db.execute(update(AiFlight).where(AiFlight.id == key, AiFlight.owner_token == owner,
                    AiFlight.status == "running", AiFlight.lease_until > now).values(
                    status="unknown" if error.unknown else "failed", error=error_payload(error), owner_token=None))
        await notify(key)
        raise
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
