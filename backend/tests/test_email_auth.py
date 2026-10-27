"""Mailbox proof, one-time challenges and password/session consistency."""

import asyncio
import json
import threading
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from app.platform import auth
from app.platform.models import EmailChallenge, LoginSession, User, utcnow
from tests.platform_db import isolated_database, postgres_test_url

PASSWORD = "initial-password-123"
NEW_PASSWORD = "replacement-password-456"
EMAIL = "learner@example.test"


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def email_client(request, tmp_path, monkeypatch):
    if request.param == "postgresql" and not postgres_test_url():
        pytest.skip("Set PLATFORM_TEST_DATABASE_URL for real PostgreSQL email auth tests")
    async with isolated_database(request.param, tmp_path) as database:
        monkeypatch.setattr(auth, "async_session_maker", database.sessions)
        monkeypatch.setattr(auth, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError("Redis unavailable")))
        monkeypatch.setattr(auth.settings, "ENVIRONMENT", "development")
        monkeypatch.setattr(auth.settings, "AUTH_COOKIE_SECURE", False)
        monkeypatch.setattr(auth.settings, "CORS_ORIGINS", ["http://localhost:5173"])
        monkeypatch.setattr(auth.settings, "AUTH_MAX_ATTEMPTS", 10)
        monkeypatch.setattr(auth.settings, "AUTH_IP_MAX_ATTEMPTS", 60)
        monkeypatch.setattr(auth.mailer, "smtp_configured", lambda: True)
        codes = {}

        async def send(email, code, purpose):
            codes[(email, purpose)] = code

        monkeypatch.setattr(auth.mailer, "send_verification_code", send)
        app = FastAPI()
        app.add_middleware(auth.AuthCSRFMiddleware)
        app.include_router(auth.router, prefix="/api")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
            headers={"Origin": "http://localhost:5173"},
        ) as client:
            yield client, database.sessions, codes


def csrf(client):
    return {"X-CSRF-Token": client.cookies[auth.CSRF_COOKIE]}


async def request_code(client, codes, purpose="register", email=EMAIL):
    response = await client.post("/api/auth/email/code", json={"email": email, "purpose": purpose})
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "no-store"
    return codes[(email, purpose)]


async def register(client, codes, username="learner", email=EMAIL):
    code = await request_code(client, codes, email=email)
    response = await client.post("/api/auth/register", json={
        "username": username, "email": email, "password": PASSWORD, "code": code,
    })
    assert response.status_code == 201, response.text
    return response.json()


async def login(client, username="learner", password=PASSWORD):
    return await client.post("/api/auth/login", json={"username": username, "password": password})


async def legacy_account(client, sessions, username="legacy"):
    async with sessions() as db:
        user = User(username=username, password_hash=auth.hash_password(PASSWORD), role="user")
        db.add(user)
        await db.commit()
        user_id = user.id
    assert (await login(client, username)).status_code == 200
    return user_id


@pytest.mark.asyncio
async def test_registration_requires_mailbox_proof_and_keeps_existing_response_shape(email_client):
    client, sessions, codes = email_client
    missing = await client.post("/api/auth/register", json={"username": "learner", "password": PASSWORD})
    assert missing.status_code == 422
    code = await request_code(client, codes)
    payload = {"username": "learner", "email": EMAIL, "password": PASSWORD, "code": "999999" if code != "999999" else "888888"}
    assert (await client.post("/api/auth/register", json=payload)).status_code == 400
    payload["code"] = code
    response = await client.post("/api/auth/register", json=payload)
    assert response.status_code == 201
    assert set(response.json()) == {"id", "username", "role"}
    assert code not in response.text and PASSWORD not in response.text
    async with sessions() as db:
        user = await db.get(User, response.json()["id"])
        challenge = await db.get(EmailChallenge, auth._challenge_id(EMAIL, "register"))
        assert user.email == EMAIL
        assert challenge.status == "consumed" and challenge.code_hash == "0" * 64
    assert (await login(client, "  LEARNER@EXAMPLE.TEST  ")).status_code == 200
    assert (await client.get("/api/auth/security")).json() == {"email": EMAIL}


