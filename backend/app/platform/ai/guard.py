"""Finite local queues, process scoped circuits, PostgreSQL permits and quotas."""

import asyncio
import hashlib
import hmac
import time
import weakref
from collections import deque
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy import delete, func, or_, select, text, update

from app.core.config import settings
from app.platform.contracts import PlatformError, trace_id
from app.platform.models import Agent, AiCall, AiPermit, Provider, new_id, utcnow

_gates = weakref.WeakKeyDictionary()
_circuits = {}


def workload(config):
    return "vision" if config["scene"] == "vision" else "background" if config["scene"] in {"resume", "report"} else "interactive"


def capacity(kind):
    return {"interactive": settings.AI_MAX_CONCURRENCY, "background": settings.AI_BACKGROUND_CONCURRENCY,
            "vision": settings.AI_VISION_CONCURRENCY, "tool": settings.AI_TOOL_CONCURRENCY,
            "parse": settings.PARSER_PROCESSES, "media": settings.MEDIA_MAX_CONNECTIONS}[kind]


class Gate:
    def __init__(self, limit, kind="other"):
        self.limit, self.active, self.waiters = limit, 0, 0
        self.kind = kind
        self.changed = asyncio.Condition()

    async def acquire(self, deadline):
        async with self.changed:
            if self.active >= self.limit:
                if self.waiters >= settings.AI_MAX_WAITERS:
                    raise PlatformError("AI_OVERLOADED", "AI 队列已满，本次未发送，请稍后重试")
                self.waiters += 1
                try:
                    await asyncio.wait_for(self.changed.wait_for(lambda: self.active < self.limit),
                                           max(0, min(settings.AI_WAIT_SECONDS, deadline - time.monotonic())))
                except TimeoutError as exc:
                    raise PlatformError("AI_OVERLOADED", "等待 AI 超时，本次未发送，请稍后重试") from exc
                finally:
                    self.waiters -= 1
                    if self.active < self.limit:
                        self.changed.notify(1)
            self.active += 1

    @asynccontextmanager
    async def enter(self, deadline):
        try:
            await self.acquire(deadline)
        except PlatformError:
            from app.platform.operations.telemetry import record
            await record("rejected", workload=self.kind)
            raise
        try:
            yield
        finally:
            async with self.changed:
                self.active -= 1
                self.changed.notify(1)


def gate(kind):
    gates = _gates.setdefault(asyncio.get_running_loop(), {})
    key = (kind, capacity(kind))
    return gates.setdefault(key, Gate(capacity(kind), kind))


class Circuit:
    def __init__(self):
        self.results = deque(maxlen=20)
        self.open_until = 0
        self.probing = False

    def enter(self):
        if self.open_until:
            if time.monotonic() < self.open_until or self.probing:
                raise PlatformError("AI_CIRCUIT_OPEN", "模型供应商熔断中，本次未发送，请稍后重试")
            self.probing = True

    def finish(self, failure: bool | None):
        if failure is None:
            self.probing = False
            return
        if self.probing:
            self.probing = False
            if not failure:
                self.results.clear()
                self.open_until = 0
                return
            self.open_until = time.monotonic() + settings.AI_CIRCUIT_OPEN_SECONDS
        self.results.append(failure)
        if len(self.results) >= settings.AI_CIRCUIT_MIN_CALLS and sum(self.results) / len(self.results) >= .5:
            self.open_until = time.monotonic() + settings.AI_CIRCUIT_OPEN_SECONDS


def credential_scope(config):
    from app.platform.admin import decrypt_key

    try:
        secret = decrypt_key(config.get("encrypted_key"))
    except Exception as exc:
        raise PlatformError("AI_CONFIGURATION_ERROR", "模型凭据不可解密，请管理员处理", retry_policy="never") from exc
    if not secret:
        raise PlatformError("AI_CONFIGURATION_ERROR", "尚未配置模型密钥，请由管理员配置", retry_policy="never")
    return hmac.new(settings.SECRET_KEY.encode(), secret.encode(), hashlib.sha256).hexdigest()


def circuit(config):
    # Bound process memory even if administrators create arbitrary configurations.
    if len(_circuits) >= 256:
        for key, item in list(_circuits.items()):
            if not item.open_until and not item.probing:
                del _circuits[key]
                break
        else:
            raise PlatformError("AI_OVERLOADED", "模型配置容量已满，本次未发送")
    credentials = credential_scope(config) if config.get("encrypted_key") else "unconfigured"
    key = (config.get("base_url"), config.get("model"), credentials, config.get("provider_version"))
    return _circuits.setdefault(key, Circuit())


async def database_now(db):
    if db.bind.dialect.name == "postgresql":
        return (await db.scalar(select(func.clock_timestamp()))).replace(tzinfo=None)
    return utcnow()


async def lock_scope(db, key):
    if db.bind.dialect.name == "postgresql":
        number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": number})


