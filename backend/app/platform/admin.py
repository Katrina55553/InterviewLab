"""Versioned provider/Agent configuration with encrypted provider credentials."""

import base64
import hashlib
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import async_session_maker
from app.platform.auth import require_admin
from app.platform.models import Agent, AiCall, AiPermit, ConfigRevision, Outbox, Provider, User, new_id

router = APIRouter(prefix="/admin", tags=["platform-admin"], dependencies=[Depends(require_admin)])
Scene = Literal["parse", "resume", "questions", "evaluate", "followup", "report", "chat", "vision"]
SCENES = ("parse", "resume", "questions", "evaluate", "followup", "report", "chat", "vision")
AVAILABLE_TOOLS = frozenset({"lookup_resume_field", "verify_code_snippet"})


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()))


def encrypt_key(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_key(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="模型凭据不可解密，请管理员重新保存 API Key") from exc


def _validate_url(value: str | None) -> str | None:
    if value is None:
        return value
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("模型地址须为不含账号、查询参数或片段的 HTTP(S) URL")
    return value


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=4096)
    enabled: bool = True
    vision: bool = False

    @field_validator("name", "model")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("此字段不能为空")
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        return _validate_url(value)


class ProviderUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, min_length=1, max_length=2000)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = None
    vision: bool | None = None

    @field_validator("name", "model")
    @classmethod
    def not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("此字段不能为空")
        return value.strip() if value is not None else value

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str | None) -> str | None:
        return _validate_url(value)


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    scene: Scene | None = None
    provider_id: str | None = Field(default=None, max_length=36)
    prompt: str = Field(default="", max_length=30000)
    tools: list[str] = Field(default_factory=list, max_length=10)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("智能体名称不能为空")
        return value.strip()

    @field_validator("tools")
    @classmethod
    def valid_tools(cls, value: list[str]) -> list[str]:
        if not set(value) <= AVAILABLE_TOOLS:
            raise ValueError("包含尚未注册的工具")
        return list(dict.fromkeys(value))


class AgentUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    scene: Scene | None = None
    provider_id: str | None = Field(default=None, max_length=36)
    prompt: str | None = Field(default=None, max_length=30000)
    tools: list[str] | None = Field(default=None, max_length=10)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("智能体名称不能为空")
        return value.strip() if value is not None else value

    @field_validator("tools")
    @classmethod
    def valid_tools(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and not set(value) <= AVAILABLE_TOOLS:
            raise ValueError("包含尚未注册的工具")
        return list(dict.fromkeys(value)) if value is not None else value


def provider_json(provider: Provider) -> dict:
    return {
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "model": provider.model,
        "enabled": provider.enabled,
        "vision": provider.vision,
        "version": provider.version,
        "has_key": bool(provider.encrypted_key),
        "created_at": provider.created_at,
    }


def agent_json(agent: Agent) -> dict:
    return {
        "id": agent.id,
        "name": agent.name,
        "scene": agent.scene,
        "provider_id": agent.provider_id,
        "prompt": agent.prompt,
        "tools": [tool for tool in agent.tools if tool in AVAILABLE_TOOLS],
        "enabled": agent.enabled,
        "version": agent.version,
        "created_at": agent.created_at,
    }


def check_version(row, expected):
    if row.version != expected:
        raise HTTPException(409, "配置已被修改，请刷新后重试")


def record_revision(db, kind, row, actor_id, *, deleted=False):
    config = {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name != "created_at"}
    db.add(ConfigRevision(id=new_id(), kind=kind, entity_id=row.id, version=row.version,
                          actor_id=actor_id, config=config, deleted=deleted))


def safe_revision(config):
    return {**{k: v for k, v in config.items() if k != "encrypted_key"},
            **({"has_key": bool(config.get("encrypted_key"))} if "encrypted_key" in config else {})}


@router.get("/diagnostics")
async def diagnostics():
    """Authenticated finite aggregate data; no prompts, results, credentials or user IDs."""
    from app.platform.ai.guard import database_now
    from app.platform.operations.health import readiness

    health, _ = await readiness()
    async with async_session_maker() as db:
        now = await database_now(db)
        unknown = await db.scalar(select(func.count()).select_from(AiCall).where(AiCall.outcome == "unknown"))
        permits = await db.scalar(select(func.count()).select_from(AiPermit).where(AiPermit.lease_until > now))
        queued = await db.scalar(select(func.count()).select_from(Outbox).where(Outbox.execution_status == "queued"))
    return {"health": health, "unknown_calls": unknown, "active_permits": permits, "queued_jobs": queued}


@router.get("/revisions/{kind}/{entity_id}")
async def revisions(kind: Literal["provider", "agent"], entity_id: str,
                    offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)):
    async with async_session_maker() as db:
        rows = (await db.scalars(select(ConfigRevision).where(ConfigRevision.kind == kind,
            ConfigRevision.entity_id == entity_id).order_by(ConfigRevision.version.desc()).offset(offset).limit(limit + 1))).all()
        result = []
        for index, row in enumerate(rows[:limit]):
            previous = rows[index + 1].config if index + 1 < len(rows) else {}
            safe, old = safe_revision(row.config), safe_revision(previous)
            result.append({"id": row.id, "version": row.version, "actor_id": row.actor_id, "created_at": row.created_at,
                           "deleted": row.deleted, "config": safe,
                           "changes": {k: {"before": old.get(k), "after": v} for k, v in safe.items() if old.get(k) != v},
                           "key_changed": row.config.get("encrypted_key") != previous.get("encrypted_key")})
        return {"items": result, "has_more": len(rows) > limit}


