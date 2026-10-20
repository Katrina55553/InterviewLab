"""Opaque cookie sessions, CSRF protection, and shared REST/WebSocket identity."""

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.redis import get_redis
from app.platform.models import AuthThrottle, LoginSession, User, utcnow

router = APIRouter(prefix="/auth", tags=["platform-auth"])
SESSION_COOKIE = "ra_session"
CSRF_COOKIE = "ra_csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[\w.@-]{3,64}", value):
            raise ValueError("用户名须为 3–64 个字母、数字、汉字或 . @ _ -")
        return value


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=64)
    return (
        "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt" or (n, r, p) != ("16384", "8", "1"):
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.urlsafe_b64decode(salt),
            n=16384,
            r=8,
            p=1,
            dklen=64,
        )
        return hmac.compare_digest(actual, base64.urlsafe_b64decode(expected))
    except (ValueError, TypeError):
        return False


def user_json(user: User) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def allowed_origins() -> set[str]:
    origins = settings.CORS_ORIGINS
    if isinstance(origins, str):
        origins = origins.split(",")
    return {origin.strip().rstrip("/").lower() for origin in origins if origin.strip() and origin.strip() != "*"}


def check_origin(request: Request | WebSocket, *, required: bool = False) -> None:
    origin = request.headers.get("origin", "").strip().rstrip("/").lower()
    if (required and not origin) or (origin and origin not in allowed_origins()):
        raise HTTPException(status_code=403, detail="请求来源不受信任")
    if not origin and request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="请求来源不受信任")


