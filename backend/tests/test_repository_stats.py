"""Repository metadata uses validated snapshots and never fetches in HTTP reads."""

import asyncio
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from celery.exceptions import Retry
from fastapi import FastAPI
from redis.exceptions import ConnectionError as RedisConnectionError

from app.platform import repository_stats as stats
from app.platform.auth import current_user
from app.tasks import repository_stats as tasks
from app.tasks.celery_app import REPOSITORY_STATS_TASK, celery_app, initialize_repository_stats

SAVED = {"stars": 12, "forks": 2, "updated_at": "2026-10-20T19:00:00Z", "source": "github"}
FALLBACK = {"stars": 4, "forks": 0, "updated_at": None, "source": "fallback"}


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.writes = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.writes.append((key, ex))
        return True

    async def eval(self, script, numkeys, *args):
        keys, arguments = args[:numkeys], args[numkeys:]
        if self.values.get(keys[0]) != arguments[0]:
            return 0
        if script == stats.COMMIT:
            await self.set(keys[1], arguments[1])
        else:
            del self.values[keys[0]]
        return 1

    async def aclose(self):
        pass


@pytest.fixture
def cache(monkeypatch):
    client = MemoryRedis()
    monkeypatch.setattr(stats.redis, "from_url", lambda *args, **kwargs: client)
    monkeypatch.setattr(stats, "get_redis", lambda: client)
    return client


@pytest.fixture
def github(monkeypatch):
    requests = []
    response = {"stargazers_count": 17, "forks_count": 3}
    original_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response)

    monkeypatch.setattr(stats.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(stats.settings, "GITHUB_TOKEN", "")
    return response, requests


@pytest.mark.asyncio
async def test_sync_then_authenticated_http_read_uses_snapshot(cache, github):
    before = datetime.now(UTC)
    assert await stats.synchronize() == "updated"
    saved = await stats.read_cached_stats(cache)
    assert (saved.stars, saved.forks, saved.source) == (17, 3, "github")
    assert saved.updated_at >= before
    assert cache.writes == [(stats.LOCK_KEY, 60), (stats.CACHE_KEY, None)]
    assert stats.LOCK_KEY not in cache.values
    requests = github[1]
    assert len(requests) == 1 and str(requests[0].url) == stats.GITHUB_URL
    assert "authorization" not in requests[0].headers
    assert requests[0].headers["user-agent"] == "ResumeAgent"
    assert requests[0].extensions["timeout"]["read"] == 10
    app = FastAPI()
    app.include_router(stats.router, prefix="/api")
    async with _api_client(app) as client:
        assert (await client.get("/api/repository/stats")).status_code == 401
        app.dependency_overrides[current_user] = lambda: SimpleNamespace(id="user-1")
        response = await client.get("/api/repository/stats")
    assert response.status_code == 200
    assert response.json() == saved.model_dump(mode="json")
    assert len(requests) == 1


# Capture before fixtures replace AsyncClient to keep local API requests independent.
_API_CLIENT = httpx.AsyncClient


def _api_client(app):
    return _API_CLIENT(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "not-json", "{}", json.dumps({**SAVED, "stars": -1}),
    json.dumps({**SAVED, "forks": True}), json.dumps({**SAVED, "updated_at": None}),
    json.dumps({"source": "github", "updated_at": SAVED["updated_at"]}), json.dumps(FALLBACK)])
async def test_missing_or_corrupt_cache_returns_defaults_without_fetching(cache, value, monkeypatch):
    if value is not None:
        cache.values[stats.CACHE_KEY] = value
    fetch = AsyncMock(side_effect=AssertionError("HTTP reads must never fetch"))
    monkeypatch.setattr(stats, "fetch_counts", fetch)
    assert (await stats.repository_stats()).model_dump() == FALLBACK
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_unavailable_redis_returns_defaults(cache, monkeypatch):
    monkeypatch.setattr(cache, "get", AsyncMock(side_effect=RedisConnectionError()))
    assert (await stats.repository_stats()).model_dump() == FALLBACK
    monkeypatch.setattr(stats, "get_redis", Mock(side_effect=RuntimeError("not initialized")))
    assert (await stats.repository_stats()).model_dump() == FALLBACK


@pytest.mark.asyncio
async def test_zero_counts_are_real_results_and_optional_token_is_server_only(cache, github, monkeypatch):
    github[0].update(stargazers_count=0, forks_count=0)
    monkeypatch.setattr(stats.settings, "GITHUB_TOKEN", "test-token")
    assert await stats.synchronize() == "updated"
    saved = await stats.read_cached_stats(cache)
    assert (saved.stars, saved.forks) == (0, 0)
    assert github[1][0].headers["authorization"] == "Bearer test-token"
    assert "test-token" not in saved.model_dump_json()


@pytest.mark.asyncio
async def test_startup_skips_good_cache_but_repairs_corrupt_cache(cache, github):
    cache.values[stats.CACHE_KEY] = json.dumps(SAVED)
    assert await stats.synchronize(only_if_missing=True) == "cached"
    assert not github[1]
    cache.values[stats.CACHE_KEY] = "invalid-json"
    assert await stats.synchronize(only_if_missing=True) == "updated"
    assert len(github[1]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("stargazers_count", -1), ("forks_count", True),
                                        ("forks_count", "2"), ("forks_count", None)])
