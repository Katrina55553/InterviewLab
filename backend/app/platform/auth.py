"""Opaque cookie sessions, CSRF protection, and shared REST/WebSocket identity."""

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, WebSocket, WebSocketException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.redis import get_redis
from app.platform import mailer
from app.platform.models import AuthThrottle, EmailChallenge, LoginSession, User, new_id, utcnow

router = APIRouter(prefix="/auth", tags=["platform-auth"])
SESSION_COOKIE = "ra_session"
CSRF_COOKIE = "ra_csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
CODE_LIFETIME_SECONDS = 600
CODE_RESEND_SECONDS = 60
CODE_MAX_ATTEMPTS = 5
CODE_MESSAGE = "如邮箱符合条件，验证码将发送至该邮箱，请检查收件箱和垃圾邮件。"
DUMMY_PASSWORD_HASH = "scrypt$16384$8$1$MDAwMDAwMDAwMDAwMDAwMA==$" + base64.urlsafe_b64encode(bytes(64)).decode()


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


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not value.isascii() or value.count("@") != 1:
        raise ValueError("请输入有效的邮箱地址")
    local, domain = value.split("@")
    if (not re.fullmatch(r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}", local)
            or local.startswith(".") or local.endswith(".") or ".." in local
            or len(domain) > 253 or "." not in domain
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                   for label in domain.split("."))):
        raise ValueError("请输入有效的邮箱地址")
    return value


class EmailAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    _email = field_validator("email")(normalize_email)


class EmailCodeRequest(EmailAddress):
    purpose: Literal["register", "reset"]


class Registration(Credentials, EmailAddress):
    code: str = Field(pattern=r"^[0-9]{6}$")

    @field_validator("username")
    @classmethod
    def no_email_username(cls, value: str) -> str:
        if "@" in value:
            raise ValueError("用户名不能包含 @，邮箱请填写在邮箱栏")
        return value


class LoginCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_login(cls, value: str) -> str:
        value = value.strip().lower()
        if len(value) < 3 or any(character.isspace() for character in value):
            raise ValueError("请输入用户名或邮箱")
        return value


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class PasswordReset(EmailAddress):
    code: str = Field(pattern=r"^[0-9]{6}$")
    new_password: str = Field(min_length=8, max_length=128)


class EmailBinding(EmailAddress):
    current_password: str = Field(min_length=8, max_length=128)


class EmailBindingConfirm(EmailBinding):
    code: str = Field(pattern=r"^[0-9]{6}$")


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
            public_auth = request.url.path.rstrip("/").endswith((
                "/auth/login", "/auth/register", "/auth/email/code", "/auth/password/reset",
            ))
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


async def _throttle(request: Request, username: str, *, namespace: str = "auth") -> None:
    """Atomic across workers; PostgreSQL takes over when Redis is unavailable."""
    window = settings.AUTH_RATE_LIMIT_WINDOW_SECONDS
    bucket = int(time.time()) // window
    ip = request.client.host if request.client else "unknown"
    identities = (f"ip:{ip}", f"account:{username}")
    keys = [f"platform:{namespace}:{bucket}:{_token_hash(identity)}" for identity in identities]
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


def _clear_cookies(response: Response) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name, path="/", secure=settings.AUTH_COOKIE_SECURE or settings.is_production,
            httponly=name == SESSION_COOKIE, samesite="lax",
        )


def _challenge_id(email: str, purpose: str, user_id: str | None = None) -> str:
    # A reset challenge binds to its account through user_id in the row, but its
    # mailbox scope stays identical for existing and unknown email addresses.
    owner = user_id if purpose == "bind" else ""
    return _token_hash(f"{purpose}:{email}:{owner}")


def _code_hash(challenge_id: str, generation: str, code: str) -> str:
    # A six-digit code cannot be protected from offline guessing by an unkeyed hash.
    return hmac.new(
        settings.SECRET_KEY.encode(), f"email-code:{challenge_id}:{generation}:{code}".encode(), hashlib.sha256,
    ).hexdigest()


