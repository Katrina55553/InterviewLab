"""Daily GitHub metadata refresh, isolated from interview command processing."""

import asyncio
import logging

import httpx
from redis.exceptions import RedisError

from app.platform.repository_stats import RefreshLeaseLost, synchronize
from app.tasks.celery_app import REPOSITORY_STATS_TASK, celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name=REPOSITORY_STATS_TASK, max_retries=3,
                 ignore_result=True, soft_time_limit=30, time_limit=45)
def refresh_repository_stats(self, only_if_missing: bool = False):
    try:
        return asyncio.run(synchronize(only_if_missing=only_if_missing))
    except (httpx.HTTPError, RedisError, TimeoutError, RefreshLeaseLost, ValueError) as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        retryable = isinstance(exc, (httpx.RequestError, RedisError, TimeoutError, RefreshLeaseLost)) or (
            status is not None and (status in {403, 429} or status >= 500)
        )
        if retryable and self.request.retries < self.max_retries:
            logger.warning("repository_stats_retrying", extra={"status": status})
            raise self.retry(exc=exc, countdown=60 * 2 ** self.request.retries) from exc
        logger.warning("repository_stats_failed", extra={"status": status})
        raise