class RestoreRevision(BaseModel):
    version: int = Field(ge=1)
    expected_version: int = Field(ge=1)


@router.post("/revisions/{kind}/{entity_id}/restore")
async def restore_revision(kind: Literal["provider", "agent"], entity_id: str, body: RestoreRevision,
                           user: User = Depends(require_admin)):
    from app.platform.ai.guard import lock_scope

    model = Provider if kind == "provider" else Agent
    async with async_session_maker() as db, db.begin():
        await lock_scope(db, "config:" + kind + ":" + entity_id)
        row = await db.scalar(select(model).where(model.id == entity_id).with_for_update())
        latest = await db.scalar(select(ConfigRevision).where(ConfigRevision.kind == kind,
            ConfigRevision.entity_id == entity_id).order_by(ConfigRevision.version.desc()).limit(1))
        old = await db.scalar(select(ConfigRevision).where(ConfigRevision.kind == kind,
            ConfigRevision.entity_id == entity_id, ConfigRevision.version == body.version))
        if not latest or not old:
            raise HTTPException(404, "配置修订不存在")
        if latest.version != body.expected_version:
            raise HTTPException(409, "配置已被修改，请刷新后重试")
        fields = {k: v for k, v in old.config.items() if k not in {"id", "version"}}
        if kind == "agent":
            await _check_provider(db, fields.get("provider_id"))
        if row is None:
            row = model(id=entity_id, **fields, version=latest.version + 1)
            db.add(row)
        else:
            for key, value in fields.items():
                setattr(row, key, value)
            row.version = latest.version + 1
        await db.flush()
        record_revision(db, kind, row, user.id)
        return provider_json(row) if kind == "provider" else agent_json(row)


@router.get("/providers")
async def list_providers() -> list[dict]:
    async with async_session_maker() as db:
        return [
            provider_json(item) for item in (await db.execute(select(Provider).order_by(Provider.created_at))).scalars()
        ]


@router.post("/providers", status_code=201)
async def create_provider(body: ProviderCreate, user: User = Depends(require_admin)) -> dict:
    fields = body.model_dump(exclude={"api_key"})
    provider = Provider(**fields, encrypted_key=encrypt_key(body.api_key.strip()) if body.api_key.strip() else None)
    async with async_session_maker() as db:
        db.add(provider)
        await db.flush()
        record_revision(db, "provider", provider, user.id)
        await db.commit()
    return provider_json(provider)


@router.get("/providers/{provider_id}")
async def get_provider(provider_id: str) -> dict:
    async with async_session_maker() as db:
        provider = await db.scalar(select(Provider).where(Provider.id == provider_id).with_for_update())
        if provider is None:
            raise HTTPException(status_code=404, detail="模型配置不存在")
        return provider_json(provider)