async def _resolve_identity(request: Request | WebSocket) -> tuple[User, LoginSession]:
    cached = getattr(request.state, "platform_identity", None)
    if cached is not None:
        return cached
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 256:
        raise HTTPException(status_code=401, detail="请先登录")
    async with async_session_maker() as db:
        row = (
            await db.execute(
                select(User, LoginSession)
                .join(LoginSession, LoginSession.user_id == User.id)
                .where(LoginSession.id == _token_hash(token), LoginSession.expires_at > utcnow())
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
        identity = (row[0], row[1])
    request.state.platform_identity = identity
    return identity


async def current_user(request: Request) -> User:
    user, _ = await _resolve_identity(request)
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="此操作需要管理员权限")
    return user


async def websocket_user(websocket: WebSocket) -> User:
    try:
        check_origin(websocket, required=True)
        user, _ = await _resolve_identity(websocket)
        return user
    except HTTPException as exc:
        raise WebSocketException(code=1008, reason=exc.detail) from exc


async def check_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    check_origin(request)
    _, login = await _resolve_identity(request)
    header = request.headers.get("X-CSRF-Token", "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if (
        not header
        or not cookie
        or not hmac.compare_digest(header.encode(), cookie.encode())
        or not hmac.compare_digest(header.encode(), login.csrf_token.encode())
    ):
        raise HTTPException(status_code=403, detail="CSRF 校验失败，请刷新页面后重试")


class AuthCSRFMiddleware:
    """Protect all cookie-authenticated mutations without buffering SSE bodies."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            request = Request(scope)
            public_auth = request.url.path.endswith(("/auth/login", "/auth/register"))
            if request.cookies.get(SESSION_COOKIE) and not public_auth:
                try:
                    await check_csrf(request)
                except HTTPException as exc:
                    from app.platform.contracts import error_payload
                    await JSONResponse({**error_payload(exc), "detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)(
                        scope, receive, send
                    )
                    return
        await self.app(scope, receive, send)


_THROTTLE_LUA = """
local counts = {}
for i, key in ipairs(KEYS) do
    counts[i] = redis.call('INCR', key)
    if counts[i] == 1 then redis.call('EXPIRE', key, ARGV[1]) end
end
return counts
"""


async def _throttle(request: Request, username: str) -> None:
    """Atomic across workers; PostgreSQL takes over when Redis is unavailable."""
    window = settings.AUTH_RATE_LIMIT_WINDOW_SECONDS
    bucket = int(time.time()) // window
    ip = request.client.host if request.client else "unknown"
    identities = (f"ip:{ip}", f"account:{username}")
    keys = [f"platform:auth:{bucket}:{_token_hash(identity)}" for identity in identities]
    try:
        counts = await get_redis().eval(_THROTTLE_LUA, len(keys), *keys, window * 2)
    except Exception:
        counts = []
        async with async_session_maker() as db:
            dialect = db.bind.dialect.name
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                insert = sqlite_insert
            else:
                insert = pg_insert
            for key in keys:
                statement = insert(AuthThrottle).values(
                    id=key, attempts=1, expires_at=utcnow() + timedelta(seconds=window * 2)
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[AuthThrottle.id],
                    set_={"attempts": AuthThrottle.attempts + 1},
                ).returning(AuthThrottle.attempts)
                counts.append((await db.execute(statement)).scalar_one())
            await db.execute(delete(AuthThrottle).where(AuthThrottle.expires_at < utcnow()))
            await db.commit()
    if int(counts[0]) > settings.AUTH_IP_MAX_ATTEMPTS or int(counts[1]) > settings.AUTH_MAX_ATTEMPTS:
        retry_after = window - int(time.time()) % window
        raise HTTPException(
            status_code=429, detail="尝试次数过多，请稍后再试", headers={"Retry-After": str(retry_after)}
        )


async def _create_login(db, user: User) -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            id=_token_hash(token),
            user_id=user.id,
            csrf_token=csrf,
            expires_at=utcnow() + timedelta(hours=settings.AUTH_SESSION_HOURS),
        )
    )
    return token, csrf


def _set_cookies(response: Response, token: str, csrf: str) -> None:
    secure = settings.AUTH_COOKIE_SECURE or settings.is_production
    options = {"path": "/", "secure": secure, "samesite": "lax", "max_age": settings.AUTH_SESSION_HOURS * 3600}
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **options)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **options)


@router.post("/register", status_code=201)
async def register(body: Credentials, request: Request, response: Response) -> dict:
    check_origin(request)
    await _throttle(request, body.username)
    password_hash = await asyncio.to_thread(hash_password, body.password)
    async with async_session_maker() as db:
        user = User(username=body.username, password_hash=password_hash, role="user")
        db.add(user)
        try:
            await db.flush()
            token, csrf = await _create_login(db, user)
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(status_code=409, detail="用户名已存在") from exc
    _set_cookies(response, token, csrf)
    return user_json(user)


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response) -> dict:
    check_origin(request)
    await _throttle(request, body.username)
    async with async_session_maker() as db:
        user = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    # Equal-cost password work for unknown usernames avoids a timing oracle.
    encoded = (
        user.password_hash
        if user
        else "scrypt$16384$8$1$MDAwMDAwMDAwMDAwMDAwMA==$" + base64.urlsafe_b64encode(bytes(64)).decode()
    )
    valid = await asyncio.to_thread(verify_password, body.password, encoded)
    if user is None or not valid:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    async with async_session_maker() as db:
        old_token = request.cookies.get(SESSION_COOKIE)
        if old_token:
            await db.execute(delete(LoginSession).where(LoginSession.id == _token_hash(old_token)))
        token, csrf = await _create_login(db, user)
        await db.commit()
    _set_cookies(response, token, csrf)
    return user_json(user)


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return user_json(user)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, _user: User = Depends(current_user)) -> None:
    await check_csrf(request)
    async with async_session_maker() as db:
        await db.execute(delete(LoginSession).where(LoginSession.id == _token_hash(request.cookies[SESSION_COOKIE])))
        await db.commit()
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=settings.AUTH_COOKIE_SECURE or settings.is_production,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        CSRF_COOKIE, path="/", secure=settings.AUTH_COOKIE_SECURE or settings.is_production, samesite="lax"
    )
