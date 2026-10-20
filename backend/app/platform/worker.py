"""Transactional outbox dispatcher and idempotent Celery platform task."""

import asyncio
import logging

from sqlalchemy import select

from app.core.database import async_session_maker, engine
from app.platform.models import Outbox
from app.platform.service import run_job
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
_worker_loop = None


@celery_app.task(name="app.platform.worker.execute_job", acks_late=True, reject_on_worker_lost=True)
def execute_job(job_id: str):
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
    return _worker_loop.run_until_complete(run_job(job_id))


async def dispatch_once() -> int:
    count = 0
    async with async_session_maker() as db, db.begin():
        jobs = (await db.scalars(select(Outbox).where(Outbox.status == "pending")
                                 .order_by(Outbox.created_at).with_for_update(skip_locked=True).limit(25))).all()
        for job in jobs:
            # Publish before marking. A crash may duplicate delivery; run_job claims durably.
            await asyncio.to_thread(celery_app.send_task, "app.platform.worker.execute_job", args=[job.id],
                                    task_id=job.id, queue="default", retry=False)
            job.status = "published"
            count += 1
    return count


async def main():
    try:
        while True:
            try:
                await dispatch_once()
            except Exception:
                logger.warning("outbox dispatch failed; committed jobs remain pending", exc_info=True)
            await asyncio.sleep(2)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
