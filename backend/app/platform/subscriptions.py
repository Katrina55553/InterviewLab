"""Server-owned ChatGPT authorization. No OAuth token is returned to the browser."""

import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update

from app.core.config import settings
from app.core.database import async_session_maker
from app.platform.admin import decrypt_key, encrypt_key
from app.platform.auth import current_user
from app.platform.contracts import PlatformError
from app.platform.models import User, UserModelCredential, UserOAuthHost, UserOAuthSession, new_id, utcnow
from app.platform.oauth_oidc import ISSUER, verify_id_token

router = APIRouter(prefix="/settings/ai/oauth", tags=["personal-ai-oauth"])
PROVIDER_ID = "openai"
BASE_URL = "https://api.openai.com/v1"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"


def oauth_http_client():
    return httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)


def unavailable():
    return PlatformError("AI_CONFIGURATION_ERROR", "订阅授权不可用，请在 AI 设置中重新登录并授权使用 ChatGPT 计划", status=409, retry_policy="never")


def auth_json(row):
    return {"state": "configured" if row else "unconfigured", "method": "oauth",
            "source": "ChatGPT 订阅" if row else None}


async def token_data(response, client_id, client, *, nonce=None, previous=None):
    access = response.get("access_token")
    refresh = response.get("refresh_token") or (previous or {}).get("refresh_token")
    lifetime = response.get("expires_in")
    scope = response.get("scope", " ".join((previous or {}).get("scopes", [])))
    if (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh
            or not isinstance(lifetime, (int, float)) or not 0 < lifetime <= 365 * 86400
            or not isinstance(scope, str)):
        raise ValueError("Invalid OAuth token response")
    id_token = response.get("id_token")
    if id_token:
        identity = await verify_id_token(id_token, client_id, client, nonce=nonce)
        if previous and identity["sub"] != previous["identity"]["sub"]:
            raise ValueError("OAuth account changed during refresh")
    elif previous:
        identity, id_token = previous["identity"], previous["id_token"]
    else:
        raise ValueError("Missing ID token")
    return {"access_token": access, "refresh_token": refresh, "expires_at": time.time() + lifetime,
            "scopes": scope.split(), "client_id": client_id, "id_token": id_token, "identity": identity}


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    providerId: str = Field(pattern="^openai$")


class FinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    callback_url: str = Field(min_length=1, max_length=32768)


def redirect_uri():
    value = settings.USER_LLM_OAUTH_REDIRECT_URI
    parsed = urlsplit(value)
    if (parsed.query or parsed.fragment or parsed.username or parsed.password or not parsed.hostname
            or not (parsed.scheme == "https" or parsed.scheme == "http" and parsed.hostname == "127.0.0.1")
            or not settings.USER_LLM_OAUTH_CLIENT_ID and parsed.hostname != "127.0.0.1"):
        raise HTTPException(503, "订阅登录回调地址配置无效；动态注册需要 127.0.0.1 回调")
    return value


async def owned_session(db, session_id, user_id):
    row = await db.get(UserOAuthSession, session_id)
    if not row or row.user_id != user_id:
        raise HTTPException(404, "授权会话不存在")
    return row


@router.post("/start")
async def start(body: StartRequest, user: User = Depends(current_user)):
    callback = redirect_uri()
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    async with async_session_maker() as db, db.begin():
        await db.get(User, user.id, with_for_update=True)
        await db.execute(delete(UserOAuthSession).where(UserOAuthSession.user_id == user.id,
            (UserOAuthSession.expires_at < utcnow()) | UserOAuthSession.status.not_in(["pending", "exchanging"])))
        await db.execute(update(UserOAuthSession).where(UserOAuthSession.user_id == user.id,
            UserOAuthSession.provider_id == body.providerId, UserOAuthSession.status.in_(["pending", "exchanging"]))
            .values(status="cancelled", encrypted_data=encrypt_key("{}")))
        host = await db.get(UserOAuthHost, user.id)
        if host is None:
            host = UserOAuthHost(user_id=user.id, host_id=new_id())
            db.add(host)
        credential = await db.scalar(select(UserModelCredential).where(UserModelCredential.user_id == user.id,
            UserModelCredential.provider_id == body.providerId))
        previous = json.loads(decrypt_key(credential.encrypted_key)) if credential and credential.auth_method == "oauth" else None
        client_id = (previous or {}).get("client_id") or settings.USER_LLM_OAUTH_CLIENT_ID or "dynamic_agent_client"
        pending = {"state": state, "nonce": nonce, "verifier": verifier, "client_id": client_id, "redirect_uri": callback,
                   "subject": previous["identity"]["sub"] if previous else None}
        row = UserOAuthSession(id=new_id(), user_id=user.id, provider_id=body.providerId,
            state_hash=hashlib.sha256(state.encode()).hexdigest(), encrypted_data=encrypt_key(json.dumps(pending)),
            status="pending", expires_at=utcnow() + timedelta(minutes=10))
        db.add(row)
        params = {"client_id": client_id, "ext_agent_host_id": "urn:uuid:" + host.host_id,
            "response_type": "code", "redirect_uri": callback, "scope": SCOPES, "resource": BASE_URL,
            "state": state, "nonce": nonce, "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")}
        if client_id == "dynamic_agent_client":
            params["agent_name_hint"] = "InterviewLab"
        return {"session_id": row.id, "authorization_url": AUTHORIZE_URL + "?" + urlencode(params),
                "redirect_uri": callback, "expires_in": 600}


