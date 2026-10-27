"""Independent checks for the isolated CI adapter and normal persisted AI guards."""

import time

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.platform import auth, runtime
from app.platform.admin import decrypt_key
from app.platform.ai import guard
from app.platform.contracts import PlatformError
from app.platform.models import AiCall, AiPermit, User, UserModelCredential
from tests import engineering_runtime as fixture
from tests.auth_helpers import provision_user
from tests.platform_db import isolated_database


@pytest.fixture
def isolated_settings(monkeypatch):
    values = {
        "DATABASE_URL": fixture.TEST_DATABASE_URL, "REDIS_URL": "redis://redis:6379/0",
        "CELERY_BROKER_URL": "redis://redis:6379/1", "CELERY_RESULT_BACKEND": "redis://redis:6379/2",
        "SECRET_KEY": fixture.TEST_SECRET_KEY, "ENVIRONMENT": "development",
        "USER_LLM_ALLOWED_BASE_URLS": fixture.FIXTURE_BASE_URL, "PLATFORM_DEMO_MODE": False,
    }
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)


@pytest.mark.parametrize("name,value", [
    ("DATABASE_URL", "postgresql+asyncpg://postgres:test-only@postgres:5432/real_business"),
    ("DATABASE_URL", "postgresql+asyncpg://postgres:test-only@prod:5432/resume_agent_test"),
    ("REDIS_URL", "redis://prod:6379/0"),
    ("CELERY_BROKER_URL", "redis://redis:6379/0"),
    ("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
    ("SECRET_KEY", "a-real-production-secret-key"),
    ("ENVIRONMENT", "production"),
    ("USER_LLM_ALLOWED_BASE_URLS", fixture.FIXTURE_BASE_URL + ",https://api.openai.com/v1"),
])
def test_fixture_adapter_refuses_nonisolated_configuration(isolated_settings, monkeypatch, name, value):
    fixture.assert_isolated_environment()
    monkeypatch.setattr(settings, name, value)
    with pytest.raises(RuntimeError, match="isolated test Compose"):
        fixture.assert_isolated_environment()


@pytest.mark.asyncio
async def test_fixture_forwards_only_test_credential_and_never_follows_redirects(isolated_settings):
    calls = []

    async def upstream(request):
        calls.append(request)
        return httpx.Response(307, headers={"location": "https://external.example/v1/chat/completions"})

    async with fixture.fixture_http_client(fixture.FIXTURE_BASE_URL) as client:
        await client._transport.transport.aclose()
        client._transport.transport = httpx.MockTransport(upstream)
        response = await client.post(fixture.FIXTURE_BASE_URL + "/chat/completions",
                                     headers={"Authorization": "Bearer " + fixture.FIXTURE_API_KEY}, json={})
        assert response.status_code == 307 and len(calls) == 1
        assert str(calls[0].url) == "http://provider:8000/v1/chat/completions"
        assert calls[0].headers["host"] == "provider:8000"
        for url, credential in [
            ("https://external.example/v1/chat/completions", fixture.FIXTURE_API_KEY),
            ("http://fixture.invalid/v1/chat/completions", fixture.FIXTURE_API_KEY),
            (fixture.FIXTURE_BASE_URL + "/chat/completions", "a-real-provider-secret"),
        ]:
            with pytest.raises(ValueError, match="only the"):
                await client.post(url, headers={"Authorization": "Bearer " + credential}, json={})
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_fixture_retains_response_size_bound(isolated_settings):
    async def upstream(_request):
        return httpx.Response(200, stream=httpx.ByteStream(b"response larger than limit"))

    async with fixture.fixture_http_client(fixture.FIXTURE_BASE_URL, response_limit=4) as client:
        await client._transport.transport.aclose()
        client._transport.transport = httpx.MockTransport(upstream)
        with pytest.raises(httpx.ReadError, match="大小限制"):
            await client.post(fixture.FIXTURE_BASE_URL + "/chat/completions",
                              headers={"Authorization": "Bearer " + fixture.FIXTURE_API_KEY}, json={})


@pytest.mark.asyncio
async def test_fixture_users_freeze_private_config_and_use_normal_quota_audit(isolated_settings, monkeypatch, tmp_path):
    async with isolated_database("sqlite", tmp_path) as database:
        monkeypatch.setattr(auth, "async_session_maker", database.sessions)
        monkeypatch.setattr(runtime, "async_session_maker", database.sessions)
        await provision_user("fixture_owner", "test-only-password-2026", fixture_model=True)
        await provision_user("fixture_other", "test-only-password-2026", fixture_model=True)
        async with database.sessions() as db:
            users = list((await db.scalars(select(User).order_by(User.username))).all())
            credentials = list((await db.scalars(select(UserModelCredential))).all())
        configs = [(await runtime.freeze_configs(user_id=user.id))["chat"] for user in users]
        assert len(credentials) == 2 and credentials[0].id != credentials[1].id
        assert all(row.encrypted_key != fixture.FIXTURE_API_KEY for row in credentials)
        assert all(decrypt_key(row.encrypted_key) == fixture.FIXTURE_API_KEY for row in credentials)
        assert all(config["credential_owner_id"] == user.id for config, user in zip(configs, users, strict=True))
        assert all(config["base_url"] == fixture.FIXTURE_BASE_URL and config["user_provider_id"]
                   and config["provider_id"] is None for config in configs)
        assert guard.credential_scope(configs[0]) == guard.credential_scope(configs[1])
        call_id = await guard.reserve(database.sessions, configs[0], {"user_id": users[0].id})
        await guard.mark_sent(database.sessions, call_id)
        await guard.settle(database.sessions, call_id, time.monotonic(), "success",
                           {"prompt_tokens": 3, "completion_tokens": 2})
        async with database.sessions() as db:
            call = await db.get(AiCall, call_id)
            assert call.user_id == users[0].id and call.outcome == "known" and call.reserved_tokens == 5
            assert await db.get(AiPermit, call_id) is None
        with pytest.raises(PlatformError) as refused:
            await guard.reserve(database.sessions, configs[0], {"user_id": users[1].id})
        assert refused.value.code == "AI_CONFIGURATION_ERROR"