@router.patch("/providers/{provider_id}")
@router.put("/providers/{provider_id}")
async def update_provider(provider_id: str, body: ProviderUpdate, user: User = Depends(require_admin)) -> dict:
    async with async_session_maker() as db:
        provider = (
            await db.execute(select(Provider).where(Provider.id == provider_id).with_for_update())
        ).scalar_one_or_none()
        if provider is None:
            raise HTTPException(status_code=404, detail="模型配置不存在")
        check_version(provider, body.expected_version)
        fields = body.model_dump(exclude_unset=True, exclude_none=True, exclude={"api_key", "expected_version"})
        if body.api_key and body.api_key.strip():
            fields["encrypted_key"] = encrypt_key(body.api_key.strip())
        if fields:
            for key, value in fields.items():
                setattr(provider, key, value)
            provider.version += 1
            record_revision(db, "provider", provider, user.id)
        await db.commit()
        return provider_json(provider)


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: str, expected_version: int = Query(ge=1), user: User = Depends(require_admin)) -> Response:
    async with async_session_maker() as db:
        provider = await db.scalar(select(Provider).where(Provider.id == provider_id).with_for_update())
        if provider is None:
            raise HTTPException(status_code=404, detail="模型配置不存在")
        check_version(provider, expected_version)
        dependents = (await db.scalars(select(Agent).where(Agent.provider_id == provider_id).with_for_update())).all()
        for agent in dependents:
            agent.provider_id, agent.version = None, agent.version + 1
            record_revision(db, "agent", agent, user.id)
        provider.version += 1
        record_revision(db, "provider", provider, user.id, deleted=True)
        await db.delete(provider)
        await db.commit()
    return Response(status_code=204)


@router.get("/agents")
async def list_agents() -> list[dict]:
    async with async_session_maker() as db:
        return [agent_json(item) for item in (await db.execute(select(Agent).order_by(Agent.created_at))).scalars()]


async def _check_provider(db, provider_id: str | None) -> None:
    if provider_id is not None and await db.get(Provider, provider_id) is None:
        raise HTTPException(status_code=422, detail="所选模型配置不存在")


@router.post("/agents", status_code=201)
async def create_agent(body: AgentCreate, user: User = Depends(require_admin)) -> dict:
    async with async_session_maker() as db:
        await _check_provider(db, body.provider_id)
        agent = Agent(**body.model_dump())
        db.add(agent)
        try:
            await db.flush()
            record_revision(db, "agent", agent, user.id)
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(status_code=409, detail="此业务场景已经绑定了智能体") from exc
        return agent_json(agent)


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    async with async_session_maker() as db:
        agent = await db.scalar(select(Agent).where(Agent.id == agent_id).with_for_update())
        if agent is None:
            raise HTTPException(status_code=404, detail="智能体不存在")
        return agent_json(agent)


@router.patch("/agents/{agent_id}")
@router.put("/agents/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate, user: User = Depends(require_admin)) -> dict:
    async with async_session_maker() as db:
        agent = (await db.execute(select(Agent).where(Agent.id == agent_id).with_for_update())).scalar_one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail="智能体不存在")
        check_version(agent, body.expected_version)
        fields = body.model_dump(exclude_unset=True, exclude={"expected_version"})
        # scene/provider_id explicitly support null to clear a binding.
        fields = {key: value for key, value in fields.items() if value is not None or key in {"scene", "provider_id"}}
        if "provider_id" in fields:
            await _check_provider(db, fields["provider_id"])
        if fields:
            for key, value in fields.items():
                setattr(agent, key, value)
            agent.version += 1
            record_revision(db, "agent", agent, user.id)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(status_code=409, detail="此业务场景已经绑定了智能体") from exc
        return agent_json(agent)


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, expected_version: int = Query(ge=1), user: User = Depends(require_admin)) -> Response:
    async with async_session_maker() as db:
        agent = await db.scalar(select(Agent).where(Agent.id == agent_id).with_for_update())
        if agent is None:
            raise HTTPException(status_code=404, detail="智能体不存在")
        check_version(agent, expected_version)
        agent.version += 1
        record_revision(db, "agent", agent, user.id, deleted=True)
        await db.delete(agent)
        await db.commit()
    return Response(status_code=204)


@router.get("/capabilities")
async def capabilities() -> dict:
    """Global scene policy, not a user's personal model availability."""
    async with async_session_maker() as db:
        agents = {agent.scene: agent for agent in (await db.execute(select(Agent))).scalars() if agent.scene}
    scenes = {}
    for scene in SCENES:
        agent = agents.get(scene)
        scenes[scene] = {
            "enabled": agent.enabled if agent else True,
            "agent_id": agent.id if agent else None,
        }
    media_configured = bool(settings.XFYUN_APP_ID and settings.XFYUN_API_KEY and settings.XFYUN_API_SECRET)
    return {
        "demo_mode": settings.PLATFORM_DEMO_MODE,
        "scenes": scenes,
        "asr": media_configured,
        "tts": media_configured,
        "vision": scenes["vision"]["enabled"],
        "tools": sorted(AVAILABLE_TOOLS),
    }
