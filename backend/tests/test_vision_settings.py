"""Image-verified visual settings stay independent, private and frozen per interview."""

import base64
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.platform import media, runtime, vision_settings
from app.platform.ai import guard
from app.platform.media_models import VisionFeedback
from app.platform.models import Agent, AiCall, ChatMessage, Interview, Resume, UserVisionSettings, new_id
from tests.test_user_model_connections import (
    SELECTION,
    connect,
    register,
    save,
)
from tests.test_user_model_connections import (
    personal_client as personal_client,
)

VISION = {"connectionType": "builtin", "authMethod": "api-key", "providerId": "openai-api", "modelId": "image-model"}


class ImageSDK:
    def __init__(self):
        self.content = "RED"
        self.configs = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=self.reply)))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def reply(self, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))], usage=None)

    def client(self, config):
        self.configs.append(copy.deepcopy(config))
        return self


@pytest_asyncio.fixture
async def visual(personal_client, monkeypatch):
    client, database = personal_client
    for module in (vision_settings, media):
        monkeypatch.setattr(module, "async_session_maker", database.sessions)
    if database.dialect == "sqlite":
        # SQLite covers persistence only; the PostgreSQL branch exercises the real lock.
        monkeypatch.setattr(media, "_try_lock", AsyncMock(return_value=True))
    sdk = ImageSDK()
    monkeypatch.setattr(runtime, "_client", sdk.client)
    guard._circuits.clear()
    return client, database, sdk


async def prepare(client):
    owner = await register(client)
    await connect(client)
    assert (await save(client)).status_code == 200
    assert (await client.post("/api/settings/ai/connect", json={
        "connectionType": "builtin", "authMethod": "api-key", "providerId": "openai-api", "apiKey": "image-private-key",
    })).status_code == 200
    return owner


async def enable(client, selection=None, version=0):
    selection = selection or VISION
    result = await client.post("/api/settings/ai/vision/probe", json=selection)
    assert result.status_code == 200, result.text
    assert result.json()["state"] == "reachable", result.text
    return await client.put("/api/settings/ai/vision", json={"enabled": True, "selection": selection,
        "expected_version": version, "probe_token": result.json()["probe_token"]})