async def reserve(sessions, config, context):
    if context.get("fence"):
        await context["fence"]()
    scope, kind = credential_scope(config), workload(config)
    call_id = new_id()
    async with sessions() as db, db.begin():
        # Live emergency policy is checked even for frozen interview configuration.
        if config.get("provider_id"):
            provider = await db.get(Provider, config["provider_id"])
            if provider is None or not provider.enabled:
                raise PlatformError("AI_CONFIGURATION_ERROR", "此供应商已停用", retry_policy="never")
        if config.get("agent_id"):
            agent = await db.get(Agent, config["agent_id"])
            if agent is None or not agent.enabled:
                raise PlatformError("AI_CONFIGURATION_ERROR", "此智能体已停用", retry_policy="never")
        await lock_scope(db, "credential:" + scope)
        now = await database_now(db)
        await db.execute(delete(AiPermit).where(AiPermit.credential_scope == scope, AiPermit.lease_until <= now))
        permits = list((await db.scalars(select(AiPermit).where(AiPermit.credential_scope == scope))).all())
        unknown = await db.scalar(select(func.count()).select_from(AiCall).where(
            AiCall.credential_scope == scope, AiCall.outcome == "unknown", AiCall.created_at > now - timedelta(minutes=5),
            or_(AiCall.status != "running", AiCall.lease_until <= now)))
        if unknown >= settings.AI_UNKNOWN_LIMIT:
            raise PlatformError("AI_RESULT_UNKNOWN", "近期未知调用过多，暂停新尝试，请管理员核查", outcome="not_sent")
        if len(permits) >= settings.AI_PROVIDER_CONCURRENCY or sum(p.workload == kind for p in permits) >= capacity(kind):
            raise PlatformError("AI_OVERLOADED", "模型共享容量已满，本次未发送")
        background_budget = max(0, settings.AI_PROVIDER_CONCURRENCY - settings.AI_INTERACTIVE_RESERVED_PERMITS)
        if kind != "interactive" and sum(p.workload != "interactive" for p in permits) >= background_budget:
            raise PlatformError("AI_OVERLOADED", "后台容量已满，保留交互面试容量，本次未发送")
        user_id = context.get("user_id")
        budget = config.get("_input_budget", 0) + config.get("_output_budget", settings.AI_MAX_OUTPUT_TOKENS)
        if user_id:
            await lock_scope(db, "quota:" + user_id)
            count = await db.scalar(select(func.count()).select_from(AiCall).where(
                AiCall.user_id == user_id, AiCall.created_at > now - timedelta(minutes=1)))
            used = await db.scalar(select(func.coalesce(func.sum(AiCall.reserved_tokens), 0)).where(
                AiCall.user_id == user_id, AiCall.created_at > now - timedelta(days=1)))
            if count >= settings.RATE_LIMIT_PER_MINUTE or used + budget > settings.TOKEN_QUOTA_PER_USER:
                raise PlatformError("AI_QUOTA_EXCEEDED", "AI 调用频率或 Token 配额已用尽", status=429, retry_policy="manual")
        expires = now + timedelta(seconds=settings.AI_COMMAND_LEASE_SECONDS)
        db.add(AiPermit(id=call_id, credential_scope=scope, workload=kind, owner_token=call_id, lease_until=expires))
        db.add(AiCall(id=call_id, user_id=user_id, interview_id=context.get("interview_id"),
            scene=config["scene"], provider_id=config.get("provider_id"), model=config.get("model"), status="running",
            trace_id=trace_id(), flight_id=context.get("flight_id"), credential_scope=scope, workload=kind,
            reserved_tokens=budget, outcome="not_sent", lease_until=expires))
    return call_id


async def settle(sessions, call_id, started, status, usage=None, error=None):
    async with sessions() as db, db.begin():
        now = await database_now(db)
        permit = await db.scalar(select(AiPermit).where(AiPermit.id == call_id).with_for_update())
        row = await db.get(AiCall, call_id)
        if not row or not permit or permit.owner_token != call_id or permit.lease_until <= now:
            from app.platform.operations.telemetry import record

            await record("lease_lost", scene=row.scene if row else "other")
            raise PlatformError("AI_RESULT_UNKNOWN", "调用许可已失效，迟到结果未接受", outcome="unknown")
        row.status, row.duration_ms = status, int((time.monotonic() - started) * 1000)
        row.input_tokens = (usage or {}).get("prompt_tokens")
        row.output_tokens = (usage or {}).get("completion_tokens")
        row.error_code = error
        row.outcome = "unknown" if status in {"unknown", "interrupted"} else "not_sent" if status == "not_sent" else "known"
        if row.outcome == "not_sent":
            row.reserved_tokens = 0
        elif row.outcome == "known" and row.input_tokens is not None and row.output_tokens is not None:
            row.reserved_tokens = row.input_tokens + row.output_tokens
        await db.delete(permit)


async def mark_sent(sessions, call_id):
    async with sessions() as db, db.begin():
        now = await database_now(db)
        permit = await db.get(AiPermit, call_id)
        if not permit or permit.owner_token != call_id or permit.lease_until <= now:
            raise PlatformError("STATE_CONFLICT", "发送前调用许可失效", status=409)
        await db.execute(update(AiCall).where(AiCall.id == call_id, AiCall.status == "running").values(outcome="unknown"))


async def renew(sessions, call_id):
    async with sessions() as db, db.begin():
        now = await database_now(db)
        until = now + timedelta(seconds=settings.AI_COMMAND_LEASE_SECONDS)
        result = await db.execute(update(AiPermit).where(AiPermit.id == call_id, AiPermit.owner_token == call_id,
            AiPermit.lease_until > now).values(lease_until=until))
        if result.rowcount != 1:
            raise PlatformError("AI_RESULT_UNKNOWN", "调用许可失效", outcome="unknown")
        await db.execute(update(AiCall).where(AiCall.id == call_id, AiCall.status == "running").values(lease_until=until))