@pytest.mark.asyncio
async def test_disabled_smtp_is_explicit_and_does_not_create_a_challenge(email_client, monkeypatch):
    client, sessions, _ = email_client
    monkeypatch.setattr(auth.mailer, "smtp_configured", lambda: False)
    assert (await client.get("/api/auth/capabilities")).json() == {"email_enabled": False}
    response = await client.post("/api/auth/email/code", json={"email": EMAIL, "purpose": "register"})
    assert response.status_code == 503
    async with sessions() as db:
        assert await db.scalar(select(EmailChallenge.id)) is None


@pytest.mark.asyncio
async def test_codes_are_keyed_hashes_rate_limited_and_replaced_on_resend(email_client, monkeypatch):
    client, sessions, codes = email_client
    numbers = iter([123456, 234567, 345678])
    monkeypatch.setattr(auth.secrets, "randbelow", lambda _: next(numbers))
    old = await request_code(client, codes)
    async with sessions() as db:
        row = await db.get(EmailChallenge, auth._challenge_id(EMAIL, "register"))
        assert old not in row.code_hash and row.code_hash != auth._token_hash(old)
        assert row.status == "sent"
    limited = await client.post("/api/auth/email/code", json={"email": EMAIL, "purpose": "register"})
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
    async with sessions() as db:
        row = await db.get(EmailChallenge, auth._challenge_id(EMAIL, "register"))
        row.resend_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    current = await request_code(client, codes)
    assert old != current
    payload = {"username": "learner", "email": EMAIL, "password": PASSWORD, "code": old}
    assert (await client.post("/api/auth/register", json=payload)).status_code == 400
    payload["code"] = current
    assert (await client.post("/api/auth/register", json=payload)).status_code == 201


@pytest.mark.asyncio
async def test_expiry_and_attempt_exhaustion_cannot_be_bypassed_with_correct_code(email_client):
    client, sessions, codes = email_client
    code = await request_code(client, codes)
    payload = {"username": "learner", "email": EMAIL, "password": PASSWORD,
               "code": "999999" if code != "999999" else "888888"}
    for _ in range(auth.CODE_MAX_ATTEMPTS):
        assert (await client.post("/api/auth/register", json=payload)).status_code == 400
    payload["code"] = code
    assert (await client.post("/api/auth/register", json=payload)).status_code == 400
    async with sessions() as db:
        row = await db.get(EmailChallenge, auth._challenge_id(EMAIL, "register"))
        assert row.attempts == auth.CODE_MAX_ATTEMPTS
        row.attempts = 0
        row.expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    assert (await client.post("/api/auth/register", json=payload)).status_code == 400


@pytest.mark.asyncio
async def test_public_reset_accepts_stale_cookie_and_consumes_code_revoking_all_sessions(email_client):
    client, sessions, codes = email_client
    await register(client, codes)
    first = client.cookies[auth.SESSION_COOKIE]
    client.cookies.clear()
    assert (await login(client)).status_code == 200
    second = client.cookies[auth.SESSION_COOKIE]
    code = await request_code(client, codes, "reset")
    client.cookies.set(auth.SESSION_COOKIE, "expired-cookie", domain="testserver.local", path="/")
    response = await client.post("/api/auth/password/reset", json={"email": EMAIL, "code": code,
                                                                    "new_password": NEW_PASSWORD})
    assert response.status_code == 204 and auth.SESSION_COOKIE not in client.cookies
    async with sessions() as db:
        assert await db.get(LoginSession, auth._token_hash(first)) is None
        assert await db.get(LoginSession, auth._token_hash(second)) is None
        assert await db.scalar(select(EmailChallenge.id).where(EmailChallenge.purpose == "reset")) is None
    assert (await client.post("/api/auth/password/reset", json={"email": EMAIL, "code": code,
                                                               "new_password": PASSWORD})).status_code == 400
    assert (await login(client)).status_code == 401
    assert (await login(client, EMAIL, NEW_PASSWORD)).status_code == 200