def _code_response() -> dict:
    return {"message": CODE_MESSAGE, "expires_in": CODE_LIFETIME_SECONDS, "resend_after": CODE_RESEND_SECONDS}


async def _issue_code(email: str, purpose: str, user_id: str | None, *, eligible: bool = True,
                      background_tasks: BackgroundTasks | None = None) -> None:
    if not mailer.smtp_configured():
        raise HTTPException(status_code=503, detail="邮件服务尚未配置，请联系管理员")
    challenge_id = _challenge_id(email, purpose, user_id)
    generation = new_id()
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = utcnow()
    values = {
        "id": challenge_id, "email": email, "purpose": purpose, "user_id": user_id,
        "generation": generation, "code_hash": _code_hash(challenge_id, generation, code),
        "status": "pending", "attempts": 0, "expires_at": now + timedelta(seconds=CODE_LIFETIME_SECONDS),
        "resend_at": now + timedelta(seconds=CODE_RESEND_SECONDS),
    }
    async with async_session_maker() as db:
        if db.bind.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            insert = pg_insert
        statement = insert(EmailChallenge).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[EmailChallenge.id], set_=values, where=EmailChallenge.resend_at <= now,
        ).returning(EmailChallenge.id)
        if (await db.execute(statement)).scalar_one_or_none() is None:
            next_send = await db.scalar(select(EmailChallenge.resend_at).where(EmailChallenge.id == challenge_id))
            raise HTTPException(
                status_code=429, detail="验证码发送过于频繁，请稍后重试",
                headers={"Retry-After": str(max(1, int((next_send - now).total_seconds()) + 1))},
            )
        await db.execute(delete(EmailChallenge).where(EmailChallenge.expires_at < now - timedelta(days=1)))
        await db.commit()
    if not eligible:
        return
    if background_tasks is not None:
        # The reset response must finish before SMTP can reveal mailbox eligibility
        # through its network latency. FastAPI supervises this response-bound task.
        background_tasks.add_task(_deliver_code, email, purpose, challenge_id, generation, code)
    else:
        await _deliver_code(email, purpose, challenge_id, generation, code)


async def _deliver_code(email: str, purpose: str, challenge_id: str, generation: str, code: str) -> None:
    try:
        await mailer.send_verification_code(email, code, purpose)
    except mailer.MailDeliveryError as exc:
        # Keep an unusable pending record through the normal resend cooldown.
        # Removing it would reveal known reset accounts through retry behavior
        # and let delivery failures bypass the mailbox send rate limit.
        # Keep the public reset response indistinguishable for absent mailboxes.
        if purpose != "reset":
            raise HTTPException(status_code=503, detail="验证码邮件发送失败，请稍后重试") from exc
        return
    async with async_session_maker() as db:
        await db.execute(update(EmailChallenge).where(
            EmailChallenge.id == challenge_id, EmailChallenge.generation == generation,
            EmailChallenge.status == "pending",
        ).values(status="sent"))
        await db.commit()


async def _consume_code(db, email: str, purpose: str, code: str, user_id: str | None = None) -> None:
    challenge_id = _challenge_id(email, purpose, user_id)
    row = await db.get(EmailChallenge, challenge_id)
    if row is not None:
        consumed = await db.scalar(update(EmailChallenge).where(
            EmailChallenge.id == challenge_id, EmailChallenge.generation == row.generation,
            EmailChallenge.user_id == user_id,
            EmailChallenge.code_hash == _code_hash(challenge_id, row.generation, code),
            EmailChallenge.status == "sent", EmailChallenge.attempts < CODE_MAX_ATTEMPTS,
            EmailChallenge.expires_at > utcnow(),
        ).values(status="consumed", code_hash="0" * 64).returning(EmailChallenge.id))
        if consumed is not None:
            return
        await db.execute(update(EmailChallenge).where(
            EmailChallenge.id == challenge_id, EmailChallenge.generation == row.generation,
            EmailChallenge.status == "sent", EmailChallenge.attempts < CODE_MAX_ATTEMPTS,
            EmailChallenge.expires_at > utcnow(),
        ).values(attempts=EmailChallenge.attempts + 1))
    # Invalid attempts must persist even though the HTTP operation fails.
    await db.commit()
    raise HTTPException(status_code=400, detail="验证码无效或已过期，请重新获取")