async def test_invalid_github_counts_preserve_last_good_data(cache, github, field, value):
    cache.values[stats.CACHE_KEY] = json.dumps(SAVED)
    github[0][field] = value
    with pytest.raises(ValueError):
        await stats.synchronize()
    assert json.loads(cache.values[stats.CACHE_KEY]) == SAVED
    assert stats.LOCK_KEY not in cache.values


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ReadTimeout("timeout"), httpx.HTTPStatusError(
    "rate limited", request=httpx.Request("GET", stats.GITHUB_URL), response=httpx.Response(429))])
async def test_github_failure_preserves_snapshot_and_releases_lock(cache, monkeypatch, error):
    cache.values[stats.CACHE_KEY] = json.dumps(SAVED)
    monkeypatch.setattr(stats, "fetch_counts", AsyncMock(side_effect=error))
    with pytest.raises(type(error)):
        await stats.synchronize()
    assert json.loads(cache.values[stats.CACHE_KEY]) == SAVED
    assert stats.LOCK_KEY not in cache.values


@pytest.mark.asyncio
async def test_concurrent_refreshes_make_one_request(cache, monkeypatch):
    entered, resume = asyncio.Event(), asyncio.Event()

    async def fetch():
        entered.set()
        await resume.wait()
        return stats.GitHubCounts(stargazers_count=6, forks_count=1)

    request = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(stats, "fetch_counts", request)
    first = asyncio.create_task(stats.synchronize())
    await entered.wait()
    assert await stats.synchronize() == "locked"
    resume.set()
    assert await first == "updated"
    assert request.await_count == 1


@pytest.mark.asyncio
async def test_expired_owner_cannot_write_or_release_new_owners_lock(cache, monkeypatch):
    cache.values[stats.CACHE_KEY] = json.dumps(SAVED)

    async def fetch():
        cache.values[stats.LOCK_KEY] = "replacement-owner"
        return stats.GitHubCounts(stargazers_count=9, forks_count=0)

    monkeypatch.setattr(stats, "fetch_counts", fetch)
    with pytest.raises(stats.RefreshLeaseLost):
        await stats.synchronize()
    assert cache.values[stats.LOCK_KEY] == "replacement-owner"
    assert json.loads(cache.values[stats.CACHE_KEY]) == SAVED


@pytest.mark.parametrize("retry,countdown", [(0, 60), (1, 120), (2, 240)])
@pytest.mark.parametrize("status", [None, 403, 429, 503])
def test_transient_failures_retry_with_backoff(monkeypatch, retry, countdown, status):
    error = httpx.ConnectError("offline") if status is None else httpx.HTTPStatusError(
        "failed", request=httpx.Request("GET", stats.GITHUB_URL), response=httpx.Response(status))
    monkeypatch.setattr(tasks, "synchronize", AsyncMock(side_effect=error))
    task = tasks.refresh_repository_stats
    retry_call = Mock(side_effect=Retry())
    monkeypatch.setattr(task, "retry", retry_call)
    task.push_request(retries=retry)
    try:
        with pytest.raises(Retry):
            task.run()
    finally:
        task.pop_request()
    retry_call.assert_called_once_with(exc=error, countdown=countdown)


@pytest.mark.parametrize("error,retries", [(ValueError("invalid payload"), 0),
    (httpx.HTTPStatusError("not found", request=httpx.Request("GET", stats.GITHUB_URL), response=httpx.Response(404)), 0),
    (httpx.ConnectError("offline"), 3)])
def test_permanent_errors_and_exhausted_retries_stop(monkeypatch, error, retries):
    monkeypatch.setattr(tasks, "synchronize", AsyncMock(side_effect=error))
    task = tasks.refresh_repository_stats
    retry_call = Mock()
    monkeypatch.setattr(task, "retry", retry_call)
    task.push_request(retries=retries)
    try:
        with pytest.raises(type(error)):
            task.run()
    finally:
        task.pop_request()
    retry_call.assert_not_called()


def test_beat_registers_daily_task_and_queues_cache_warmup():
    celery_app.loader.import_default_modules()
    assert REPOSITORY_STATS_TASK in celery_app.tasks
    assert celery_app.conf.timezone == "Asia/Shanghai"
    entry = celery_app.conf.beat_schedule["refresh-repository-stats-daily"]
    assert entry["task"] == REPOSITORY_STATS_TASK
    assert entry["schedule"].hour == {3} and entry["schedule"].minute == {0}
    assert entry["options"]["queue"] == "default"
    sender = SimpleNamespace(app=SimpleNamespace(send_task=Mock()))
    initialize_repository_stats(sender)
    sender.app.send_task.assert_called_once_with(REPOSITORY_STATS_TASK, kwargs={"only_if_missing": True},
                                                queue="default", retry=False)


@pytest.mark.asyncio
async def test_real_redis_commit_has_no_ttl_and_lock_is_released(monkeypatch):
    url = os.environ.get("PLATFORM_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set PLATFORM_TEST_REDIS_URL for the real Redis fencing test")
    monkeypatch.setattr(stats.settings, "REDIS_URL", url)
    key = "test:repository-stats:" + uuid4().hex
    monkeypatch.setattr(stats, "CACHE_KEY", key)
    monkeypatch.setattr(stats, "LOCK_KEY", key + ":lock")
    monkeypatch.setattr(stats, "fetch_counts", AsyncMock(return_value=stats.GitHubCounts(stargazers_count=7, forks_count=0)))
    client = stats.redis.from_url(url, decode_responses=True)
    try:
        assert await stats.synchronize() == "updated"
        assert await client.ttl(key) == -1
        assert not await client.exists(key + ":lock")
        assert (await stats.read_cached_stats(client)).stars == 7
    finally:
        await client.delete(key, key + ":lock")
        await client.aclose()
