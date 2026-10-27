"""The engineering fixture preserves personal ownership and cannot target business services."""

import json

import httpx
import pytest
from openai import AsyncOpenAI
from sqlalchemy import select

from app.core.config import settings
from app.platform import auth, runtime
from app.platform.admin import decrypt_key
from app.platform.ai.guard import credential_scope
from app.platform.models import User, UserAISettings, UserModelCredential
from tests import engineering_runtime as engineering
from tests.auth_helpers import provision_user
from tests.platform_db import isolated_database, postgres_test_url


@pytest.fixture
def isolated_runtime(monkeypatch):
    for name, value in {
        "DATABASE_URL": engineering.TEST_DATABASE_URL,
        "REDIS_URL": "redis://redis:6379/0",
        "CELERY_BROKER_URL": "redis://redis:6379/1",
        "CELERY_RESULT_BACKEND": "redis://redis:6379/2",
        "SECRET_KEY": engineering.TEST_SECRET_KEY,
        "ENVIRONMENT": "development",
        "USER_LLM_ALLOWED_BASE_URLS": engineering.FIXTURE_BASE_URL,
        "PLATFORM_DEMO_MODE": False,
    }.items():
        monkeypatch.setattr(settings, name, value)


@pytest.mark.parametrize(("name", "value"), [
    ("DATABASE_URL", "postgresql+asyncpg://postgres:test-only@postgres:5432/resume_agent"),
    ("REDIS_URL", "redis://redis:6379/3"),
    ("CELERY_BROKER_URL", "redis://redis:6379/0"),
    ("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
    ("SECRET_KEY", "a-business-secret-cannot-enable-this-adapter"),
    ("ENVIRONMENT", "production"),
    ("USER_LLM_ALLOWED_BASE_URLS", engineering.FIXTURE_BASE_URL + ",https://api.deepseek.com"),
])
def test_fixture_entry_rejects_any_business_environment_setting(isolated_runtime, monkeypatch, name, value):
    monkeypatch.setattr(settings, name, value)
    with pytest.raises(RuntimeError, match="isolated test Compose"):
        engineering.fixture_http_client(engineering.FIXTURE_BASE_URL)


@pytest.mark.asyncio
async def test_openai_sdk_uses_http_fixture_without_changing_selected_url(isolated_runtime, monkeypatch):
    received = []

    def upstream(request):
        received.append(request)
        return httpx.Response(200, json={
            "id": "fixture", "object": "chat.completion", "created": 0, "model": "test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "fixture response"},
                         "finish_reason": "stop"}],
        })

    monkeypatch.setattr(engineering.httpx, "AsyncHTTPTransport", lambda **_kwargs: httpx.MockTransport(upstream))
    async with AsyncOpenAI(api_key=engineering.FIXTURE_API_KEY, base_url=engineering.FIXTURE_BASE_URL,
                           http_client=engineering.fixture_http_client(engineering.FIXTURE_BASE_URL),
                           max_retries=0) as client:
        result = await client.chat.completions.create(model="test", messages=[{"role": "user", "content": "probe"}])
    assert result.choices[0].message.content == "fixture response"
    assert len(received) == 1
    assert str(received[0].url) == "http://provider:8000/v1/chat/completions"
    assert received[0].headers["host"] == "provider:8000"
    assert received[0].headers["authorization"] == "Bearer " + engineering.FIXTURE_API_KEY
    assert json.loads(received[0].content)["messages"] == [{"role": "user", "content": "probe"}]


@pytest.mark.parametrize("target", [
    "http://fixture.invalid/v1/chat/completions",
    "https://fixture.invalid:444/v1/chat/completions",
    "https://other.invalid/v1/chat/completions",
    "https://fixture.invalid/control",
    "https://user:password@fixture.invalid/v1/chat/completions",
])
@pytest.mark.asyncio
async def test_transport_rejects_requests_outside_exact_fixture(isolated_runtime, target):
    async with engineering.fixture_http_client(engineering.FIXTURE_BASE_URL) as client:
        with pytest.raises(ValueError, match="isolated fixture endpoint"):
            await client.post(target, headers={"Authorization": "Bearer " + engineering.FIXTURE_API_KEY})


@pytest.mark.asyncio
async def test_transport_rejects_real_credentials_before_any_network_request(isolated_runtime):
    async with engineering.fixture_http_client(engineering.FIXTURE_BASE_URL) as client:
        with pytest.raises(ValueError, match="test fixture credential"):
            await client.post(engineering.FIXTURE_BASE_URL + "/chat/completions",
                              headers={"Authorization": "Bearer never-forward-real-credentials"})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sqlite", "postgresql"])
async def test_fixture_provisioning_is_explicit_and_preserves_personal_scope(isolated_runtime, tmp_path, monkeypatch, kind):
    if kind == "postgresql" and not postgres_test_url():
        pytest.skip("Set PLATFORM_TEST_DATABASE_URL for real PostgreSQL engineering fixture tests")
    async with isolated_database(kind, tmp_path) as database:
        monkeypatch.setattr(auth, "async_session_maker", database.sessions)
        monkeypatch.setattr(runtime, "async_session_maker", database.sessions)
        await provision_user("unconfigured", "fixture-password-2026")
        await provision_user("fixture_one", "fixture-password-2026", fixture_model=True)
        await provision_user("fixture_two", "fixture-password-2026", fixture_model=True)
        async with database.sessions() as db:
            users = {user.username: user for user in await db.scalars(select(User))}
            assert await db.get(UserAISettings, users["unconfigured"].id) is None
            credentials = list(await db.scalars(select(UserModelCredential)))
            assert {credential.user_id for credential in credentials} == {
                users["fixture_one"].id, users["fixture_two"].id,
            }
            assert all(credential.encrypted_key != engineering.FIXTURE_API_KEY for credential in credentials)
            assert all(decrypt_key(credential.encrypted_key) == engineering.FIXTURE_API_KEY for credential in credentials)
        first = await runtime.freeze_configs(user_id=users["fixture_one"].id)
        second = await runtime.freeze_configs(user_id=users["fixture_two"].id)
        for username, frozen in (("fixture_one", first), ("fixture_two", second)):
            for scene, config in frozen.items():
                if scene != "vision":
                    assert config["credential_owner_id"] == users[username].id
                    assert config["base_url"] == engineering.FIXTURE_BASE_URL
                    assert config["user_provider_id"] in {credential.id for credential in credentials
                                                          if credential.user_id == users[username].id}
        assert credential_scope(first["chat"]) == credential_scope(second["chat"])


@pytest.mark.asyncio
async def test_fixture_provisioning_cannot_write_business_database(isolated_runtime, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/resume_agent")
    with pytest.raises(RuntimeError, match="isolated test Compose"):
        await provision_user("unsafe_target", "fixture-password-2026", fixture_model=True)
