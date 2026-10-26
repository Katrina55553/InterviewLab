"""Short claim -> publish outside transaction -> fenced acknowledgement."""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import or_, select

from app.core.config import settings
from app.core.database import async_session_maker, engine
from app.platform.ai.guard import database_now
from app.platform.contracts import traced
from app.platform.models import Outbox, new_id
from app.platform.operations.faults import checkpoint
from app.platform.service import run_job
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
_worker_loop = None


@celery_app.task(name="app.platform.worker.execute_job", acks_late=True, reject_on_worker_lost=True)
def execute_job(job_id: str, delivery_id: str | None = None, trace_id: str | None = None):
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
    with traced(new=True, trace_id=trace_id, job_id=job_id):
        logger.info("job_received", extra={"delivery_id": delivery_id})
        result = _worker_loop.run_until_complete(run_job(job_id))
        logger.info("job_finished", extra={"delivery_id": delivery_id})
        return result


async def dispatch_once() -> int:
    claimed = []
    async with async_session_maker() as db, db.begin():
        now = await database_now(db)
        jobs = (await db.scalars(select(Outbox).where(
            Outbox.execution_status == "queued",
            or_(Outbox.delivery_lease_until.is_(None), Outbox.delivery_lease_until <= now),
            or_(Outbox.next_delivery_at.is_(None), Outbox.next_delivery_at <= now),
            or_(Outbox.status == "pending", Outbox.published_at.is_(None), Outbox.published_at <= now - timedelta(seconds=settings.OUTBOX_REDELIVER_SECONDS)),
        ).order_by(Outbox.created_at).with_for_update(skip_locked=True).limit(25))).all()
        for job in jobs:
            owner, delivery = new_id(), new_id()
            job.delivery_owner, job.delivery_id = owner, delivery
            job.delivery_lease_until = now + timedelta(seconds=30)
            job.delivery_attempts += 1
            claimed.append((job.id, job.kind, owner, delivery, job.trace_id, job.delivery_attempts))
    count = 0
    for job_id, kind, owner, delivery, trace, attempts in claimed:
        async with async_session_maker() as db, db.begin():
            now = await database_now(db)
            job = await db.scalar(select(Outbox).where(Outbox.id == job_id).with_for_update())
            if not job or job.delivery_owner != owner or job.delivery_lease_until <= now or job.execution_status != "queued":
                continue
            job.delivery_lease_until = now + timedelta(seconds=30)
        published = False
        try:
            with traced(new=True, trace_id=trace, job_id=job_id):
                await asyncio.wait_for(asyncio.to_thread(celery_app.send_task, "app.platform.worker.execute_job",
                    args=[job_id, delivery, trace], task_id=delivery,
                    queue="prepare" if kind == "diagnose" else kind.removeprefix("probe:") if kind.removeprefix("probe:") in {"prepare", "report"} else "default",
                    retry=False), timeout=10)
                published = True
                logger.info("job_published", extra={"delivery_id": delivery})
                await checkpoint("after_publish_before_ack")
        except Exception:
            logger.warning("outbox_delivery_unknown")
        async with async_session_maker() as db, db.begin():
            now = await database_now(db)
            job = await db.scalar(select(Outbox).where(Outbox.id == job_id).with_for_update())
            if not job or job.delivery_owner != owner or job.delivery_lease_until <= now:
                continue
            job.delivery_owner, job.delivery_lease_until = None, None
            if published:
                job.status, job.published_at = "published", now
                job.next_delivery_at = now + timedelta(seconds=settings.OUTBOX_REDELIVER_SECONDS)
                count += 1
            else:
                job.next_delivery_at = now + timedelta(seconds=min(60, 2 ** min(attempts, 6)))
    return count


async def main():
    from app.platform.operations.health import pulse
    from app.platform.operations.logging import configure_logging

    settings.validate_runtime()
    configure_logging()
    try:
        while True:
            try:
                await dispatch_once()
                await pulse("dispatcher", ready=True, progress=True)
            except Exception:
                logger.exception("dispatch_failed")
            await asyncio.sleep(2)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
