"""Model Connection Kit host: private credentials, selections and real probes."""

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import async_session_maker
from app.platform.admin import encrypt_key
from app.platform.auth import current_user
from app.platform.contracts import PlatformError
from app.platform.model_providers import api_key_providers, provider_url
from app.platform.model_transport import allowed_urls, validate_personal_url
from app.platform.models import User, UserAISettings, UserModelCredential, UserOAuthSession, new_id

router = APIRouter(prefix="/settings/ai", tags=["personal-ai"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelCost(StrictModel):
    input: float = Field(ge=0, allow_inf_nan=False)
    output: float = Field(ge=0, allow_inf_nan=False)
    cacheRead: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cacheWrite: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ModelMetadata(StrictModel):
    input: list[Literal["text", "image"]] = Field(min_length=1, max_length=2)
    reasoning: bool
    contextWindow: int = Field(ge=1, le=10000000)
    maxTokens: int = Field(ge=1, le=10000000)
    cost: ModelCost | None = None

    @model_validator(mode="after")
    def limits(self):
        if self.maxTokens > self.contextWindow or "text" not in self.input:
            raise ValueError("模型需支持文本，输出上限不能超过上下文窗口")
        return self


class CustomEndpoint(StrictModel):
    baseUrl: str = Field(min_length=1, max_length=2000)
    api: Literal["openai-completions"]
    displayName: str | None = Field(default=None, max_length=100)
    model: ModelMetadata

    @field_validator("baseUrl")
    @classmethod
    def valid_url(cls, value):
        return validate_personal_url(value)


class ModelSelection(StrictModel):
    connectionType: Literal["builtin", "custom"]
    authMethod: Literal["api-key", "none", "oauth"]
    providerId: str = Field(min_length=1, max_length=2100)
    modelId: str = Field(min_length=1, max_length=200)
    custom: CustomEndpoint | None = None

    @field_validator("modelId")
    @classmethod
    def model_id(cls, value):
        value = value.strip()
        if not value or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError("请填写不含空白或控制字符的模型 ID")
        return value

    @model_validator(mode="after")
    def valid_selection(self):
        if self.connectionType == "builtin":
            if self.providerId == "openai" and self.authMethod == "oauth" and self.custom is None:
                return self
            if self.authMethod != "api-key" or self.custom is not None:
                raise ValueError("请选择支持的服务商和模型")
            provider_url(self.providerId)
        elif self.authMethod == "oauth" or self.custom is None or self.providerId != "custom:" + self.custom.baseUrl:
            raise ValueError("自定义连接标识必须与服务地址匹配")
        return self

    def base_url(self):
        if self.authMethod == "oauth":
            from app.platform.subscriptions import BASE_URL

            return BASE_URL
        return self.custom.baseUrl if self.custom else provider_url(self.providerId)


class SaveSelection(StrictModel):
    selection: ModelSelection
    expected_version: int = Field(ge=0)


class ConnectRequest(StrictModel):
    connectionType: Literal["builtin", "custom"]
    authMethod: Literal["api-key", "none"]
    providerId: str = Field(min_length=1, max_length=2100)
    apiKey: str | None = Field(default=None, max_length=4096)
    modelId: str | None = None
    custom: CustomEndpoint | None = None

    @model_validator(mode="after")
    def valid_request(self):
        if self.connectionType == "custom":
            ModelSelection.model_validate(self.model_dump(exclude={"apiKey"}))
        else:
            if self.authMethod != "api-key" or self.custom or self.modelId:
                raise ValueError("此服务商仅支持 API Key 认证")
            provider_url(self.providerId)
        if self.authMethod == "api-key" and not (self.apiKey or "").strip():
            raise ValueError("请填写 API Key")
        if self.authMethod == "none" and self.apiKey:
            raise ValueError("无认证连接不能携带 API Key")
        return self

    def base_url(self):
        return self.custom.baseUrl if self.custom else provider_url(self.providerId)


def auth_json(credential):
    if credential and credential.auth_method == "oauth":
        from app.platform.subscriptions import auth_json as oauth_auth_json

        return oauth_auth_json(credential)
    return {"state": "configured" if credential else "unconfigured",
            "method": "api-key" if credential and credential.encrypted_key else "none",
            "source": "个人连接" if credential else None}


async def credential_for(db, user_id, provider_id, *, lock=False):
    query = select(UserModelCredential).where(UserModelCredential.user_id == user_id,
                                              UserModelCredential.provider_id == provider_id)
    return await db.scalar(query.with_for_update() if lock else query)


async def config_for(db, user_id: str, selection: ModelSelection):
    credential = await credential_for(db, user_id, selection.providerId)
    if not credential or credential.base_url != selection.base_url() or (
            selection.authMethod == "api-key" and not credential.encrypted_key):
        raise HTTPException(409, "请先连接此服务商，再测试或保存")
    if (selection.authMethod == "oauth") != (credential.auth_method == "oauth"):
        raise HTTPException(409, "连接认证方式已更改，请重新选择模型")
    return {"scene": "chat", "enabled": True, "tools": [], "prompt": "",
            "base_url": credential.base_url, "model": selection.modelId,
            "encrypted_key": None if selection.authMethod == "oauth" else credential.encrypted_key, "credential_owner_id": user_id,
            "user_provider_id": credential.id, "provider_id": None, "provider_version": credential.version,
            "auth_method": selection.authMethod, "oauth_scope": credential.oauth_scope}


def settings_json(row):
    return {"selection": row.selection if row else None, "version": row.version if row else 0,
            "has_key": bool(row and row.encrypted_key), "allowed_base_urls": allowed_urls()}


@router.get("")
async def read_settings(user: User = Depends(current_user)):
    async with async_session_maker() as db:
        return settings_json(await db.get(UserAISettings, user.id))


@router.get("/providers")
async def list_providers(_user: User = Depends(current_user)):
    values = [{"id": "openai", "name": "ChatGPT", "description": "登录 ChatGPT 并授权使用订阅计划。",
               "auth": {"oauth": {"label": "Continue with ChatGPT", "isSubscription": True}}}]
    return values + api_key_providers()


@router.get("/models")
async def list_models(provider_id: str, _user: User = Depends(current_user)):
    if provider_id == "openai":
        from app.platform.subscription_client import list_models as subscription_models
        from app.platform.subscriptions import BASE_URL

        async with async_session_maker() as db:
            credential = await credential_for(db, _user.id, provider_id)
            if not credential or credential.auth_method != "oauth":
                return []
            config = {"user_provider_id": credential.id, "credential_owner_id": _user.id,
                      "oauth_scope": credential.oauth_scope, "base_url": BASE_URL}
        return await subscription_models(config, async_session_maker)
    if provider_id != "deepseek":
        return []
    # Conservative application presets, not the provider's live catalog or maximum context.
    return [{"id": model, "providerId": "deepseek", "name": model,
             "description": "预设 ID；展示应用默认预算，实际可用性请测试连接。",
             "input": ["text"], "reasoning": model.endswith("reasoner"),
             "contextWindow": 8192, "maxTokens": min(settings.AI_MAX_OUTPUT_TOKENS, 8192)}
            for model in ("deepseek-chat", "deepseek-reasoner")]


@router.get("/auth")
async def auth_status(provider_id: str = Query(max_length=2100), user: User = Depends(current_user)):
    async with async_session_maker() as db:
        return auth_json(await credential_for(db, user.id, provider_id))


@router.post("/connect")
async def connect(body: ConnectRequest, user: User = Depends(current_user)):
    base_url = body.base_url()
    async with async_session_maker() as db, db.begin():
        row = await credential_for(db, user.id, body.providerId, lock=True)
        if row is None:
            row = UserModelCredential(user_id=user.id, provider_id=body.providerId, base_url=base_url, version=1)
            db.add(row)
        else:
            row.version += 1
            if bool(row.encrypted_key) != (body.authMethod == "api-key"):
                row.id = new_id()
        row.encrypted_key = encrypt_key(body.apiKey.strip()) if body.authMethod == "api-key" else None
        row.auth_method = body.authMethod
        row.base_url = base_url
        try:
            await db.flush()
        except IntegrityError as exc:
            raise HTTPException(409, "认证配置已更新，请重试") from exc
        return {"providerId": body.providerId, "auth": auth_json(row)}


@router.put("")
async def save_selection(body: SaveSelection, user: User = Depends(current_user)):
    selection = body.selection
    async with async_session_maker() as db, db.begin():
        config = await config_for(db, user.id, selection)
        row = await db.get(UserAISettings, user.id, with_for_update=True)
        if body.expected_version != (row.version if row else 0):
            raise HTTPException(409, "模型选择已更新，请刷新后重试")
        if row is None:
            row = UserAISettings(user_id=user.id, version=1)
            db.add(row)
        else:
            row.version += 1
        row.base_url, row.model = config["base_url"], config["model"]
        row.encrypted_key = config["encrypted_key"] or ""
        row.vision = bool(selection.custom and "image" in selection.custom.model.input)
        row.selection = selection.model_dump(exclude_none=True)
        try:
            await db.flush()
        except IntegrityError as exc:
            raise HTTPException(409, "模型选择已更新，请刷新后重试") from exc
        return settings_json(row)


@router.delete("/auth")
async def disconnect(provider_id: str = Query(max_length=2100), user: User = Depends(current_user)):
    async with async_session_maker() as db, db.begin():
        await db.get(User, user.id, with_for_update=True)
        await db.execute(update(UserOAuthSession).where(UserOAuthSession.user_id == user.id,
            UserOAuthSession.provider_id == provider_id, UserOAuthSession.status.in_(["pending", "exchanging"]))
            .values(status="cancelled", encrypted_data=encrypt_key("{}")))
        credential = await credential_for(db, user.id, provider_id, lock=True)
        if credential:
            await db.delete(credential)
        row = await db.get(UserAISettings, user.id, with_for_update=True)
        if row and (row.selection or {}).get("providerId") == provider_id:
            await db.delete(row)
    return auth_json(None)


@router.delete("", status_code=204)
async def clear_selection(expected_version: int = Query(ge=1), user: User = Depends(current_user)):
    async with async_session_maker() as db, db.begin():
        row = await db.get(UserAISettings, user.id, with_for_update=True)
        if row and row.version != expected_version:
            raise HTTPException(409, "模型选择已更新，请刷新后重试")
        if row:
            await db.delete(row)
    return Response(status_code=204)


@router.post("/probe")
async def probe(selection: ModelSelection, user: User = Depends(current_user)):
    from app.platform import runtime
    from app.platform.ai.execution import attempt

    async with async_session_maker() as db:
        config = await config_for(db, user.id, selection)
    config["response_limit"] = 1024 * 1024
    messages = [{"role": "user", "content": "Respond with OK."}]
    try:
        async with asyncio.timeout(15):
            with runtime.call_scope(user.id):
                budget = settings.AI_MAX_OUTPUT_TOKENS if selection.authMethod == "oauth" else 1
                async with attempt(config, messages, time.monotonic() + 15, budget) as (client, state):
                    result = await client.chat.completions.create(
                        model=selection.modelId, messages=messages, max_tokens=1, stream=False,
                    )
                    state["usage"] = result.usage.model_dump() if result.usage else None
                    if not result.choices or not result.choices[0].message or not (
                        result.choices[0].message.content or getattr(result.choices[0].message, "reasoning_content", None)
                    ):
                        raise ValueError("Invalid model response")
        return {"state": "reachable", "testedAt": int(time.time() * 1000), "mayBeBillable": True,
                "message": "模型已返回有效响应；测试未写入对话。"}
    except PlatformError as exc:
        if exc.code in {"AI_QUOTA_EXCEEDED", "AI_OVERLOADED"}:
            raise
    except (TimeoutError, ValueError):
        pass
    return {"state": "unreachable", "testedAt": int(time.time() * 1000), "mayBeBillable": True,
            "message": "连接测试失败，请检查服务地址、模型 ID、Key 和服务商额度。"}
