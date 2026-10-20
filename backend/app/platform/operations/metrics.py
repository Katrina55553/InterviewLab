"""Database-derived exporter includes every web/worker process exactly once.

Run one exporter per database. Do not sum copies of this global view across replicas.
No Prometheus prefork registry or arbitrary model/provider label values are used.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from sqlalchemy import func, select

from app.core.database import async_session_maker
from app.platform.ai.guard import database_now
from app.platform.models import (
    AiCall,
    AiFlight,
    AiPermit,
    CircuitObservation,
    InstanceHeartbeat,
    OperationCounter,
    Outbox,
)
from app.platform.operations.telemetry import NAMES, WORKLOADS


@asynccontextmanager
async def lifespan(app):
    from app.core.config import settings
    from app.platform.operations.logging import configure_logging

    settings.validate_runtime()
    configure_logging()
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
SCENES = {"resume", "questions", "evaluate", "followup", "report", "chat", "vision"}


async def render_metrics():
    lines = []

    def sample(name, value, **labels):
        suffix = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}" if labels else ""
        lines.append(f"platform_{name}{suffix} {float(value or 0)}")

    async with async_session_maker() as db:
        now = await database_now(db)
        async def histogram(name, duration, model, conditions, bounds, **labels):
            base = select(func.count()).select_from(model).where(*conditions, duration >= 0)
            for boundary in bounds:
                sample(name + "_bucket", await db.scalar(base.where(duration <= boundary)), **labels, le=boundary)
            count = await db.scalar(base)
            sample(name + "_bucket", count, **labels, le="+Inf")
            sample(name + "_count", count, **labels)
            sample(name + "_sum", await db.scalar(select(func.sum(duration)).select_from(model).where(*conditions, duration >= 0)), **labels)

        rows = (await db.execute(select(AiCall.scene, AiCall.status, func.count(), func.sum(AiCall.duration_ms))
            .group_by(AiCall.scene, AiCall.status))).all()
        outcomes = ("completed", "failed", "unknown", "interrupted", "not_sent", "other")
        attempts = {}
        for scene, status, count, duration in rows:
            key = (scene if scene in SCENES else "other", status if status in (*outcomes, "running") else "other")
            previous = attempts.get(key, (0, 0))
            attempts[key] = (previous[0] + count, previous[1] + (duration or 0))
        for scene in sorted(SCENES | {"other"}):
            sample("ai_inflight_attempts", attempts.get((scene, "running"), (0, 0))[0], scene=scene)
            sample("ai_requests_total", sum(attempts.get((scene, status), (0, 0))[0] for status in (*outcomes, "running")), scene=scene)
            for status in outcomes:
                count, duration = attempts.get((scene, status), (0, 0))
                sample("ai_attempts_total", count, scene=scene, outcome=status)
                sample("ai_attempt_duration_seconds_total", duration / 1000, scene=scene, outcome=status)
        await histogram("ai_duration_seconds", AiCall.duration_ms / 1000, AiCall,
                        [AiCall.status != "running", AiCall.duration_ms.is_not(None)], (.1, .5, 1, 5, 15, 30, 60, 120, 600))
        permits = dict((await db.execute(select(AiPermit.workload, func.count()).where(AiPermit.lease_until > now).group_by(AiPermit.workload))).all())
        for kind in ("interactive", "background", "vision"):
            sample("active_permits", permits.get(kind, 0), workload=kind)
        circuits = {(scene, state): count for scene, state, count in (await db.execute(
            select(CircuitObservation.scene, CircuitObservation.state, func.count()).where(CircuitObservation.valid_until > now)
            .group_by(CircuitObservation.scene, CircuitObservation.state))).all()}
        for scene in sorted(SCENES | {"other"}):
            for state in ("open", "half_open"):
                sample("circuits", circuits.get((scene, state), 0), scene=scene, state=state)
        flights = dict((await db.execute(select(AiFlight.status, func.count()).group_by(AiFlight.status))).all())
        for status in ("running", "completed", "failed", "unknown"):
            sample("flights", flights.get(status, 0), status=status)
        queued = await db.scalar(select(func.min(Outbox.created_at)).where(Outbox.execution_status == "queued"))
        sample("outbox_oldest_seconds", (now - queued).total_seconds() if queued else 0)
        for kind, status, count in (await db.execute(select(Outbox.kind, Outbox.execution_status, func.count()).group_by(Outbox.kind, Outbox.execution_status))).all():
            if kind in {"prepare", "report"} and status in {"queued", "running", "completed", "failed", "unknown"}:
                sample("jobs", count, workload=kind, status=status)
        def seconds(end, start):
            return (func.julianday(end) - func.julianday(start)) * 86400 if db.bind.dialect.name == "sqlite" else func.extract("epoch", end - start)

        for kind in ("prepare", "report"):
            await histogram("job_wait_seconds", seconds(Outbox.started_at, Outbox.created_at), Outbox,
                            [Outbox.kind == kind, Outbox.started_at.is_not(None)], (1, 5, 15, 60, 300, 900), workload=kind)
            await histogram("job_execution_seconds", seconds(Outbox.finished_at, Outbox.started_at), Outbox,
                            [Outbox.kind == kind, Outbox.started_at.is_not(None), Outbox.finished_at.is_not(None)], (1, 5, 15, 60, 300, 900), workload=kind)
        for role in ("worker", "dispatcher", "inspector"):
            rows = (await db.scalars(select(InstanceHeartbeat).where(InstanceHeartbeat.role == role))).all()
            sample("healthy_instances", sum(r.ready and (now - r.last_seen).total_seconds() < 60 for r in rows), role=role)
        sample("database_up", 1)
        for counter in (await db.scalars(select(OperationCounter))).all():
            if counter.name in NAMES and counter.scene in SCENES | {"other"} and counter.workload in WORKLOADS | {"other"}:
                sample(counter.name + "_total", counter.value, scene=counter.scene, workload=counter.workload)
                sample(counter.name + "_seconds_sum", counter.duration_ms / 1000, scene=counter.scene, workload=counter.workload)
    return "\n".join(lines) + "\n"


@app.get("/metrics")
async def metrics():
    try:
        value = await render_metrics()
    except Exception:
        value = "platform_database_up 0\n"
    return Response(value, media_type="text/plain; version=0.0.4")