@pytest.mark.asyncio
async def test_reset_requests_do_not_reveal_absent_or_unverified_accounts(email_client):
    client, sessions, codes = email_client
    await register(client, codes)
    await legacy_account(client, sessions, "unverified@example.test")
    expected = await client.post("/api/auth/email/code", json={"email": EMAIL, "purpose": "reset"})
    absent = await client.post("/api/auth/email/code", json={"email": "absent@example.test", "purpose": "reset"})
    legacy = await client.post("/api/auth/email/code", json={"email": "unverified@example.test", "purpose": "reset"})
    assert expected.status_code == absent.status_code == legacy.status_code == 202
    assert expected.json() == absent.json() == legacy.json()
    assert ("absent@example.test", "reset") not in codes
    assert ("unverified@example.test", "reset") not in codes


@pytest.mark.asyncio
async def test_reset_response_finishes_before_smtp_and_only_successful_delivery_enables_code(email_client, monkeypatch):
    client, sessions, codes = email_client
    await register(client, codes)
    response_complete = asyncio.Event()
    smtp_started = asyncio.Event()
    release_smtp = asyncio.Event()
    events = []
    original_send = auth.mailer.send_verification_code

    async def delayed_send(email, code, purpose):
        assert response_complete.is_set(), "SMTP must not delay the public reset response"
        events.append("smtp")
        smtp_started.set()
        await release_smtp.wait()
        await original_send(email, code, purpose)

    monkeypatch.setattr(auth.mailer, "send_verification_code", delayed_send)
    app = FastAPI()
    app.add_middleware(auth.AuthCSRFMiddleware)
    app.include_router(auth.router, prefix="/api")

    async def request(email):
        messages = []

        async def receive():
            return {"type": "http.request", "body": json.dumps({"email": email, "purpose": "reset"}).encode(),
                    "more_body": False}

        async def send(message):
            messages.append(message)
            events.append(message["type"])
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete.set()

        await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "scheme": "http",
                   "method": "POST", "path": "/api/auth/email/code", "query_string": b"", "root_path": "",
                   "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
                   "headers": [(b"origin", b"http://localhost:5173"), (b"content-type", b"application/json")]},
                  receive, send)
        return messages

    pending = asyncio.create_task(request(EMAIL))
    try:
        await asyncio.wait_for(response_complete.wait(), 5)
        await asyncio.wait_for(smtp_started.wait(), 5)
        assert events == ["http.response.start", "http.response.body", "smtp"]
        assert not pending.done()
        async with sessions() as db:
            challenge = await db.get(EmailChallenge, auth._challenge_id(EMAIL, "reset"))
            assert challenge.status == "pending"
        assert (EMAIL, "reset") not in codes
    finally:
        release_smtp.set()
        known_messages = await pending
    async with sessions() as db:
        challenge = await db.get(EmailChallenge, auth._challenge_id(EMAIL, "reset"))
        assert challenge.status == "sent"
    events.clear()
    response_complete.clear()
    absent_messages = await request("absent@example.test")
    assert events == ["http.response.start", "http.response.body"]
    assert known_messages == absent_messages
    assert ("absent@example.test", "reset") not in codes


@pytest.mark.asyncio
async def test_challenges_are_scoped_to_mailbox_and_purpose(email_client):
    client, _, codes = email_client
    code = await request_code(client, codes)
    assert (await client.post("/api/auth/password/reset", json={"email": EMAIL, "code": code,
                                                               "new_password": NEW_PASSWORD})).status_code == 400
    assert (await client.post("/api/auth/register", json={"username": "learner", "email": "other@example.test",
                                                         "password": PASSWORD, "code": code})).status_code == 400


