"""Exercise real session/CSRF/configuration persistence without external services."""

from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, WebSocketException
from sqlalchemy import create_engine, inspect, select, text
from starlette.websockets import WebSocket

from app.core.database import Base
from app.platform import admin, auth, cli, media_models  # noqa: F401
from app.platform.models import ConfigRevision, LoginSession, Provider, User, utcnow
from tests.platform_db import isolated_database, postgres_test_url


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def platform_client(request, tmp_path, monkeypatch):
    if request.param == "postgresql" and not postgres_test_url():
        pytest.skip("Set PLATFORM_TEST_DATABASE_URL for real PostgreSQL auth tests")
    async with isolated_database(request.param, tmp_path) as database:
        sessions = database.sessions
        monkeypatch.setattr(auth, "async_session_maker", sessions)
        monkeypatch.setattr(admin, "async_session_maker", sessions)
        monkeypatch.setattr(
            auth, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError("Redis deliberately unavailable"))
        )
        monkeypatch.setattr(auth.settings, "AUTH_COOKIE_SECURE", False)
        monkeypatch.setattr(auth.settings, "ENVIRONMENT", "development")
        monkeypatch.setattr(auth.settings, "CORS_ORIGINS", ["http://localhost:5173"])
        monkeypatch.setattr(auth.settings, "AUTH_MAX_ATTEMPTS", 10)
        monkeypatch.setattr(auth.settings, "AUTH_IP_MAX_ATTEMPTS", 60)
        monkeypatch.setattr(auth.settings, "LLM_API_KEY", "")
        app = FastAPI()
        app.add_middleware(auth.AuthCSRFMiddleware)
        app.include_router(auth.router, prefix="/api")
        app.include_router(admin.router, prefix="/api")

        @app.post("/api/mutate")
        async def mutate(user=Depends(auth.current_user)):
            return {"user_id": user.id}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers={"Origin": "http://localhost:5173"}
        ) as client:
            yield client, sessions


async def _register(client, username="learner"):
    response = await client.post("/api/auth/register", json={"username": username, "password": "correct-horse-123"})
    assert response.status_code == 201, response.text
    return response.json()


def _csrf(client):
    return {"X-CSRF-Token": client.cookies[auth.CSRF_COOKIE]}


async def _make_admin(client, sessions):
    user = await _register(client, "adminuser")
    async with sessions() as db:
        record = await db.get(User, user["id"])
        record.role = "admin"
        await db.commit()
    return user


@pytest.mark.asyncio
async def test_revisions_are_atomic_redacted_and_restore_creates_new_version(platform_client):
    client, sessions = platform_client
    await _make_admin(client, sessions)
    secret = "test-secret-never-returned"
    created = await client.post("/api/admin/providers", json={"name": "versioned", "base_url": "https://example.invalid/v1",
        "model": "v1", "api_key": secret}, headers=_csrf(client))
    provider = created.json()
    path = f"/api/admin/providers/{provider['id']}"
    assert (await client.patch(path, json={"model": "v2", "expected_version": 1}, headers=_csrf(client))).status_code == 200
    assert (await client.patch(path, json={"model": "stale", "expected_version": 1}, headers=_csrf(client))).status_code == 409
    history_path = f"/api/admin/revisions/provider/{provider['id']}"
    history = await client.get(history_path + "?limit=1")
    assert history.json()["has_more"]
    assert history.json()["items"][0]["changes"]["model"] == {"before": "v1", "after": "v2"}
    assert secret not in history.text and "encrypted_key" not in history.text
    restored = await client.post(history_path + "/restore", json={"version": 1, "expected_version": 2}, headers=_csrf(client))
    assert restored.status_code == 200 and restored.json()["version"] == 3 and restored.json()["model"] == "v1"
    async with sessions() as db:
        rows = (await db.scalars(select(ConfigRevision).where(ConfigRevision.entity_id == provider["id"])
            .order_by(ConfigRevision.version))).all()
        assert [r.version for r in rows] == [1, 2, 3]
        assert [r.config["model"] for r in rows] == ["v1", "v2", "v1"]
        assert all(admin.decrypt_key(r.config["encrypted_key"]) == secret for r in rows)
    assert (await client.delete(path + "?expected_version=3", headers=_csrf(client))).status_code == 204
    restored = await client.post(history_path + "/restore", json={"version": 2, "expected_version": 4}, headers=_csrf(client))
    assert restored.status_code == 200 and restored.json()["version"] == 5 and restored.json()["model"] == "v2"


