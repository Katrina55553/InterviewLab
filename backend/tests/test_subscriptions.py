"""Signed OIDC grants, durable account isolation and real Responses wire contracts."""

import asyncio
import base64
import hashlib
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import select

from app.core.config import settings
from app.platform import auth, runtime, subscription_client, subscriptions, user_settings
from app.platform.admin import decrypt_key, encrypt_key
from app.platform.contracts import PlatformError
from app.platform.models import AiCall, UserAISettings, UserModelCredential, UserOAuthSession
from app.platform.oauth_oidc import ISSUER, JWKS_URL
from tests.platform_db import isolated_database, postgres_test_url
from tests.test_user_model_connections import register

SELECTION = {"connectionType": "builtin", "authMethod": "oauth", "providerId": "openai", "modelId": "account-model"}


def encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def oauth(request, tmp_path, monkeypatch, signing_key):
    from app.main import app

    if request.param == "postgresql" and not postgres_test_url():
        pytest.skip("Set PLATFORM_TEST_DATABASE_URL for isolated subscription PostgreSQL tests")
    async with isolated_database(request.param, tmp_path) as database:
        for module in (auth, user_settings, subscriptions, runtime):
            monkeypatch.setattr(module, "async_session_maker", database.sessions)
        monkeypatch.setattr(auth, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
        monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://localhost:5173"])
        monkeypatch.setattr(settings, "PLATFORM_DEMO_MODE", False)
        monkeypatch.setattr(settings, "USER_LLM_OAUTH_CLIENT_ID", "")
        monkeypatch.setattr(settings, "USER_LLM_OAUTH_REDIRECT_URI", "http://127.0.0.1:8000/api/settings/ai/oauth/callback")
        monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", False)
        state = SimpleNamespace(nonce="", grants=0, refreshes=0, calls=[], claims={}, scope=subscriptions.SCOPES,
                                terminal="response.completed", token_error=False, signature_invalid=False, tool_once=False,
                                refresh_started=None, refresh_continue=None, revocations=[], revocation_results=[],
                                discovery_endpoint=ISSUER + "/api/accounts/oauth/revoke", discoveries=0)
        numbers = signing_key.public_key().public_numbers()
        jwk = {"kty": "RSA", "kid": "test-key", "alg": "RS256", "use": "sig",
               "n": encoded(numbers.n.to_bytes(256)), "e": encoded(numbers.e.to_bytes(3))}

        def id_token():
            header = encoded(json.dumps({"alg": "RS256", "kid": "test-key"}).encode())
            body = encoded(json.dumps({"iss": ISSUER, "sub": "verified-account", "aud": "oaiapp_test",
                "exp": time.time() + 3600, "iat": time.time(), "nonce": state.nonce, **state.claims}).encode())
            value = (header + "." + body).encode()
            signature = b"invalid-signature" if state.signature_invalid else signing_key.sign(value, padding.PKCS1v15(), hashes.SHA256())
            return header + "." + body + "." + encoded(signature)

        async def upstream(request):
            if str(request.url) == subscriptions.DISCOVERY_URL:
                state.discoveries += 1
                return httpx.Response(200, json={"issuer": ISSUER, "revocation_endpoint": state.discovery_endpoint})
            if str(request.url) == ISSUER + "/api/accounts/oauth/revoke":
                assert request.method == "POST" and not request.url.query
                assert request.headers["content-type"] == "application/x-www-form-urlencoded"
                state.revocations.append(parse_qs(request.content.decode()))
                result = state.revocation_results.pop(0) if state.revocation_results else 200
                if result == "network":
                    raise httpx.ReadTimeout("private-refresh must-not-leak", request=request)
                return httpx.Response(result, content=b"" if result == 200 else b"private-refresh must-not-leak")
            if str(request.url) == JWKS_URL:
                return httpx.Response(200, json={"keys": [jwk]})
            if str(request.url) == subscriptions.TOKEN_URL:
                values = parse_qs(request.content.decode())
                if state.token_error:
                    return httpx.Response(400, json={"error": "invalid_grant", "secret": "must-not-leak"})
                if values["grant_type"] == ["refresh_token"]:
                    state.refreshes += 1
                    if state.refresh_started:
                        state.refresh_started.set()
                        await state.refresh_continue.wait()
                    await asyncio.sleep(.03)
                    return httpx.Response(200, json={"access_token": "renewed-private-access", "refresh_token": "renewed-private-refresh",
                                                   "expires_in": 3600, "scope": state.scope})
                state.grants += 1
                state.exchange = values
                return httpx.Response(200, json={"access_token": "private-access", "refresh_token": "private-refresh",
                    "expires_in": 3600, "scope": state.scope, "id_token": id_token()})
            if request.url.path == "/v1/models":
                assert request.headers["authorization"].startswith("Bearer ")
                return httpx.Response(200, json={"models": [{"slug": "account-model", "display_name": "My plan model", "visibility": "list"},
                                                           {"slug": "hidden", "visibility": "hidden"}]})
            if request.url.path == "/v1/responses":
                state.calls.append(json.loads(request.content))
                events = [{"type": "response.output_text.delta", "delta": "OK"},
                          {"type": state.terminal, "response": {"usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}}]
                if any(isinstance(item.get("content"), list) and any(part.get("type") == "input_image"
                       for part in item["content"]) for item in state.calls[-1]["input"]):
                    events[0]["delta"] = "RED"
                if state.tool_once and len(state.calls) == 1:
                    events[0] = {"type": "response.output_item.done", "output_index": 0, "item": {
                        "type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup_resume_field",
                        "arguments": '{"field":"skills"}', "status": "completed"}}
                return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                    content="".join("data: " + json.dumps(event) + "\n\n" for event in events))
            raise AssertionError(f"Unexpected upstream: {request.url}")

        def factory():
            return httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        monkeypatch.setattr(subscriptions, "oauth_http_client", factory)
        monkeypatch.setattr(subscription_client, "oauth_http_client", factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                headers={"Origin": "http://localhost:5173"}) as client:
            state.client, state.database = client, database
            yield state


async def begin(oauth):
    response = await oauth.client.post("/api/settings/ai/oauth/start", json={"providerId": "openai"})
    assert response.status_code == 200, response.text
    session = response.json()
    params = parse_qs(urlsplit(session["authorization_url"]).query)
    oauth.nonce = params["nonce"][0]
    callback = session["redirect_uri"] + "?" + urlencode({"code": "single-use-private-code", "state": params["state"][0], "client_id": "oaiapp_test"})
    return session, params, callback


async def authorize(oauth):
    session, params, callback = await begin(oauth)
    result = await oauth.client.post(f"/api/settings/ai/oauth/{session['session_id']}/complete", json={"callback_url": callback})
    assert result.status_code == 200, result.text
    return session, params, callback


async def saved_config(oauth):
    owner = await register(oauth.client)
    await authorize(oauth)
    result = await oauth.client.put("/api/settings/ai", json={"selection": SELECTION, "expected_version": 0})
    assert result.status_code == 200, result.text
    return owner, (await runtime.freeze_configs(user_id=owner))["chat"]


@pytest.mark.asyncio
async def test_subscription_visual_probe_uses_image_responses_and_survives_normal_token_refresh(oauth, monkeypatch):
    from app.platform import vision_settings

    monkeypatch.setattr(vision_settings, "async_session_maker", oauth.database.sessions)
    owner, _ = await saved_config(oauth)
    result = await oauth.client.post("/api/settings/ai/vision/probe", json=SELECTION)
    assert result.status_code == 200 and result.json()["state"] == "reachable", result.text
    request = oauth.calls[-1]
    assert request["store"] is False and request["input"][0]["content"][1]["type"] == "input_image"
    saved = await oauth.client.put("/api/settings/ai/vision", json={"enabled": True, "selection": SELECTION,
        "expected_version": 0, "probe_token": result.json()["probe_token"]})
    assert saved.status_code == 200, saved.text
    frozen = await runtime.freeze_configs(user_id=owner)
    assert frozen["vision"]["enabled"] is True and frozen["vision"]["auth_method"] == "oauth"
    assert frozen["vision"]["encrypted_key"] is None
    async with oauth.database.sessions() as db, db.begin():
        credential = await db.get(UserModelCredential, frozen["vision"]["user_provider_id"])
        credential.version += 1
    assert (await oauth.client.get("/api/settings/ai/vision")).json()["verified"] is True
    assert (await oauth.client.delete("/api/settings/ai/auth?provider_id=openai")).status_code == 200
    state = (await oauth.client.get("/api/settings/ai/vision")).json()
    assert state["selection"] is None and state["enabled"] is False


@pytest.mark.asyncio
async def test_authorization_is_private_single_use_pkce_and_does_not_save_the_model(oauth):
    client = oauth.client
    assert (await client.post("/api/settings/ai/oauth/start", json={"providerId": "openai"})).status_code == 401
    owner = await register(client)
    csrf = client.headers.pop("X-CSRF-Token")
    assert (await client.post("/api/settings/ai/oauth/start", json={"providerId": "openai"})).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    session, params, callback = await begin(oauth)
    assert params["client_id"] == ["dynamic_agent_client"] and params["agent_name_hint"] == ["InterviewLab"]
    async with oauth.database.sessions() as db:
        row = await db.get(UserOAuthSession, session["session_id"])
        pending = json.loads(decrypt_key(row.encrypted_data))
        assert row.user_id == owner and pending["verifier"] not in row.encrypted_data
        assert encoded(hashlib.sha256(pending["verifier"].encode()).digest()) == params["code_challenge"][0]
    endpoint = f"/api/settings/ai/oauth/{session['session_id']}/complete"
    assert (await client.post(endpoint, json={"callback_url": callback.replace(params["state"][0], "wrong-state")})).status_code == 400
    result = await client.post(endpoint, json={"callback_url": callback})
    assert result.status_code == 200 and result.json()["auth"]["method"] == "oauth"
    assert "private-access" not in result.text and "private-refresh" not in result.text
    assert (await client.post(endpoint, json={"callback_url": callback})).status_code in {400, 409}
    assert oauth.grants == 1
    assert (await client.get("/api/settings/ai")).json()["selection"] is None
    assert (await client.get("/api/settings/ai/models?provider_id=openai")).json()[0]["id"] == "account-model"
    next_session, next_params, _ = await begin(oauth)
    assert next_params["client_id"] == ["oaiapp_test"]
    assert next_params["ext_agent_host_id"] == params["ext_agent_host_id"]
    await client.delete(f"/api/settings/ai/oauth/{next_session['session_id']}")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["signature", "nonce", "iss", "aud", "exp", "iat", "scope"])
async def test_invalid_identity_or_missing_plan_permission_never_installs_a_grant(oauth, invalid):
    await register(oauth.client)
    session, _, callback = await begin(oauth)
    if invalid == "scope":
        oauth.scope = "openid profile email"
    elif invalid == "signature":
        oauth.signature_invalid = True
    else:
        oauth.claims[invalid] = 0 if invalid == "exp" else time.time() + 3600 if invalid == "iat" else "wrong"
    response = await oauth.client.post(f"/api/settings/ai/oauth/{session['session_id']}/complete", json={"callback_url": callback})
    assert response.status_code == 400 and "private-" not in response.text
    async with oauth.database.sessions() as db:
        assert await db.scalar(select(UserModelCredential)) is None
        assert (await db.get(UserOAuthSession, session["session_id"])).status == "failed"


@pytest.mark.asyncio
async def test_callback_completion_cancellation_and_user_ownership(oauth):
    await register(oauth.client)
    session, _, callback = await begin(oauth)
    response = await oauth.client.get("/api/settings/ai/oauth/callback?" + urlsplit(callback).query)
    assert response.status_code == 200 and "private" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert (await oauth.client.get(f"/api/settings/ai/oauth/{session['session_id']}")).json()["status"] == "completed"
    await register(oauth.client, "another_owner")
    assert (await oauth.client.get(f"/api/settings/ai/oauth/{session['session_id']}")).status_code == 404
    session, _, callback = await begin(oauth)
    await oauth.client.delete(f"/api/settings/ai/oauth/{session['session_id']}")
    assert (await oauth.client.post(f"/api/settings/ai/oauth/{session['session_id']}/complete", json={"callback_url": callback})).status_code in {400, 409}


@pytest.mark.asyncio
async def test_saved_subscription_uses_responses_for_completion_and_chat_without_snapshot_tokens(oauth):
    owner, config = await saved_config(oauth)
    assert config["encrypted_key"] is None and config["auth_method"] == "oauth"
    assert "private-" not in json.dumps(config)
    with runtime.call_scope(owner):
        assert await runtime.complete(config, "Say OK", json_mode=False) == "OK"
        pieces = [piece async for piece in runtime.stream_chat(config, [{"role": "user", "content": "Hi"}])]
    assert pieces == ["OK"]
    assert len(oauth.calls) == 2
    for body in oauth.calls:
        assert body["store"] is False and body["stream"] is True
        assert "max_output_tokens" not in body and "max_tokens" not in body
        assert all(item.get("role") != "system" for item in body["input"])
    async with oauth.database.sessions() as db:
        calls = list((await db.scalars(select(AiCall))).all())
        assert len(calls) == 2 and all(call.status == "completed" and call.reserved_tokens == 3 for call in calls)
    await oauth.client.delete("/api/settings/ai/auth?provider_id=openai")
    with runtime.call_scope(owner), pytest.raises(PlatformError):
        await runtime.complete(config, "must not send", json_mode=False)
    assert len(oauth.calls) == 2


@pytest.mark.asyncio
async def test_refresh_is_serialized_and_disconnect_cannot_resurrect_a_frozen_grant(oauth):
    _, config = await saved_config(oauth)
    async with oauth.database.sessions() as db, db.begin():
        row = await db.get(UserModelCredential, config["user_provider_id"])
        token = json.loads(decrypt_key(row.encrypted_key))
        token["expires_at"] = 0
        row.encrypted_key = encrypt_key(json.dumps(token))
    tokens = await asyncio.gather(*(subscriptions.access_token(config, oauth.database.sessions) for _ in range(3)))
    assert tokens == ["renewed-private-access"] * 3 and oauth.refreshes == 1
    async with oauth.database.sessions() as db:
        row = await db.get(UserModelCredential, config["user_provider_id"])
        assert row.refresh_owner is None and row.refresh_until is None
    await oauth.client.delete("/api/settings/ai/auth?provider_id=openai")
    with pytest.raises(PlatformError):
        await subscriptions.access_token(config, oauth.database.sessions)


@pytest.mark.asyncio
async def test_failed_or_truncated_responses_are_never_accepted_as_success(oauth):
    owner, config = await saved_config(oauth)
    oauth.terminal = "response.incomplete"
    with runtime.call_scope(owner), pytest.raises(PlatformError):
        await runtime.complete(config, "Say OK", json_mode=False)
    async with oauth.database.sessions() as db:
        call = await db.scalar(select(AiCall))
        assert call.status == "unknown"


@pytest.mark.asyncio
async def test_refresh_failure_preserves_tokens_and_logout_fences_a_late_refresh(oauth):
    _, config = await saved_config(oauth)
    async with oauth.database.sessions() as db, db.begin():
        row = await db.get(UserModelCredential, config["user_provider_id"])
        token = json.loads(decrypt_key(row.encrypted_key))
        token["expires_at"] = 0
        row.encrypted_key = encrypt_key(json.dumps(token))
        original = row.encrypted_key
    oauth.token_error = True
    with pytest.raises(PlatformError):
        await subscriptions.access_token(config, oauth.database.sessions)
    async with oauth.database.sessions() as db:
        row = await db.get(UserModelCredential, config["user_provider_id"])
        assert row.encrypted_key == original and row.refresh_owner is None
    oauth.token_error = False
    oauth.refresh_started, oauth.refresh_continue = asyncio.Event(), asyncio.Event()
    refresh = asyncio.create_task(subscriptions.access_token(config, oauth.database.sessions))
    await asyncio.wait_for(oauth.refresh_started.wait(), 3)
    disconnect = asyncio.create_task(oauth.client.delete("/api/settings/ai/auth?provider_id=openai"))
    await asyncio.sleep(.05)
    assert not disconnect.done() and not oauth.revocations
    oauth.refresh_continue.set()
    assert await refresh == "renewed-private-access"
    response = await disconnect
    assert response.json()["revocation"] == "revoked"
    assert oauth.revocations[0]["token"] == ["renewed-private-refresh"]
    with pytest.raises(PlatformError):
        await subscriptions.access_token(config, oauth.database.sessions)
    async with oauth.database.sessions() as db:
        assert await db.get(UserModelCredential, config["user_provider_id"]) is None


@pytest.mark.asyncio
async def test_disconnect_revokes_oauth_clears_selection_and_cancels_pending_login(oauth):
    owner, config = await saved_config(oauth)
    pending, _, _ = await begin(oauth)
    response = await oauth.client.delete("/api/settings/ai/auth?provider_id=openai")
    assert response.status_code == 200 and response.json()["state"] == "unconfigured"
    assert response.json()["revocation"] == "revoked"
    assert "private-" not in response.text
    assert oauth.revocations == [{"token": ["private-refresh"], "token_type_hint": ["refresh_token"], "client_id": ["oaiapp_test"]}]
    async with oauth.database.sessions() as db:
        assert await db.get(UserModelCredential, config["user_provider_id"]) is None
        assert await db.get(UserAISettings, owner) is None
        row = await db.get(UserOAuthSession, pending["session_id"])
        assert row.status == "cancelled" and decrypt_key(row.encrypted_data) == "{}"
    assert (await oauth.client.delete("/api/settings/ai/auth?provider_id=openai")).status_code == 200
    assert len(oauth.revocations) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("results", "expected"), [([503, 502, 200], "revoked"), (["network", 200], "revoked"),
    ([503, 503, 503], "unconfirmed"), (["network"] * 3, "unconfirmed"), ([400], "unconfirmed"), ([204], "unconfirmed")])
async def test_disconnect_retries_transient_failures_and_reports_unconfirmed_revocation(oauth, results, expected):
    owner, config = await saved_config(oauth)
    oauth.revocation_results = list(results)
    response = await oauth.client.delete("/api/settings/ai/auth?provider_id=openai")
    assert response.status_code == 200 and response.json()["revocation"] == expected
    assert response.json()["state"] == "unconfigured"
    assert len(oauth.revocations) == len(results)
    assert "private-" not in response.text and "must-not-leak" not in response.text
    if expected == "unconfirmed":
        assert "ChatGPT 设置" in response.json()["message"]
    async with oauth.database.sessions() as db:
        assert await db.get(UserModelCredential, config["user_provider_id"]) is None
        assert await db.get(UserAISettings, owner) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["https://untrusted.example/revoke", "http://auth.openai.com/revoke",
                                    "https://auth.openai.com/revoke?token=secret", None])