@pytest.mark.asyncio
async def test_change_password_requires_csrf_and_current_password_and_revokes_sessions(email_client):
    client, sessions, codes = email_client
    await register(client, codes)
    first = client.cookies[auth.SESSION_COOKIE]
    client.cookies.clear()
    assert (await login(client)).status_code == 200
    second = client.cookies[auth.SESSION_COOKIE]
    code = await request_code(client, codes, "reset")
    payload = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert (await client.post("/api/auth/password/change", json=payload)).status_code == 403
    wrong = await client.post("/api/auth/password/change", json={**payload, "current_password": "wrong-password"},
                              headers=csrf(client))
    assert wrong.status_code == 400
    assert (await client.get("/api/auth/me")).status_code == 200
    assert (await client.post("/api/auth/password/change", json=payload, headers=csrf(client))).status_code == 204
    async with sessions() as db:
        assert await db.get(LoginSession, auth._token_hash(first)) is None
        assert await db.get(LoginSession, auth._token_hash(second)) is None
    assert (await client.post("/api/auth/password/reset", json={"email": EMAIL, "code": code,
                                                               "new_password": PASSWORD})).status_code == 400
    assert (await login(client)).status_code == 401
    assert (await login(client, password=NEW_PASSWORD)).status_code == 200


@pytest.mark.asyncio
async def test_legacy_account_binds_verified_mail_without_losing_username_login(email_client):
    client, sessions, codes = email_client
    await legacy_account(client, sessions)
    assert (await client.get("/api/auth/security")).json() == {"email": None}
    payload = {"email": "LEGACY@EXAMPLE.TEST", "current_password": PASSWORD}
    sent = await client.post("/api/auth/email/bind/code", json=payload, headers=csrf(client))
    assert sent.status_code == 202
    code = codes[("legacy@example.test", "bind")]
    assert (await client.post("/api/auth/email/bind", json={**payload, "code": code},
                              headers=csrf(client))).status_code == 204
    assert (await login(client, "legacy@example.test")).status_code == 200
    assert (await login(client, "legacy")).status_code == 200
    again = await client.post("/api/auth/email/bind/code", json=payload, headers=csrf(client))
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_binding_codes_cannot_be_used_by_another_user(email_client):
    client, sessions, codes = email_client
    await legacy_account(client, sessions, "first")
    payload = {"email": EMAIL, "current_password": PASSWORD}
    assert (await client.post("/api/auth/email/bind/code", json=payload, headers=csrf(client))).status_code == 202
    code = codes[(EMAIL, "bind")]
    await legacy_account(client, sessions, "second")
    assert (await client.post("/api/auth/email/bind", json={**payload, "code": code},
                              headers=csrf(client))).status_code == 400


@pytest.mark.asyncio
async def test_mail_failures_keep_codes_unusable_and_preserve_resend_cooldown(email_client, monkeypatch):
    client, sessions, codes = email_client
    original = auth.mailer.send_verification_code

    async def fail(*args):
        raise auth.mailer.MailDeliveryError("sanitized transport error")

    monkeypatch.setattr(auth.mailer, "send_verification_code", fail)
    response = await client.post("/api/auth/email/code", json={"email": EMAIL, "purpose": "register"})
    assert response.status_code == 503
    assert "transport error" not in response.text
    async with sessions() as db:
        row = await db.get(EmailChallenge, auth._challenge_id(EMAIL, "register"))
        assert row.status == "pending"
    response = await client.post("/api/auth/email/code", json={"email": EMAIL, "purpose": "register"})
    assert response.status_code == 429
    async with sessions() as db:
        row = await db.get(EmailChallenge, auth._challenge_id(EMAIL, "register"))
        row.resend_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    monkeypatch.setattr(auth.mailer, "send_verification_code", original)
    assert await request_code(client, codes)