@pytest.mark.asyncio
async def test_register_hashes_password_and_token_and_never_exposes_them(platform_client):
    client, sessions = platform_client
    user = await _register(client)
    assert set(user) == {"id", "username", "role"}
    assert user["role"] == "user"
    token = client.cookies[auth.SESSION_COOKIE]
    async with sessions() as db:
        record = await db.get(User, user["id"])
        login = (await db.execute(select(LoginSession))).scalar_one()
        assert record.password_hash.startswith("scrypt$")
        assert "correct-horse-123" not in record.password_hash
        assert login.id != token
        assert login.id == auth._token_hash(token)
    me = await client.get("/api/auth/me")
    assert me.json() == user
    duplicate = await client.post("/api/auth/register", json={"username": "LEARNER", "password": "correct-horse-123"})
    assert duplicate.status_code == 409
    response = await client.post("/api/auth/login", json={"username": "learner", "password": "correct-horse-123"})
    session_cookie = next(value for value in response.headers.get_list("set-cookie") if value.startswith("ra_session="))
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie


@pytest.mark.asyncio
async def test_login_rotates_session_and_csrf_enforced_on_mutations(platform_client):
    client, sessions = platform_client
    user = await _register(client)
    old_token = client.cookies[auth.SESSION_COOKIE]
    invalid = await client.post("/api/auth/login", json={"username": user["username"], "password": "wrong-password"})
    assert invalid.status_code == 401
    response = await client.post(
        "/api/auth/login", json={"username": user["username"], "password": "correct-horse-123"}
    )
    assert response.status_code == 200
    assert client.cookies[auth.SESSION_COOKIE] != old_token
    async with sessions() as db:
        assert await db.get(LoginSession, auth._token_hash(old_token)) is None
    assert (await client.post("/api/mutate")).status_code == 403
    assert (await client.post("/api/mutate", headers={"X-CSRF-Token": "wrong"})).status_code == 403
    assert (await client.post("/api/mutate", headers=_csrf(client))).json() == {"user_id": user["id"]}
    assert (await client.post("/api/auth/logout", headers=_csrf(client))).status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_auth_origin_expiry_and_atomic_database_throttle(platform_client, monkeypatch):
    client, sessions = platform_client
    rejected = await client.post(
        "/api/auth/login",
        json={"username": "learner", "password": "wrong-password"},
        headers={"Origin": "https://attacker.test"},
    )
    assert rejected.status_code == 403
    await _register(client)
    async with sessions() as db:
        login = await db.get(LoginSession, auth._token_hash(client.cookies[auth.SESSION_COOKIE]))
        login.expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    assert (await client.get("/api/auth/me")).status_code == 401
    client.cookies.clear()
    monkeypatch.setattr(auth.settings, "AUTH_MAX_ATTEMPTS", 2)
    payload = {"username": "unknown", "password": "wrong-password"}
    assert (await client.post("/api/auth/login", json=payload)).status_code == 401
    assert (await client.post("/api/auth/login", json=payload)).status_code == 401
    limited = await client.post("/api/auth/login", json=payload)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_admin_configuration_encrypts_keys_and_versions_updates(platform_client):
    client, sessions = platform_client
    await _register(client)
    assert (await client.get("/api/admin/providers")).status_code == 403
    client.cookies.clear()
    await _make_admin(client, sessions)
    payload = {
        "name": "Local model",
        "base_url": "https://api.example.test/v1/",
        "model": "chat-model",
        "api_key": "sk-never-echo-this",
    }
    response = await client.post("/api/admin/providers", json=payload, headers=_csrf(client))
    assert response.status_code == 201, response.text
    provider = response.json()
    assert provider["has_key"] and provider["version"] == 1
    assert provider["base_url"] == "https://api.example.test/v1"
    assert "sk-never-echo-this" not in response.text
    assert "encrypted_key" not in provider
    async with sessions() as db:
        record = await db.get(Provider, provider["id"])
        encrypted = record.encrypted_key
        assert encrypted != payload["api_key"]
        assert admin.decrypt_key(encrypted) == payload["api_key"]
    updated = await client.patch(
        f"/api/admin/providers/{provider['id']}", json={"name": "Renamed", "api_key": "", "expected_version": 1}, headers=_csrf(client)
    )
    assert updated.json()["version"] == 2
    async with sessions() as db:
        assert (await db.get(Provider, provider["id"])).encrypted_key == encrypted
    agent_payload = {
        "name": "Scoring",
        "scene": "evaluate",
        "provider_id": provider["id"],
        "prompt": "Score the answer",
        "tools": ["lookup_resume_field"],
    }
    agent_response = await client.post("/api/admin/agents", json=agent_payload, headers=_csrf(client))
    assert agent_response.status_code == 201, agent_response.text
    assert (await client.post("/api/admin/agents", json=agent_payload, headers=_csrf(client))).status_code == 409
    capabilities = (await client.get("/api/admin/capabilities")).json()
    assert capabilities["scenes"]["evaluate"]["enabled"] is True
    assert capabilities["scenes"]["resume"]["enabled"] is True
    assert (await client.delete(f"/api/admin/providers/{provider['id']}?expected_version=2", headers=_csrf(client))).status_code == 204
    agent = (await client.get(f"/api/admin/agents/{agent_response.json()['id']}")).json()
    assert agent["provider_id"] is None and agent["version"] == 2


