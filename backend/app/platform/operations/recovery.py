"""Active repair never invokes a graph or synthesizes an AI result."""

import asyncio

from sqlalchemy import delete, select, update

from app.core.database import async_session_maker
from app.platform.ai.guard import database_now
from app.platform.models import AiCall, AiFlight, AiPermit, CircuitObservation, Command, Interview, Outbox, Resume


async def sweep_once():
    async with async_session_maker() as db:
        commands = list((await db.scalars(select(Command).where(Command.status == "running"))).all())
        jobs = list((await db.scalars(select(Outbox).where(Outbox.execution_status == "running"))).all())
    # Follow application lock order: aggregate before execution record.
    for command in commands:
        async with async_session_maker() as db, db.begin():
            await db.scalar(select(Interview).where(Interview.id == command.interview_id).with_for_update())
            now = await database_now(db)
            await db.execute(update(Command).where(Command.id == command.id, Command.status == "running",
                Command.lease_until <= now).values(status="unknown", owner_token=None,
                error="执行者租约已过期，调用结果未知；请确认后手动重试"))
    for job in jobs:
        async with async_session_maker() as db, db.begin():
            model = Resume if job.kind == "prepare" else Interview
            aggregate = await db.scalar(select(model).where(model.id == job.aggregate_id).with_for_update())
            row = await db.scalar(select(Outbox).where(Outbox.id == job.id).with_for_update())
            now = await database_now(db)
            if row.execution_status == "running" and row.lease_until <= now:
                row.execution_status, row.owner_token = "unknown", None
                row.finished_at = now
                row.error = "任务执行者退出，调用结果未知；请确认后手动重试"
                if aggregate and aggregate.active_job_id == row.id:
                    aggregate.error = row.error
                    if model is Resume:
                        aggregate.status = "failed"
    async with async_session_maker() as db, db.begin():
        now = await database_now(db)
        await db.execute(update(AiFlight).where(AiFlight.status == "running", AiFlight.lease_until <= now).values(
            status="unknown", owner_token=None, error={"code": "AI_RESULT_UNKNOWN", "message": "节点执行者退出", "outcome": "unknown"}))
        await db.execute(update(AiCall).where(AiCall.status == "running", AiCall.outcome == "not_sent", AiCall.lease_until <= now).values(
            status="not_sent", error_code="EXECUTOR_EXITED_BEFORE_SEND"))
        await db.execute(update(AiCall).where(AiCall.status == "running", AiCall.lease_until <= now).values(
            status="unknown", outcome="unknown", error_code="AI_RESULT_UNKNOWN"))
        await db.execute(delete(AiPermit).where(AiPermit.lease_until <= now))
        from datetime import timedelta

        await db.execute(delete(CircuitObservation).where(CircuitObservation.last_seen < now - timedelta(hours=1)))


async def main():
    import logging

    from app.core.config import settings
    from app.platform.operations.health import pulse
    from app.platform.operations.logging import configure_logging

    settings.validate_runtime()
    configure_logging()
    while True:
        try:
            await sweep_once()
            await pulse("inspector", ready=True, progress=True)
        except Exception:
            logging.getLogger(__name__).exception("inspection_failed")
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