@pytest.mark.asyncio
async def test_reset_delivery_failure_does_not_reveal_accounts_through_retry_or_enable_unsent_code(email_client, monkeypatch):
    client, _, codes = email_client
    await register(client, codes)
    attempted = {}

    async def fail(email, code, purpose):
        attempted[email] = code
        raise auth.mailer.MailDeliveryError("delivery unavailable")

    monkeypatch.setattr(auth.mailer, "send_verification_code", fail)
    for email in (EMAIL, "absent@example.test"):
        initial = await client.post("/api/auth/email/code", json={"email": email, "purpose": "reset"})
        assert initial.status_code == 202
        again = await client.post("/api/auth/email/code", json={"email": email, "purpose": "reset"})
        assert again.status_code == 429
    assert (await client.post("/api/auth/password/reset", json={
        "email": EMAIL, "code": attempted[EMAIL], "new_password": NEW_PASSWORD,
    })).status_code == 400
    assert (await login(client)).status_code == 200


@pytest.mark.asyncio
async def test_concurrent_reset_has_only_one_winner(email_client):
    client, _, codes = email_client
    await register(client, codes)
    code = await request_code(client, codes, "reset")
    responses = await asyncio.gather(*[
        client.post("/api/auth/password/reset", json={"email": EMAIL, "code": code,
                                                       "new_password": password})
        for password in ("one-new-password", "two-new-password")
    ])
    assert sorted(response.status_code for response in responses) == [204, 400]


@pytest.mark.asyncio
async def test_login_cannot_mint_old_password_session_after_reset(email_client, monkeypatch):
    client, sessions, codes = email_client
    await register(client, codes)
    code = await request_code(client, codes, "reset")
    verified = threading.Event()
    release = threading.Event()
    original = auth.verify_password

    def delayed(password, encoded):
        result = original(password, encoded)
        if password == PASSWORD:
            verified.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(auth, "verify_password", delayed)
    pending = asyncio.create_task(login(client))
    try:
        assert await asyncio.to_thread(verified.wait, 5)
        response = await client.post("/api/auth/password/reset", json={"email": EMAIL, "code": code,
                                                                        "new_password": NEW_PASSWORD})
        assert response.status_code == 204
    finally:
        release.set()
    assert (await pending).status_code == 401
    async with sessions() as db:
        assert await db.scalar(select(LoginSession.id)) is None


@pytest.mark.asyncio
async def test_login_aliases_share_account_limit_and_legacy_ambiguity_is_rejected(email_client, monkeypatch):
    client, sessions, codes = email_client
    await register(client, codes)
    monkeypatch.setattr(auth.settings, "AUTH_MAX_ATTEMPTS", 2)
    assert (await login(client, password="wrong-password")).status_code == 401
    assert (await login(client, EMAIL, "wrong-password")).status_code == 401
    assert (await login(client)).status_code == 429
    async with sessions() as db:
        db.add(User(username=EMAIL, password_hash=auth.hash_password(PASSWORD), role="user"))
        await db.commit()
    monkeypatch.setattr(auth.settings, "AUTH_MAX_ATTEMPTS", 10)
    assert (await login(client, EMAIL)).status_code == 401


@pytest.mark.asyncio
async def test_untrusted_origins_and_address_injection_are_rejected(email_client):
    client, _, codes = email_client
    response = await client.post("/api/auth/email/code", json={"email": EMAIL, "purpose": "register"},
                                 headers={"Origin": "https://attacker.test"})
    assert response.status_code == 403 and not codes
    for email in ("a@example.test\r\nBcc: victim@example.test", "a..b@example.test", "a@-example.test", "中文@example.test"):
        assert (await client.post("/api/auth/email/code", json={"email": email, "purpose": "register"})).status_code == 422
    code = await request_code(client, codes)
    assert (await client.post("/api/auth/register", json={"username": "fake@example.test", "email": EMAIL,
                                                         "password": PASSWORD, "code": code})).status_code == 422
