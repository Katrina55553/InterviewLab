"""Private visual model settings, verified with an image before enabling new runs."""

import asyncio
import base64
import hashlib
import hmac
import json
import struct
import time
import zlib
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from app.core.config import settings
from app.core.database import async_session_maker
from app.platform.auth import current_user
from app.platform.contracts import PlatformError
from app.platform.models import User, UserAISettings, UserVisionSettings
from app.platform.user_settings import ModelSelection, StrictModel, config_for

router = APIRouter(prefix="/settings/ai/vision", tags=["personal-vision"])
PROBE_TTL = 600


class SaveVision(StrictModel):
    enabled: bool = Field(strict=True)
    selection: ModelSelection | None = None
    expected_version: int = Field(ge=0)
    probe_token: str | None = Field(default=None, max_length=100)


def fingerprint(user_id, selection, config):
    # OAuth refreshes may rotate tokens without changing the authorized identity.
    identity = config.get("oauth_scope") if config["auth_method"] == "oauth" else config["provider_version"]
    value = [user_id, selection.model_dump(exclude_none=True), config["user_provider_id"], identity]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def probe_token(value, timestamp):
    signature = hmac.new(settings.SECRET_KEY.encode(), f"vision:{timestamp}:{value}".encode(), hashlib.sha256).hexdigest()
    return f"{timestamp}.{signature}"


def verify_probe(token, value):
    try:
        timestamp = int((token or "").split(".")[0])
        if not 0 <= time.time() - timestamp <= PROBE_TTL or not hmac.compare_digest(token, probe_token(value, timestamp)):
            raise ValueError("expired or different probe")
        return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(409, "请先测试此视觉模型，测试通过后在 10 分钟内保存") from exc


async def resolve_vision(db, user_id, personal=None):
    """Missing/revoked visual credentials never prevent a text interview."""
    row = await db.get(UserVisionSettings, user_id)
    if row is None:
        # Preserve previously enabled custom image connections until explicitly edited.
        personal = personal or await db.get(UserAISettings, user_id)
        selection = personal.selection if personal and personal.vision else None
    else:
        selection = row.selection if row.enabled else None
    if not selection:
        return None, None
    try:
        parsed = ModelSelection.model_validate(selection)
        config = await config_for(db, user_id, parsed)
        if row is not None and fingerprint(user_id, parsed, config) != row.probe_fingerprint:
            return None, None
        return config, parsed
    except (HTTPException, ValueError):
        return None, None


async def clear_provider(db, user_id, provider_id):
    row = await db.get(UserVisionSettings, user_id, with_for_update=True)
    if row and (row.selection or {}).get("providerId") == provider_id:
        row.enabled, row.selection, row.probe_fingerprint, row.tested_at = False, None, None, None
        row.version += 1


def settings_json(row, legacy=None, *, verified=False):
    return {"selection": row.selection if row else legacy.selection if legacy and legacy.vision else None,
            "enabled": row.enabled if row else bool(legacy and legacy.vision), "version": row.version if row else 0,
            "tested_at": row.tested_at.isoformat() if row and row.tested_at else None, "verified": verified}


@router.get("")
async def read_settings(user: User = Depends(current_user)):
    async with async_session_maker() as db:
        row = await db.get(UserVisionSettings, user.id)
        legacy = await db.get(UserAISettings, user.id) if row is None else None
        connection, _ = await resolve_vision(db, user.id, legacy)
        return settings_json(row, legacy, verified=connection is not None)


@router.put("")
async def save_settings(body: SaveVision, user: User = Depends(current_user)):
    async with async_session_maker() as db, db.begin():
        # Serialize first writes and credential disconnects for this account.
        await db.get(User, user.id, with_for_update=True)
        row = await db.get(UserVisionSettings, user.id, with_for_update=True)
        if body.expected_version != (row.version if row else 0):
            raise HTTPException(409, "视觉模型设置已更新，请刷新后重试")
        value = tested_at = None
        if body.enabled:
            if body.selection is None:
                raise HTTPException(422, "请选择视觉模型")
            config = await config_for(db, user.id, body.selection)
            value = fingerprint(user.id, body.selection, config)
            tested_at = verify_probe(body.probe_token, value)
        if row is None:
            row = UserVisionSettings(user_id=user.id, version=1)
            db.add(row)
        else:
            row.version += 1
        row.selection = body.selection.model_dump(exclude_none=True) if body.selection else None
        row.enabled, row.probe_fingerprint, row.tested_at = body.enabled, value, tested_at
        await db.flush()
        return settings_json(row, verified=body.enabled)


def test_image():
    """A small red PNG, generated without camera access or user data."""
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))

    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00" * 32) * 32)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(data).decode()


@router.post("/probe")
async def probe(selection: ModelSelection, user: User = Depends(current_user)):
    from app.platform import runtime
    from app.platform.ai.execution import attempt

    async with async_session_maker() as db:
        config = await config_for(db, user.id, selection)
    value = fingerprint(user.id, selection, config)
    config.update(scene="vision", response_limit=1024 * 1024)
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "Read the solid color in this image. Reply with exactly one word: RED, GREEN or BLUE."},
        {"type": "image_url", "image_url": {"url": test_image()}},
    ]}]
    try:
        deadline = time.monotonic() + 15
        async with asyncio.timeout(15):
            with runtime.call_scope(user.id):
                budget = settings.AI_MAX_OUTPUT_TOKENS if selection.authMethod == "oauth" else 64
                async with attempt(config, messages, deadline, budget) as (client, state):
                    result = await client.chat.completions.create(model=selection.modelId, messages=messages,
                                                                  max_tokens=64, stream=False)
                    state["usage"] = result.usage.model_dump() if result.usage else None
                    if not result.choices or (result.choices[0].message.content or "").strip().upper() != "RED":
                        raise runtime.AIError("模型未通过图片识别测试")
    except PlatformError as exc:
        if exc.code in {"AI_QUOTA_EXCEEDED", "AI_OVERLOADED"}:
            raise
    except (TimeoutError, ValueError):
        pass
    else:
        async with async_session_maker() as db:
            current = await config_for(db, user.id, selection)
            if fingerprint(user.id, selection, current) != value:
                raise HTTPException(409, "认证配置已更新，请重新测试视觉模型")
        timestamp = int(time.time())
        return {"state": "reachable", "testedAt": timestamp * 1000, "mayBeBillable": True,
                "probe_token": probe_token(value, timestamp), "message": "测试图片识别通过，可以保存视觉模型。"}
    return {"state": "unreachable", "testedAt": int(time.time() * 1000), "mayBeBillable": True,
            "message": "图片识别测试未通过，请确认模型支持图片、认证有效且额度充足。"}