async def finish_authorization(session_id, query, *, user_id=None):
    async with async_session_maker() as db, db.begin():
        row = await db.get(UserOAuthSession, session_id)
        if not row or user_id is not None and row.user_id != user_id:
            raise HTTPException(404, "授权会话不存在")
        state = query.get("state", "")
        if not secrets.compare_digest(row.state_hash, hashlib.sha256(state.encode()).hexdigest()):
            raise HTTPException(400, "授权状态不匹配，请重新登录")
        pending = json.loads(decrypt_key(row.encrypted_data))
        claimed = await db.execute(update(UserOAuthSession).where(UserOAuthSession.id == row.id,
            UserOAuthSession.status == "pending", UserOAuthSession.expires_at > utcnow()).values(status="exchanging"))
        if not claimed.rowcount:
            raise HTTPException(409, "授权会话已使用、过期或取消，请重新登录")
        owner = row.user_id
    try:
        if query.get("error") or query.get("iss", ISSUER) != ISSUER:
            raise ValueError("OAuth authorization denied")
        client_id = query.get("client_id") or pending["client_id"]
        if (not client_id or len(client_id) > 255 or client_id == "dynamic_agent_client"
                or pending["client_id"] != "dynamic_agent_client" and client_id != pending["client_id"]
                or not query.get("code")):
            raise ValueError("Invalid OAuth registration callback")
        async with oauth_http_client() as client:
            response = await client.post(TOKEN_URL, data={"grant_type": "authorization_code", "client_id": client_id,
                "code": query["code"], "code_verifier": pending["verifier"], "redirect_uri": pending["redirect_uri"], "resource": BASE_URL})
            response.raise_for_status()
            token = await token_data(response.json(), client_id, client, nonce=pending["nonce"])
        if pending.get("subject") and pending["subject"] != token["identity"]["sub"]:
            raise ValueError("Reauthorization returned a different account")
        if DIRECT_SCOPE not in token["scopes"]:
            raise ValueError("ChatGPT plan permission was not granted")
        async with async_session_maker() as db, db.begin():
            await db.get(User, owner, with_for_update=True)
            row = await db.get(UserOAuthSession, session_id, with_for_update=True)
            if row.status != "exchanging" or row.expires_at <= utcnow():
                raise ValueError("OAuth login was cancelled")
            credential = await db.scalar(select(UserModelCredential).where(UserModelCredential.user_id == owner,
                UserModelCredential.provider_id == PROVIDER_ID).with_for_update())
            scope = hashlib.sha256((PROVIDER_ID + ":" + client_id + ":" + token["identity"]["sub"]).encode()).hexdigest()
            if credential is None:
                credential = UserModelCredential(user_id=owner, provider_id=PROVIDER_ID, version=1)
                db.add(credential)
            else:
                credential.version += 1
                if credential.oauth_scope != scope:
                    credential.id = new_id()
            credential.auth_method, credential.base_url, credential.oauth_scope = "oauth", BASE_URL, scope
            credential.encrypted_key = encrypt_key(json.dumps(token))
            credential.refresh_owner = credential.refresh_until = None
            row.status, row.encrypted_data = "completed", encrypt_key("{}")
        return {"providerId": PROVIDER_ID, "auth": auth_json(credential)}
    except (asyncio.CancelledError, Exception) as exc:
        with anyio.CancelScope(shield=True):
            async with async_session_maker() as db, db.begin():
                await db.execute(update(UserOAuthSession).where(UserOAuthSession.id == session_id,
                    UserOAuthSession.status == "exchanging").values(status="failed", encrypted_data=encrypt_key("{}")))
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(400, "订阅授权未完成，请确认已同意使用 ChatGPT 计划后重新登录") from None


@router.post("/{session_id}/complete")
async def complete(session_id: str, body: FinishRequest, user: User = Depends(current_user)):
    async with async_session_maker() as db:
        row = await owned_session(db, session_id, user.id)
        pending = json.loads(decrypt_key(row.encrypted_data))
    actual, expected = urlsplit(body.callback_url.strip()), urlsplit(pending.get("redirect_uri", ""))
    if not expected.netloc or (actual.scheme, actual.netloc, actual.path) != (expected.scheme, expected.netloc, expected.path):
        raise HTTPException(400, "请粘贴本次授权的完整回调地址")
    values = parse_qs(actual.query, keep_blank_values=True)
    if any(len(value) != 1 for value in values.values()):
        raise HTTPException(400, "授权回调参数重复")
    return await finish_authorization(session_id, {key: value[0] for key, value in values.items()}, user_id=user.id)