async def test_disconnect_never_sends_tokens_to_untrusted_discovery_endpoint(oauth, endpoint):
    await saved_config(oauth)
    oauth.discovery_endpoint = endpoint
    response = await oauth.client.delete("/api/settings/ai/auth?provider_id=openai")
    assert response.status_code == 200 and response.json()["revocation"] == "unconfirmed"
    assert not oauth.revocations


@pytest.mark.asyncio
async def test_disconnect_removes_undecryptable_credentials_without_reporting_remote_success(oauth):
    _, config = await saved_config(oauth)
    async with oauth.database.sessions() as db, db.begin():
        row = await db.get(UserModelCredential, config["user_provider_id"])
        row.encrypted_key = "invalid-ciphertext"
    response = await oauth.client.delete("/api/settings/ai/auth?provider_id=openai")
    assert response.status_code == 200 and response.json()["revocation"] == "unconfirmed"
    assert not oauth.revocations and not oauth.discoveries


@pytest.mark.asyncio
async def test_disconnect_requires_csrf_and_cannot_revoke_another_users_grant(oauth):
    _, config = await saved_config(oauth)
    csrf = oauth.client.headers.pop("X-CSRF-Token")
    assert (await oauth.client.delete("/api/settings/ai/auth?provider_id=openai")).status_code == 403
    oauth.client.headers["X-CSRF-Token"] = csrf
    await register(oauth.client, "another_owner")
    assert (await oauth.client.delete("/api/settings/ai/auth?provider_id=openai")).status_code == 200
    assert not oauth.revocations
    async with oauth.database.sessions() as db:
        assert await db.get(UserModelCredential, config["user_provider_id"]) is not None


@pytest.mark.asyncio
async def test_namespace_tool_loop_preserves_history_and_audits_both_requests(oauth):
    owner, config = await saved_config(oauth)
    config["tools"] = ["lookup_resume_field"]
    oauth.tool_once = True
    with runtime.call_scope(owner):
        assert await runtime.complete(config, "Check my skills", json_mode=False) == "OK"
    assert len(oauth.calls) == 2
    assert oauth.calls[0]["tools"][0]["type"] == "namespace"
    assert any(item.get("type") == "function_call_output" and item["call_id"] == "call_1" for item in oauth.calls[1]["input"])
    async with oauth.database.sessions() as db:
        assert len(list((await db.scalars(select(AiCall))).all())) == 2