@pytest.mark.asyncio
async def test_visual_settings_require_login_csrf_and_a_matching_successful_image_probe(visual):
    client, database, sdk = visual
    assert (await client.get("/api/settings/ai/vision")).status_code == 401
    await prepare(client)
    csrf = client.headers.pop("X-CSRF-Token")
    assert (await client.post("/api/settings/ai/vision/probe", json=VISION)).status_code == 403
    assert (await client.put("/api/settings/ai/vision", json={"enabled": False, "expected_version": 0})).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    body = {"enabled": True, "selection": VISION, "expected_version": 0}
    assert (await client.put("/api/settings/ai/vision", json=body)).status_code == 409
    sdk.content = "OK"
    failed = await client.post("/api/settings/ai/vision/probe", json=VISION)
    assert failed.json()["state"] == "unreachable" and "probe_token" not in failed.json()
    assert (await client.put("/api/settings/ai/vision", json=body)).status_code == 409
    assert (await client.get("/api/settings/ai")).json()["selection"] == SELECTION
    async with database.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 0
        assert await db.scalar(select(UserVisionSettings)) is None
        assert await db.scalar(select(AiCall.scene)) == "vision"
        assert await db.scalar(select(AiCall.outcome)) == "known"
    request = sdk.chat.completions.create.call_args.kwargs
    assert request["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert sdk.chat.completions.create.await_count == 1


@pytest.mark.asyncio
async def test_separate_model_is_frozen_and_text_changes_or_visual_failure_do_not_mix_configs(visual):
    client, database, sdk = visual
    owner = await prepare(client)
    saved = await enable(client)
    assert saved.status_code == 200, saved.text
    assert saved.json()["selection"] == VISION and saved.json()["verified"] is True
    assert "image-private-key" not in saved.text and "encrypted_key" not in saved.text
    frozen = await runtime.freeze_configs(user_id=owner)
    assert frozen["chat"]["model"] == SELECTION["modelId"]
    assert frozen["vision"]["model"] == VISION["modelId"] and frozen["vision"]["enabled"] is True
    assert frozen["vision"]["user_provider_id"] != frozen["chat"]["user_provider_id"]
    assert (await client.get("/api/media/capabilities")).json()["vision"] is True
    sdk.content = "画面建议。"
    resume_id, interview_id = new_id(), new_id()
    async with database.sessions() as db, db.begin():
        db.add(Resume(id=resume_id, user_id=owner, filename="test.txt", file_path="unused", status="ready"))
        await db.flush()
        db.add(Interview(id=interview_id, user_id=owner, resume_id=resume_id, config_snapshot=frozen))
    image = base64.b64decode(vision_settings.test_image().split(",")[1])
    response = await client.post(f"/api/interviews/{interview_id}/vision", files={"file": ("test.png", image, "image/png")})
    assert response.status_code == 200 and response.json()["feedback"] == "画面建议。", response.text
    async with database.sessions() as db:
        feedback = await db.scalar(select(VisionFeedback))
        assert feedback.provider_id == frozen["vision"]["user_provider_id"]
        assert feedback.feedback == "画面建议。"
    assert sdk.chat.completions.create.call_args.kwargs["model"] == VISION["modelId"]
    assert (await save(client, version=1, selection={**SELECTION, "modelId": "other-text-model"})).status_code == 200
    assert (await runtime.freeze_configs(user_id=owner))["vision"]["model"] == VISION["modelId"]
    disabled = await client.put("/api/settings/ai/vision", json={"enabled": False, "selection": VISION, "expected_version": 1})
    assert disabled.status_code == 200
    assert (await runtime.freeze_configs(user_id=owner))["vision"]["enabled"] is False
    assert frozen["vision"]["enabled"] is True
    assert (await client.get("/api/media/capabilities")).json()["vision"] is False


@pytest.mark.asyncio
async def test_probe_proofs_cannot_cross_users_models_rotated_credentials_or_expiry(visual):
    client, _, _ = visual
    owner = await prepare(client)
    result = (await client.post("/api/settings/ai/vision/probe", json=VISION)).json()
    body = {"enabled": True, "selection": VISION, "expected_version": 0, "probe_token": result["probe_token"]}
    assert (await client.put("/api/settings/ai/vision", json={**body, "selection": {**VISION, "modelId": "other-image-model"}})).status_code == 409
    assert (await client.put("/api/settings/ai/vision", json={**body, "probe_token": "0." + "0" * 64})).status_code == 409
    await client.post("/api/settings/ai/connect", json={"connectionType": "builtin", "authMethod": "api-key",
        "providerId": "openai-api", "apiKey": "rotated-image-key"})
    assert (await client.put("/api/settings/ai/vision", json=body)).status_code == 409
    assert (await enable(client)).status_code == 200
    body["probe_token"] = (await client.post("/api/settings/ai/vision/probe", json=VISION)).json()["probe_token"]
    frozen = await runtime.freeze_configs(user_id=owner)
    await register(client, "another_visual_owner")
    await connect(client)
    await client.post("/api/settings/ai/connect", json={"connectionType": "builtin", "authMethod": "api-key",
        "providerId": "openai-api", "apiKey": "rotated-image-key"})
    assert (await client.get("/api/settings/ai/vision")).json()["selection"] is None
    assert (await client.put("/api/settings/ai/vision", json=body)).status_code == 409
    assert frozen["vision"]["credential_owner_id"] == owner


@pytest.mark.asyncio
async def test_credential_disconnect_clears_only_matching_visual_settings_and_policy_still_applies(visual):
    client, database, _ = visual
    owner = await prepare(client)
    assert (await enable(client)).status_code == 200
    async with database.sessions() as db, db.begin():
        db.add(Agent(name="vision policy", scene="vision", enabled=False, tools=[]))
    assert (await runtime.freeze_configs(user_id=owner))["vision"]["enabled"] is False
    assert (await client.delete("/api/settings/ai/auth?provider_id=openai-api")).status_code == 200
    state = (await client.get("/api/settings/ai/vision")).json()
    assert state["selection"] is None and state["enabled"] is False and state["version"] == 2
    assert (await client.get("/api/settings/ai")).json()["selection"] == SELECTION
    assert (await client.put("/api/settings/ai/vision", json={"enabled": False, "expected_version": 1})).status_code == 409


@pytest.mark.asyncio
async def test_key_rotation_disables_new_visual_runs_until_retested_without_changing_old_snapshot(visual):
    client, _, _ = visual
    owner = await prepare(client)
    assert (await enable(client)).status_code == 200
    frozen = await runtime.freeze_configs(user_id=owner)
    await client.post("/api/settings/ai/connect", json={"connectionType": "builtin", "authMethod": "api-key",
        "providerId": "openai-api", "apiKey": "new-image-key"})
    assert (await client.get("/api/settings/ai/vision")).json()["verified"] is False
    assert (await runtime.freeze_configs(user_id=owner))["vision"]["enabled"] is False
    assert frozen["vision"]["enabled"] is True
    assert (await enable(client, version=1)).status_code == 200
    assert (await runtime.freeze_configs(user_id=owner))["vision"]["enabled"] is True


def test_expired_and_forged_proofs_are_rejected():
    value = "fingerprint"
    for token in (vision_settings.probe_token(value, int(time.time()) - 601), "nan.signature", "123.invalid"):
        with pytest.raises(vision_settings.HTTPException):
            vision_settings.verify_probe(token, value)


@pytest.mark.asyncio
async def test_late_image_probe_cannot_verify_a_replacement_key(visual):
    client, _, sdk = visual
    await prepare(client)

    async def rotate_during_probe(**kwargs):
        await client.post("/api/settings/ai/connect", json={"connectionType": "builtin", "authMethod": "api-key",
            "providerId": "openai-api", "apiKey": "replacement-during-probe"})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="RED"))], usage=None)

    sdk.chat.completions.create.side_effect = rotate_during_probe
    result = await client.post("/api/settings/ai/vision/probe", json=VISION)
    assert result.status_code == 409
    assert "probe_token" not in result.json()


@pytest.mark.asyncio
async def test_parallel_first_save_has_one_winner_in_postgresql(visual):
    import asyncio

    client, database, _ = visual
    if database.dialect != "postgresql":
        pytest.skip("Initial-row serialization requires PostgreSQL locks")
    await prepare(client)
    token = (await client.post("/api/settings/ai/vision/probe", json=VISION)).json()["probe_token"]
    body = {"enabled": True, "selection": VISION, "expected_version": 0, "probe_token": token}
    responses = await asyncio.gather(*(client.put("/api/settings/ai/vision", json=body) for _ in range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
