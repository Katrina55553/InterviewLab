"""Versioned provider/Agent configuration with encrypted provider credentials."""

import base64
import hashlib
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import async_session_maker
from app.platform.auth import require_admin
from app.platform.models import Agent, Provider

router = APIRouter(prefix="/admin", tags=["platform-admin"], dependencies=[Depends(require_admin)])
Scene = Literal["resume", "questions", "evaluate", "followup", "report", "chat", "vision"]
SCENES = ("resume", "questions", "evaluate", "followup", "report", "chat", "vision")
AVAILABLE_TOOLS = frozenset({"search_knowledge_base", "lookup_resume_field", "verify_code_snippet"})


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
        "tools": agent.tools,
        "enabled": agent.enabled,
        "version": agent.version,
        "created_at": agent.created_at,
    }


@router.get("/providers")
async def list_providers() -> list[dict]:
    async with async_session_maker() as db:
        return [
            provider_json(item) for item in (await db.execute(select(Provider).order_by(Provider.created_at))).scalars()
        ]


@router.post("/providers", status_code=201)
async def create_provider(body: ProviderCreate) -> dict:
    fields = body.model_dump(exclude={"api_key"})
    provider = Provider(**fields, encrypted_key=encrypt_key(body.api_key.strip()) if body.api_key.strip() else None)
    async with async_session_maker() as db:
        db.add(provider)
        await db.commit()
    return provider_json(provider)


@router.get("/providers/{provider_id}")
async def get_provider(provider_id: str) -> dict:
    async with async_session_maker() as db:
        provider = await db.get(Provider, provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="模型配置不存在")
        return provider_json(provider)


@router.patch("/providers/{provider_id}")
@router.put("/providers/{provider_id}")
async def update_provider(provider_id: str, body: ProviderUpdate) -> dict:
    async with async_session_maker() as db:
        provider = (
            await db.execute(select(Provider).where(Provider.id == provider_id).with_for_update())
        ).scalar_one_or_none()
        if provider is None:
            raise HTTPException(status_code=404, detail="模型配置不存在")
        fields = body.model_dump(exclude_unset=True, exclude_none=True, exclude={"api_key"})
        if body.api_key and body.api_key.strip():
            fields["encrypted_key"] = encrypt_key(body.api_key.strip())
        if fields:
            for key, value in fields.items():
                setattr(provider, key, value)
            provider.version += 1
        await db.commit()
        return provider_json(provider)


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: str) -> Response:
    async with async_session_maker() as db:
        provider = await db.get(Provider, provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="模型配置不存在")
        # Configuration changes must be visible on the dependent Agent version too.
        await db.execute(
            update(Agent).where(Agent.provider_id == provider_id).values(provider_id=None, version=Agent.version + 1)
        )
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
async def create_agent(body: AgentCreate) -> dict:
    async with async_session_maker() as db:
        await _check_provider(db, body.provider_id)
        agent = Agent(**body.model_dump())
        db.add(agent)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(status_code=409, detail="此业务场景已经绑定了智能体") from exc
        return agent_json(agent)


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    async with async_session_maker() as db:
        agent = await db.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="智能体不存在")
        return agent_json(agent)


@router.patch("/agents/{agent_id}")
@router.put("/agents/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate) -> dict:
    async with async_session_maker() as db:
        agent = (await db.execute(select(Agent).where(Agent.id == agent_id).with_for_update())).scalar_one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail="智能体不存在")
        fields = body.model_dump(exclude_unset=True)
        # scene/provider_id explicitly support null to clear a binding.
        fields = {key: value for key, value in fields.items() if value is not None or key in {"scene", "provider_id"}}
        if "provider_id" in fields:
            await _check_provider(db, fields["provider_id"])
        if fields:
            for key, value in fields.items():
                setattr(agent, key, value)
            agent.version += 1
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(status_code=409, detail="此业务场景已经绑定了智能体") from exc
        return agent_json(agent)


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str) -> Response:
    async with async_session_maker() as db:
        agent = await db.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="智能体不存在")
        await db.delete(agent)
        await db.commit()
    return Response(status_code=204)


@router.get("/capabilities")
async def capabilities() -> dict:
    async with async_session_maker() as db:
        agents = {agent.scene: agent for agent in (await db.execute(select(Agent))).scalars() if agent.scene}
        providers = {provider.id: provider for provider in (await db.execute(select(Provider))).scalars()}
    scenes = {}
    for scene in SCENES:
        agent = agents.get(scene)
        provider = providers.get(agent.provider_id) if agent else None
        if scene == "vision" and agent is None:
            provider = next((item for item in providers.values() if item.enabled and item.vision), None)
        # The runtime deliberately supports env configuration before an administrator
        # adds scene overrides; an explicitly disabled Agent still disables its scene.
        configured = bool(
            (agent.enabled if agent else True)
            and (
                provider.enabled and provider.encrypted_key if provider else settings.LLM_API_KEY and scene != "vision"
            )
        )
        if scene == "vision":
            configured = configured and bool(provider and provider.vision)
        scenes[scene] = {
            "enabled": configured,
            "agent_id": agent.id if agent else None,
            "provider_id": provider.id if provider else None,
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