@pytest.mark.asyncio
async def test_capabilities_report_scene_policy_independently_of_legacy_credentials(platform_client, monkeypatch):
    client, sessions = platform_client
    await _make_admin(client, sessions)
    empty = (await client.get("/api/admin/capabilities")).json()
    assert all(scene["enabled"] for scene in empty["scenes"].values())
    assert empty["vision"] is True
    assert all("provider_id" not in scene for scene in empty["scenes"].values())
    monkeypatch.setattr(auth.settings, "LLM_API_KEY", "environment-test-key")
    configured = (await client.get("/api/admin/capabilities")).json()
    assert configured == empty
    disabled = await client.post(
        "/api/admin/agents",
        json={"name": "Disabled evaluator", "scene": "evaluate", "enabled": False},
        headers=_csrf(client),
    )
    assert disabled.status_code == 201
    capabilities = (await client.get("/api/admin/capabilities")).json()
    assert capabilities["scenes"]["evaluate"]["enabled"] is False
    assert capabilities["scenes"]["chat"]["enabled"] is True
    provider = await client.post(
        "/api/admin/providers",
        json={
            "name": "Vision",
            "base_url": "https://vision.example.test/v1",
            "model": "vision",
            "api_key": "vision-key",
            "vision": True,
            "enabled": False,
        },
        headers=_csrf(client),
    )
    assert provider.status_code == 201
    assert (await client.get("/api/admin/capabilities")).json() == capabilities
    disabled_vision = await client.post(
        "/api/admin/agents",
        json={"name": "Disabled vision", "scene": "vision", "enabled": False, "provider_id": provider.json()["id"]},
        headers=_csrf(client),
    )
    assert disabled_vision.status_code == 201
    current = (await client.get("/api/admin/capabilities")).json()
    assert current["vision"] is False
    assert current["scenes"]["vision"] == {"enabled": False, "agent_id": disabled_vision.json()["id"]}
    path = f"/api/admin/agents/{disabled_vision.json()['id']}"
    updated = await client.put(path, json={"enabled": True, "expected_version": 1}, headers=_csrf(client))
    assert updated.status_code == 200
    assert (await client.get("/api/admin/capabilities")).json()["vision"] is True
    restored = await client.post(
        f"/api/admin/revisions/agent/{disabled_vision.json()['id']}/restore",
        json={"version": 1, "expected_version": 2}, headers=_csrf(client),
    )
    assert restored.status_code == 200
    assert (await client.get("/api/admin/capabilities")).json()["vision"] is False
    assert (await client.delete(path + "?expected_version=3", headers=_csrf(client))).status_code == 204
    assert (await client.get("/api/admin/capabilities")).json() == capabilities


@pytest.mark.asyncio
async def test_websocket_identity_rejects_untrusted_origin_and_anonymous(monkeypatch):
    monkeypatch.setattr(auth.settings, "CORS_ORIGINS", ["http://localhost:5173"])
    for headers in ([], [(b"origin", b"https://attacker.test")], [(b"origin", b"http://localhost:5173")]):
        websocket = WebSocket(
            {
                "type": "websocket",
                "path": "/ws/test",
                "headers": headers,
                "query_string": b"",
                "scheme": "ws",
                "server": ("testserver", 80),
            },
            receive=None,
            send=None,
        )
        with pytest.raises(WebSocketException) as exc:
            await auth.websocket_user(websocket)
        assert exc.value.code == 1008


def test_initial_migration_matches_models_and_keeps_legacy_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migrations.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE legacy_keep (id INTEGER PRIMARY KEY, value TEXT)"))
        connection.execute(text("INSERT INTO legacy_keep VALUES (1, 'untouched')"))
        from tests.platform_db import _migrate
        _migrate(connection)
        inspector = inspect(connection)
        for table in Base.metadata.tables.values():
            if table.name.startswith("platform_"):
                assert {column["name"] for column in inspector.get_columns(table.name)} == set(table.columns.keys())
        assert connection.execute(text("SELECT value FROM legacy_keep")).scalar_one() == "untouched"
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("environment,confirmation", [("production", "RESET-PLATFORM"), ("development", None)])
async def test_reset_requires_development_and_explicit_confirmation(monkeypatch, environment, confirmation):
    monkeypatch.setattr(auth.settings, "ENVIRONMENT", environment)
    with pytest.raises(ValueError):
        await cli.reset_dev(confirmation)
