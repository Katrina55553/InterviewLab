"""Allowlisted atomic counters; a monitoring failure cannot replace a business result."""

import asyncio
import contextlib
import hashlib
import os
import socket
import time
from datetime import timedelta

from app.core.config import settings
from app.platform.contracts import trace_context
from app.platform.models import CircuitObservation, OperationCounter

NAMES = {"admission", "rejected", "flight_hit", "circuit_open", "lease_lost", "media_backpressure", "ai_timeout"}
SCENES = {"parse", "resume", "questions", "evaluate", "followup", "report", "chat", "vision"}
WORKLOADS = {"interactive", "background", "vision", "tool", "parse", "media"}
WRITE_TIMEOUT_SECONDS = .1


async def observe_circuit(config, breaker):
    if not trace_context():
        return
    from app.platform import runtime
    from app.platform.ai.guard import credential_scope, database_now

    async def write():
        identity = repr((config.get("base_url"), config.get("model"), credential_scope(config), config.get("provider_version")))
        key = f"{socket.gethostname()}:{os.getpid()}:" + hashlib.sha256(identity.encode()).hexdigest()
        async with runtime.async_session_maker() as db, db.begin():
            now = await database_now(db)
            if db.bind.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert
            fields = {"scene": config["scene"] if config["scene"] in SCENES else "other",
                      "state": "half_open" if breaker.probing else "open" if breaker.open_until > time.monotonic() else "closed",
                      "valid_until": now + timedelta(seconds=settings.AI_TIMEOUT_SECONDS if breaker.probing else max(0, breaker.open_until - time.monotonic())),
                      "last_seen": now}
            statement = insert(CircuitObservation).values(id=key, **fields)
            await db.execute(statement.on_conflict_do_update(index_elements=["id"], set_=fields))

    with contextlib.suppress(Exception):
        await asyncio.wait_for(write(), timeout=WRITE_TIMEOUT_SECONDS)


async def record(name, *, scene="other", workload="other", duration_ms=0):
    if name not in NAMES or not trace_context():
        return
    scene = scene if scene in SCENES else "other"
    workload = workload if workload in WORKLOADS else "other"

    async def write():
        from app.platform import runtime

        async with runtime.async_session_maker() as db, db.begin():
            if db.bind.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert
            statement = insert(OperationCounter).values(id=f"{name}:{scene}:{workload}", name=name, scene=scene,
                workload=workload, value=1, duration_ms=max(0, int(duration_ms)))
            await db.execute(statement.on_conflict_do_update(index_elements=["id"], set_={
                "value": OperationCounter.value + 1,
                "duration_ms": OperationCounter.duration_ms + max(0, int(duration_ms))}))

    with contextlib.suppress(Exception):
        await asyncio.wait_for(write(), timeout=WRITE_TIMEOUT_SECONDS)
