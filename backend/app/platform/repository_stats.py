"""Cached GitHub repository metadata; HTTP reads never contact GitHub."""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, Depends
from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from app.core.config import settings
from app.core.redis import get_redis
from app.platform.auth import current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["repository"], dependencies=[Depends(current_user)])

GITHUB_URL = "https://api.github.com/repos/Katrina55553/InterviewLab"
CACHE_KEY = "platform:repository:Katrina55553/InterviewLab:stats"
LOCK_KEY = CACHE_KEY + ":lock"
LOCK_SECONDS = 60
RELEASE = "if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"
COMMIT = """
if redis.call('get',KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('set',KEYS[2],ARGV[2])
return 1
"""


class RepositoryStats(BaseModel):
    stars: int = Field(strict=True, ge=0)
    forks: int = Field(strict=True, ge=0)
    updated_at: AwareDatetime | None = None
    source: Literal["github", "fallback"] = "fallback"


class GitHubCounts(BaseModel):
    stargazers_count: int = Field(strict=True, ge=0)
    forks_count: int = Field(strict=True, ge=0)


class RefreshLeaseLost(RuntimeError):
    """A delayed updater must not overwrite a subsequent owner's snapshot."""


async def read_cached_stats(client: redis.Redis) -> RepositoryStats | None:
    value = await client.get(CACHE_KEY)
    if value is None:
        return None
    try:
        stats = RepositoryStats.model_validate_json(value)
    except ValidationError:
        return None
    return stats if stats.source == "github" and stats.updated_at is not None else None


@router.get("/repository/stats", response_model=RepositoryStats)
async def repository_stats():
    try:
        stats = await read_cached_stats(get_redis())
    except (redis.RedisError, RuntimeError):
        logger.warning("repository_stats_cache_unavailable")
        stats = None
    return stats or RepositoryStats(stars=4, forks=0)


async def fetch_counts() -> GitHubCounts:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "InterviewLab"}
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client, asyncio.timeout(10):
        response = await client.get(GITHUB_URL, headers=headers)
        response.raise_for_status()
        return GitHubCounts.model_validate(response.json())


async def synchronize(*, only_if_missing: bool = False) -> str:
    # Each task owns its clients and event loop; no web-process pool is reused.
    client = redis.from_url(settings.REDIS_URL, decode_responses=True,
                            socket_connect_timeout=1, socket_timeout=1)
    owner = uuid4().hex
    acquired = False
    try:
        acquired = bool(await client.set(LOCK_KEY, owner, nx=True, ex=LOCK_SECONDS))
        if not acquired:
            return "locked"
        if only_if_missing and await read_cached_stats(client) is not None:
            return "cached"
        counts = await fetch_counts()
        stats = RepositoryStats(stars=counts.stargazers_count, forks=counts.forks_count,
                                updated_at=datetime.now(UTC), source="github")
        # Fence the write against an expired/replaced lock. Keep good data without a TTL.
        if not await client.eval(COMMIT, 2, LOCK_KEY, CACHE_KEY, owner, stats.model_dump_json()):
            raise RefreshLeaseLost("Repository refresh lease expired")
        logger.info("repository_stats_updated")
        return "updated"
    finally:
        if acquired:
            with contextlib.suppress(redis.RedisError):
                await client.eval(RELEASE, 1, LOCK_KEY, owner)
        await client.aclose()