@router.get("/callback", response_class=HTMLResponse)
async def callback(request: Request):
    raw_query = request.scope["query_string"]
    request.scope["query_string"] = b""
    values = parse_qs(raw_query.decode(), keep_blank_values=True)
    if any(len(value) != 1 for value in values.values()):
        raise HTTPException(400, "授权回调参数重复")
    query = {key: value[0] for key, value in values.items()}
    async with async_session_maker() as db:
        row = await db.scalar(select(UserOAuthSession).where(UserOAuthSession.state_hash == hashlib.sha256(query.get("state", "").encode()).hexdigest()))
    if not row:
        raise HTTPException(400, "授权会话无效")
    await finish_authorization(row.id, query)
    return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>订阅授权完成</title><p>ChatGPT 已连接，请返回 AI 设置页面选择模型并保存。</p></html>',
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"})


@router.get("/{session_id}")
async def status(session_id: str, user: User = Depends(current_user)):
    async with async_session_maker() as db:
        row = await owned_session(db, session_id, user.id)
        value = "expired" if row.status in {"pending", "exchanging"} and row.expires_at <= utcnow() else row.status
        credential = await db.scalar(select(UserModelCredential).where(UserModelCredential.user_id == user.id,
            UserModelCredential.provider_id == PROVIDER_ID)) if value == "completed" else None
        if value == "completed" and not credential:
            value = "cancelled"
        return {"status": value, "providerId": PROVIDER_ID,
                **({"auth": auth_json(credential)} if value == "completed" else {})}


@router.delete("/{session_id}", status_code=204)
async def cancel(session_id: str, user: User = Depends(current_user)):
    async with async_session_maker() as db, db.begin():
        await owned_session(db, session_id, user.id)
        await db.execute(update(UserOAuthSession).where(UserOAuthSession.id == session_id,
            UserOAuthSession.status.in_(["pending", "exchanging"])).values(status="cancelled", encrypted_data=encrypt_key("{}")))


async def access_token(config, sessions):
    """Refresh under a DB lease; no network call holds a database transaction."""
    owner = new_id()
    deadline = time.monotonic() + 20
    while True:
        async with sessions() as db, db.begin():
            row = await db.get(UserModelCredential, config["user_provider_id"])
            if (not row or row.user_id != config["credential_owner_id"] or row.auth_method != "oauth"
                    or row.oauth_scope != config["oauth_scope"]):
                raise unavailable()
            token = json.loads(decrypt_key(row.encrypted_key))
            if DIRECT_SCOPE not in token.get("scopes", []):
                raise unavailable()
            if token["expires_at"] > time.time() + 60:
                return token["access_token"]
            claimed = await db.execute(update(UserModelCredential).where(UserModelCredential.id == row.id,
                UserModelCredential.version == row.version,
                (UserModelCredential.refresh_until.is_(None)) | (UserModelCredential.refresh_until < utcnow()))
                .values(refresh_owner=owner, refresh_until=utcnow() + timedelta(seconds=60)))
            credential_id, version = row.id, row.version
        if claimed.rowcount:
            break
        if time.monotonic() >= deadline:
            raise PlatformError("AI_OVERLOADED", "订阅凭据正在刷新，请稍后重试", status=503)
        await asyncio.sleep(.1)
    try:
        async with oauth_http_client() as client:
            response = await client.post(TOKEN_URL, data={"grant_type": "refresh_token", "client_id": token["client_id"],
                "refresh_token": token["refresh_token"], "resource": BASE_URL})
            response.raise_for_status()
            renewed = await token_data(response.json(), token["client_id"], client, previous=token)
        if DIRECT_SCOPE not in renewed["scopes"]:
            raise unavailable()
        async with sessions() as db, db.begin():
            saved = await db.execute(update(UserModelCredential).where(UserModelCredential.id == credential_id,
                UserModelCredential.version == version, UserModelCredential.refresh_owner == owner)
                .values(encrypted_key=encrypt_key(json.dumps(renewed)), version=version + 1, refresh_owner=None, refresh_until=None))
            if not saved.rowcount:
                raise unavailable()
        return renewed["access_token"]
    except Exception:
        raise unavailable() from None
    finally:
        with anyio.CancelScope(shield=True), contextlib.suppress(Exception):
            async with sessions() as db, db.begin():
                await db.execute(update(UserModelCredential).where(UserModelCredential.id == credential_id,
                    UserModelCredential.refresh_owner == owner).values(refresh_owner=None, refresh_until=None))
