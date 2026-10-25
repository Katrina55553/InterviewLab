"""Personal model isolation and Model Connection Kit host contracts; no cloud calls."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.core.config import settings
from app.platform import auth, model_catalog, runtime, user_settings
from app.platform.admin import decrypt_key
from app.platform.ai import guard
from app.platform.contracts import PlatformError
from app.platform.model_transport import PublicModelTransport, public_address, validate_personal_url
from app.platform.models import AiCall, ChatMessage, UserModelCredential
from tests.platform_db import isolated_database, postgres_test_url

SELECTION = {"connectionType": "builtin", "authMethod": "api-key", "providerId": "deepseek", "modelId": "deepseek-chat"}


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def personal_client(request, tmp_path, monkeypatch):
    from app.main import app

    if request.param == "postgresql" and not postgres_test_url():
        pytest.skip("Set PLATFORM_TEST_DATABASE_URL for isolated PostgreSQL model connection tests")
    async with isolated_database(request.param, tmp_path) as database:
        for module in (auth, runtime, user_settings):
            monkeypatch.setattr(module, "async_session_maker", database.sessions)
        monkeypatch.setattr(auth, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
        monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://localhost:5173"])
        monkeypatch.setattr(settings, "PLATFORM_DEMO_MODE", False)
        monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                     headers={"Origin": "http://localhost:5173"}) as client:
            yield client, database


async def register(client, username="model_owner"):
    response = await client.post("/api/auth/register", json={"username": username, "password": "test-password-123"})
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = client.cookies[auth.CSRF_COOKIE]
    return response.json()["id"]


async def connect(client, key="personal-key"):
    response = await client.post("/api/settings/ai/connect", json={**{k: v for k, v in SELECTION.items() if k != "modelId"}, "apiKey": key})
    assert response.status_code == 200, response.text
    return response


async def save(client, version=0, selection=None):
    return await client.put("/api/settings/ai", json={"selection": selection or SELECTION, "expected_version": version})


@pytest.mark.asyncio
async def test_authentication_csrf_and_secret_only_crosses_connect_boundary(personal_client):
    client, database = personal_client
    assert (await client.get("/api/settings/ai")).status_code == 401
    await register(client)
    token = client.headers.pop("X-CSRF-Token")
    assert (await client.post("/api/settings/ai/connect", json={})).status_code == 403
    client.headers["X-CSRF-Token"] = token
    assert (await save(client)).status_code == 409
    response = await connect(client)
    assert "personal-key" not in response.text and "encrypted_key" not in response.text
    assert (await client.get("/api/settings/ai")).json()["selection"] is None
    async with database.sessions() as db:
        credential = await db.scalar(select(UserModelCredential))
        assert credential.encrypted_key != "personal-key"
        assert decrypt_key(credential.encrypted_key) == "personal-key"
    saved = await save(client)
    assert saved.status_code == 200 and saved.json()["selection"] == SELECTION
    assert "personal-key" not in saved.text
    assert (await save(client)).status_code == 409
    assert (await save(client, 1, {**SELECTION, "apiKey": "must-not-persist"})).status_code == 422


@pytest.mark.asyncio
async def test_named_providers_resolve_and_freeze_their_own_endpoints(personal_client):
    client, database = personal_client
    owner = await register(client)
    expected = {
        "deepseek": "https://api.deepseek.com", "openai-api": "https://api.openai.com/v1",
        "openrouter": "https://openrouter.ai/api/v1", "siliconflow": "https://api.siliconflow.cn/v1",
        "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1", "moonshot": "https://api.moonshot.cn/v1",
        "bigmodel": "https://open.bigmodel.cn/api/paas/v4",
    }
    providers = (await client.get("/api/settings/ai/providers")).json()
    assert {provider["id"]: provider["baseUrl"] for provider in providers if "apiKey" in provider["auth"]} == expected
    assert next(provider for provider in providers if provider["id"] == "deepseek")["defaultModel"] == "deepseek-flash"
    assert (await client.get("/api/settings/ai/models?provider_id=deepseek")).json() == []
    assert next(provider for provider in providers if provider["id"] == "openai")["auth"].keys() == {"oauth"}
    for version, (provider_id, base_url) in enumerate(expected.items()):
        connection = {"connectionType": "builtin", "authMethod": "api-key", "providerId": provider_id}
        result = await client.post("/api/settings/ai/connect", json={**connection, "apiKey": f"private-{provider_id}"})
        assert result.status_code == 200, result.text
        assert f"private-{provider_id}" not in result.text
        selection = {**connection, "modelId": "account-model"}
        result = await save(client, version, selection)
        assert result.status_code == 200 and result.json()["selection"] == selection, result.text
        frozen = await runtime.freeze_configs(user_id=owner)
        assert all(config["base_url"] == base_url for config in frozen.values())
        assert decrypt_key(frozen["chat"]["encrypted_key"]) == f"private-{provider_id}"
    async with database.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(UserModelCredential)) == len(expected)


@pytest.mark.asyncio
async def test_provider_directory_enforces_allowlist_and_authentication_type(personal_client, monkeypatch):
    client, _ = personal_client
    await register(client)
    monkeypatch.setattr(settings, "USER_LLM_ALLOWED_BASE_URLS", "https://openrouter.ai/api/v1")
    providers = (await client.get("/api/settings/ai/providers")).json()
    assert [provider["id"] for provider in providers] == ["openai", "openrouter"]
    connection = {"connectionType": "builtin", "authMethod": "api-key", "providerId": "openrouter", "apiKey": "private-key"}
    assert (await client.post("/api/settings/ai/connect", json=connection)).status_code == 200
    for provider_id in ("deepseek", "openai-api", "unknown", "openai"):
        assert (await client.post("/api/settings/ai/connect", json={**connection, "providerId": provider_id})).status_code == 422
    assert (await client.post("/api/settings/ai/connect", json={**connection, "baseUrl": "https://untrusted.example/v1"})).status_code == 422
    assert (await save(client, selection={**SELECTION, "providerId": "openai-api", "authMethod": "oauth"})).status_code == 422
    monkeypatch.setattr(settings, "USER_LLM_ALLOWED_BASE_URLS", "https://api.deepseek.com/v1")
    providers = (await client.get("/api/settings/ai/providers")).json()
    assert next(provider for provider in providers if provider["id"] == "deepseek")["baseUrl"] == "https://api.deepseek.com/v1"


@pytest.mark.asyncio
async def test_catalog_loads_with_each_accounts_saved_key_and_never_runs_inference(personal_client, monkeypatch):
    client, database = personal_client
    await register(client)
    requests = []

    async def upstream(request):
        requests.append(request)
        assert request.method == "GET"
        provider_id = request.headers["authorization"].removeprefix("Bearer catalog-key-")
        if provider_id == "openrouter":
            assert request.url.path == "/api/v1/models/user"
        if provider_id == "siliconflow":
            assert request.url.params["sub_type"] == "chat"
        return httpx.Response(200, json={"data": [{"id": provider_id + "-account-model", "name": "账户模型"}]})

    monkeypatch.setattr(model_catalog, "model_http_client", lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    providers = (await client.get("/api/settings/ai/providers")).json()
    for provider in (provider for provider in providers if "apiKey" in provider["auth"]):
        provider_id = provider["id"]
        assert (await client.get("/api/settings/ai/models", params={"provider_id": provider_id})).json() == []
        body = {"connectionType": "builtin", "authMethod": "api-key", "providerId": provider_id, "apiKey": "catalog-key-" + provider_id}
        assert (await client.post("/api/settings/ai/connect", json=body)).status_code == 200
        result = await client.get("/api/settings/ai/models", params={"provider_id": provider_id})
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
        assert result.json()[0]["id"] == provider_id + "-account-model" and result.json()[0]["name"] == "账户模型"
        assert str(requests[-1].url).startswith(provider["baseUrl"] + "/models")
        assert "catalog-key-" not in result.text and "encrypted_key" not in result.text
    assert len(requests) == 7
    assert (await client.get("/api/settings/ai")).json()["selection"] is None
    async with database.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AiCall)) == 0
        assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 0
    await client.post("/api/auth/logout")
    await register(client, "catalog_other_owner")
    assert (await client.get("/api/settings/ai/models?provider_id=openai-api")).json() == []
    assert len(requests) == 7


@pytest.mark.asyncio
async def test_late_catalog_is_rejected_after_disconnect(personal_client, monkeypatch):
    client, _ = personal_client
    await register(client)
    await connect(client)

    async def upstream(_request):
        assert (await client.delete("/api/settings/ai/auth?provider_id=deepseek")).status_code == 200
        return httpx.Response(200, json={"data": [{"id": "late-private-model"}]})

    monkeypatch.setattr(model_catalog, "model_http_client", lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    response = await client.get("/api/settings/ai/models?provider_id=deepseek")
    assert response.status_code == 409 and "late-private-model" not in response.text
    assert (await client.get("/api/settings/ai/models?provider_id=deepseek")).json() == []


@pytest.mark.asyncio
async def test_credentials_and_selection_are_account_scoped(personal_client):
    client, _ = personal_client
    owner = await register(client)
    await connect(client)
    assert (await save(client)).status_code == 200
    frozen = await runtime.freeze_configs(user_id=owner)
    with runtime.call_scope(owner):
        assert decrypt_key(frozen["chat"]["encrypted_key"]) == "personal-key"
    await client.post("/api/auth/logout")
    other = await register(client, "other_model_owner")
    assert (await client.get("/api/settings/ai")).json()["selection"] is None
    assert (await client.get("/api/settings/ai/auth?provider_id=deepseek")).json()["state"] == "unconfigured"
    assert (await save(client)).status_code == 409
    with pytest.raises(PlatformError):
        await runtime.freeze_configs(user_id=other)
    await connect(client, "other-private-key")
    await save(client)
    theirs = await runtime.freeze_configs(user_id=other)
    assert decrypt_key(theirs["chat"]["encrypted_key"]) == "other-private-key"
    with pytest.raises(PlatformError):
        await guard.reserve(user_settings.async_session_maker, frozen["chat"], {"user_id": other})


@pytest.mark.asyncio
async def test_freeze_rotation_disconnect_and_no_platform_fallback(personal_client, monkeypatch):
    client, database = personal_client
    owner = await register(client)
    monkeypatch.setattr(settings, "LLM_API_KEY", "platform-must-not-be-used")
    with pytest.raises(PlatformError):
        await runtime.freeze_configs(user_id=owner)
    await connect(client)
    await save(client)
    frozen = await runtime.freeze_configs(user_id=owner)
    assert all(config["credential_owner_id"] == owner for config in frozen.values())
    await connect(client, "rotated-personal-key")
    fresh = await runtime.freeze_configs(user_id=owner)
    assert decrypt_key(frozen["resume"]["encrypted_key"]) == "personal-key"
    assert decrypt_key(fresh["resume"]["encrypted_key"]) == "rotated-personal-key"
    call = await guard.reserve(database.sessions, fresh["chat"], {"user_id": owner})
    await guard.settle(database.sessions, call, time.monotonic(), "not_sent")
    assert (await client.delete("/api/settings/ai/auth?provider_id=deepseek")).status_code == 200
    assert (await client.get("/api/settings/ai")).json()["selection"] is None
    with pytest.raises(PlatformError):
        await guard.reserve(database.sessions, frozen["chat"], {"user_id": owner})
    with pytest.raises(PlatformError):
        await guard.reserve(database.sessions, {"encrypted_key": frozen["chat"]["encrypted_key"]}, {"user_id": owner})


@pytest.mark.asyncio
async def test_probe_is_real_minimal_and_does_not_save_a_selection_or_message(personal_client, monkeypatch):
    client, database = personal_client
    owner = await register(client)
    await connect(client)
    send = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="O"))], usage=None))
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=send)))

    class Client:
        async def __aenter__(self):
            return sdk

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(runtime, "_client", lambda config: Client())
    result = await client.post("/api/settings/ai/probe", json=SELECTION)
    assert result.status_code == 200 and result.json()["state"] == "reachable", result.text
    assert send.await_args.kwargs["max_tokens"] == 1
    assert send.await_args.kwargs["stream"] is False
    assert (await client.get("/api/settings/ai")).json()["selection"] is None
    async with database.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 0
        call = await db.scalar(select(AiCall))
        assert call.user_id == owner and call.status == "completed"
    send.return_value = SimpleNamespace(choices=[], usage=None)
    result = await client.post("/api/settings/ai/probe", json=SELECTION)
    assert result.json()["state"] == "unreachable"


@pytest.mark.asyncio
async def test_custom_metadata_and_anonymous_connection_round_trip(personal_client):
    client, _ = personal_client
    owner = await register(client)
    custom = {"connectionType": "custom", "authMethod": "none", "providerId": "custom:https://api.deepseek.com/v1",
              "modelId": "my-model", "custom": {"baseUrl": "https://api.deepseek.com/v1", "api": "openai-completions",
              "displayName": "My connection", "model": {"input": ["text", "image"], "reasoning": False,
              "contextWindow": 8192, "maxTokens": 1024, "cost": {"input": 1.5, "output": 2}}}}
    assert (await client.post("/api/settings/ai/connect", json=custom)).status_code == 200
    saved = await save(client, selection=custom)
    assert saved.status_code == 200 and saved.json()["selection"] == custom
    frozen = await runtime.freeze_configs(user_id=owner)
    assert frozen["vision"]["enabled"] is True
    assert frozen["chat"]["max_output_tokens"] == 1024
    assert frozen["chat"]["auth_method"] == "none" and frozen["chat"]["encrypted_key"] is None
    assert (await client.post("/api/settings/ai/connect", json={**custom, "providerId": "custom:someone-else"})).status_code == 422
    custom["custom"]["model"]["maxTokens"] = 9000
    assert (await save(client, version=1, selection=custom)).status_code == 422


@pytest.mark.asyncio
async def test_postgres_parallel_selection_save_has_one_winner(personal_client):
    client, database = personal_client
    if database.dialect != "postgresql":
        pytest.skip("This assertion requires real PostgreSQL locking")
    await register(client)
    await connect(client)
    results = await asyncio.gather(save(client), save(client))
    assert sorted(result.status_code for result in results) == [200, 409]


@pytest.mark.parametrize("url", ["http://localhost:11434/v1", "https://127.0.0.1/v1", "https://api.deepseek.com/other", "https://api.deepseek.com?key=secret", "https://key@api.deepseek.com", "https://api.deepseek.com:8443"])
def test_custom_proxy_rejects_unapproved_urls(url):
    with pytest.raises(ValueError):
        validate_personal_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1", "::1", "::ffff:8.8.8.8", "2001:db8::1"])
def test_private_or_special_dns_answers_are_rejected(address):
    assert not public_address(address)


@pytest.mark.asyncio
async def test_egress_pins_dns_preserves_tls_and_strips_anonymous_authorization(monkeypatch):
    transport = PublicModelTransport("https://api.deepseek.com", anonymous=True)
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]))
    sent = AsyncMock(return_value=httpx.Response(302))
    monkeypatch.setattr(transport.transport, "handle_async_request", sent)
    try:
        await transport.handle_async_request(httpx.Request("POST", "https://api.deepseek.com/chat/completions", headers={"Authorization": "Bearer no-auth"}))
        request = sent.await_args.args[0]
        assert request.url.host == "8.8.8.8" and request.headers["host"] == "api.deepseek.com"
        assert request.extensions["sni_hostname"] == "api.deepseek.com"
        assert "authorization" not in request.headers
        sent.reset_mock()
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]))
        with pytest.raises(ValueError):
            await transport.handle_async_request(httpx.Request("POST", "https://api.deepseek.com/chat/completions"))
        sent.assert_not_awaited()
    finally:
        await transport.aclose()