async def _password_user(db, request: Request, user: User, password: str) -> User:
    # A no-op write acquires the user lock on PostgreSQL and SQLite alike. All
    # password changes and session minting serialize through this same record.
    fresh = await db.scalar(update(User).where(User.id == user.id)
                            .values(password_hash=User.password_hash).returning(User))
    session_id = _token_hash(request.cookies.get(SESSION_COOKIE, ""))
    login = await db.scalar(select(LoginSession.id).where(
        LoginSession.id == session_id, LoginSession.user_id == user.id, LoginSession.expires_at > utcnow(),
    ))
    if fresh is None or login is None:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    if not await asyncio.to_thread(verify_password, password, fresh.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    return fresh


async def _revoke_credentials(db, user: User, password_hash: str) -> None:
    user.password_hash = password_hash
    await db.flush()
    await db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    await db.execute(delete(EmailChallenge).where(EmailChallenge.user_id == user.id))


@router.get("/capabilities")
async def capabilities(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"email_enabled": mailer.smtp_configured()}


@router.post("/email/code", status_code=202)
async def email_code(body: EmailCodeRequest, request: Request, response: Response,
                     background_tasks: BackgroundTasks) -> dict:
    check_origin(request)
    await _throttle(request, body.email, namespace="email-send")
    async with async_session_maker() as db:
        account = await db.scalar(select(User).where(User.email == body.email))
        legacy = await db.scalar(select(User.id).where(User.username == body.email))
    eligible = account is not None if body.purpose == "reset" else account is None and legacy is None
    await _issue_code(body.email, body.purpose, account.id if account and body.purpose == "reset" else None,
                      eligible=eligible, background_tasks=background_tasks if body.purpose == "reset" else None)
    response.headers["Cache-Control"] = "no-store"
    return _code_response()


@router.post("/register", status_code=201)
async def register(body: Registration, request: Request, response: Response) -> dict:
    check_origin(request)
    await _throttle(request, body.email, namespace="email-verify")
    password_hash = await asyncio.to_thread(hash_password, body.password)
    async with async_session_maker() as db:
        if await db.scalar(select(User.id).where(or_(User.username == body.username, User.email == body.email,
                                                      User.username == body.email))):
            raise HTTPException(status_code=409, detail="用户名或邮箱已被使用")
        await _consume_code(db, body.email, "register", body.code)
        user = User(username=body.username, email=body.email, password_hash=password_hash, role="user")
        db.add(user)
        try:
            await db.flush()
            token, csrf = await _create_login(db, user)
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(status_code=409, detail="用户名或邮箱已被使用") from exc
    response.headers["Cache-Control"] = "no-store"
    _set_cookies(response, token, csrf)
    return user_json(user)


@router.post("/login")
async def login(body: LoginCredentials, request: Request, response: Response) -> dict:
    check_origin(request)
    async with async_session_maker() as db:
        matches = list((await db.scalars(select(User).where(or_(User.username == body.username,
                                                                User.email == body.username)))).all())
    # Ambiguous legacy email-shaped usernames never select an arbitrary account.
    user = matches[0] if len(matches) == 1 else None
    await _throttle(request, user.id if user else body.username)
    encoded = user.password_hash if user else DUMMY_PASSWORD_HASH
    valid = await asyncio.to_thread(verify_password, body.password, encoded)
    if user is None or not valid:
        raise HTTPException(status_code=401, detail="用户名、邮箱或密码错误")
    async with async_session_maker() as db:
        locked = await db.scalar(update(User).where(User.id == user.id, User.password_hash == encoded)
                                 .values(password_hash=encoded).returning(User.id))
        if locked is None:
            raise HTTPException(status_code=401, detail="密码已变更，请重新登录")
        old_token = request.cookies.get(SESSION_COOKIE)
        if old_token:
            await db.execute(delete(LoginSession).where(LoginSession.id == _token_hash(old_token)))
        token, csrf = await _create_login(db, user)
        await db.commit()
    response.headers["Cache-Control"] = "no-store"
    _set_cookies(response, token, csrf)
    return user_json(user)


@router.get("/security")
async def security(response: Response, user: User = Depends(current_user)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"email": user.email}


@router.post("/email/bind/code", status_code=202)
async def binding_code(body: EmailBinding, request: Request, response: Response,
                       user: User = Depends(current_user)) -> dict:
    await check_csrf(request)
    await _throttle(request, user.id, namespace="email-send")
    async with async_session_maker() as db:
        fresh = await _password_user(db, request, user, body.current_password)
        if fresh.email is not None:
            raise HTTPException(status_code=409, detail="账号已绑定邮箱")
        taken = await db.scalar(select(User.id).where(or_(User.email == body.email, User.username == body.email)))
        if taken is not None and taken != user.id:
            raise HTTPException(status_code=409, detail="该邮箱不可绑定，请使用其他邮箱")
        await db.commit()
    await _issue_code(body.email, "bind", user.id)
    response.headers["Cache-Control"] = "no-store"
    return _code_response()


@router.post("/email/bind", status_code=204)
async def binding_confirm(body: EmailBindingConfirm, request: Request, response: Response,
                          user: User = Depends(current_user)) -> None:
    await check_csrf(request)
    await _throttle(request, user.id, namespace="email-verify")
    async with async_session_maker() as db:
        fresh = await _password_user(db, request, user, body.current_password)
        if fresh.email is not None:
            raise HTTPException(status_code=409, detail="账号已绑定邮箱")
        taken = await db.scalar(select(User.id).where(or_(User.email == body.email, User.username == body.email)))
        if taken is not None and taken != user.id:
            raise HTTPException(status_code=409, detail="该邮箱不可绑定，请使用其他邮箱")
        await _consume_code(db, body.email, "bind", body.code, user.id)
        fresh.email = body.email
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(status_code=409, detail="该邮箱不可绑定，请使用其他邮箱") from exc
    response.headers["Cache-Control"] = "no-store"


@router.post("/password/change", status_code=204)
async def change_password(body: PasswordChange, request: Request, response: Response,
                          user: User = Depends(current_user)) -> None:
    await check_csrf(request)
    await _throttle(request, user.id, namespace="password-change")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    encoded = await asyncio.to_thread(hash_password, body.new_password)
    async with async_session_maker() as db:
        fresh = await _password_user(db, request, user, body.current_password)
        await _revoke_credentials(db, fresh, encoded)
        await db.commit()
    response.headers["Cache-Control"] = "no-store"
    _clear_cookies(response)


@router.post("/password/reset", status_code=204)
async def reset_password(body: PasswordReset, request: Request, response: Response) -> None:
    check_origin(request)
    await _throttle(request, body.email, namespace="email-verify")
    encoded = await asyncio.to_thread(hash_password, body.new_password)
    async with async_session_maker() as db:
        # Acquire the user lock before the challenge lock, matching bind/change.
        user = await db.scalar(update(User).where(User.email == body.email)
                               .values(password_hash=User.password_hash).returning(User))
        await _consume_code(db, body.email, "reset", body.code, user.id if user else None)
        if user is None:
            raise HTTPException(status_code=400, detail="验证码无效或已过期，请重新获取")
        await _revoke_credentials(db, user, encoded)
        await db.commit()
    response.headers["Cache-Control"] = "no-store"
    _clear_cookies(response)


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
