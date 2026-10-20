"""Protocol/unit checks; provider doubles do not claim real Xunfei acceptance."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.platform import media
from app.platform.media_asr import TranscriptAssembler, asr_signed_url, check_asr_error
from app.platform.media_models import MediaTask
from app.platform.media_tts import (
    TTS_CREATE,
    TTS_QUERY,
    TTSProviderError,
    parse_tts_response,
    tts_create_body,
    tts_signed_url,
    validate_audio_url,
)
from app.platform.models import User, utcnow


def packet(segment_id, value, *, begin=0, end=500, stable=False, final=False, **extra):
    return {"data": {"seg_id": segment_id, "ls": final, "cn": {"st": {
        "bg": begin, "ed": end, "type": "0" if stable else "1",
        "rt": [{"ws": [{"cw": [{"w": value}]}]}], **extra,
    }}}}


def mock_session(*values):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(side_effect=[SimpleNamespace(scalar_one_or_none=lambda value=value: value)
                                              for value in values])
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.fixture
def media_client():
    app = FastAPI()
    app.include_router(media.router, prefix="/api")
    app.include_router(media.ws_router)
    with TestClient(app) as client:
        yield client


def authorize(client):
    client.app.dependency_overrides[media.current_user] = lambda: User(id="owner", username="owner")
    client.app.dependency_overrides[media.check_csrf] = lambda: None


def test_ast_signing_uses_sorted_encoded_parameters_and_hmac_sha1():
    fixed = datetime(2026, 10, 15, 1, 2, 3, tzinfo=UTC)
    url = asr_signed_url("wss://provider.invalid/ast", "app", "key", "secret", now=fixed, session_id="unique")
    params = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
    signature = params.pop("signature")
    assert params["utc"] == "2026-10-15T01:02:03+0000"
    assert params["samplerate"] == "16000"
    assert params["audio_encode"] == "pcm_s16le"
    assert "utc=2026-10-15T01%3A02%3A03%2B0000" in url
    expected = base64.b64encode(hmac.new(b"secret", urlencode(sorted(params.items())).encode(), hashlib.sha1).digest()).decode()
    assert signature == expected
    assert "secret" not in url


def test_transcript_revises_same_segment_without_duplicate_append():
    assembler = TranscriptAssembler()
    assert assembler.apply(packet(0, "我使用"))["live_text"] == "我使用"
    update = assembler.apply(packet(0, "我使用 Python", stable=True))
    assert update["display_text"] == "我使用 Python"
    assert update["committed_text"] == "我使用 Python"
    assert assembler.apply(packet(0, "我使用 Python", stable=True)) is None
    assert assembler.apply(packet(0, "我使")) is None  # Delayed partial cannot roll back stable text.


def test_transcript_time_overlap_replaces_new_id_and_ignores_retired_packet():
    assembler = TranscriptAssembler()
    assembler.apply(packet(0, "我使用", end=400))
    update = assembler.apply(packet(1, "我使用 Python", end=650))
    assert update["display_text"] == "我使用 Python"
    assert assembler.apply(packet(0, "我使用", end=400, stable=True)) is None
    update = assembler.apply(packet(2, "开发服务", begin=700, end=1200, stable=True))
    assert update["display_text"] == "我使用 Python开发服务"
    assert update["committed_text"] == ""
    update = assembler.apply({"data": {"ls": True}})
    assert update["committed_text"] == "我使用 Python开发服务"
    assert update["live_text"] == ""
    assert update["final"] is True


def test_transcript_wpgs_inclusive_range_replacement():
    assembler = TranscriptAssembler()
    assembler.apply(packet(1, "我们", pgs="apd"))
    assembler.apply(packet(2, "讨论", pgs="apd"))
    update = assembler.apply(packet(3, "我们实现", pgs="rpl", rg=[1, 2], stable=True))
    assert update["display_text"] == "我们实现"
    assert assembler.apply(packet(2, "讨论", pgs="apd", stable=True)) is None


@pytest.mark.parametrize("same_id", [False, True])
def test_transcript_punctuation_does_not_erase_sentence(same_id):
    assembler = TranscriptAssembler()
    assembler.apply(packet(0, "完成", stable=True))
    update = assembler.apply(packet(0 if same_id else 1, "。", stable=True))
    assert update["display_text"] == "完成。"
    assert assembler.apply(packet(0 if same_id else 1, "。", stable=True)) is None


def test_transcript_accepts_json_encoded_data():
    payload = packet(1, "转写完成", stable=True, final=True)
    payload["data"] = json.dumps(payload["data"])
    update = TranscriptAssembler().apply(json.dumps(payload))
    assert update["committed_text"] == "转写完成"


@pytest.mark.parametrize("error", [{"action": "error", "code": "35001"}, {"data": {"normal": False}}])
def test_ast_business_error_fails_explicitly(error):
    with pytest.raises(ValueError, match="语音识别服务返回错误"):
        check_asr_error(error)


def test_tts_signing_has_utc_date_and_post_request_line():
    fixed = datetime(2026, 10, 15, 1, 2, 3, tzinfo=UTC)
    url = tts_signed_url(TTS_CREATE, "testkey", "testsecret", now=fixed)
    params = parse_qs(urlsplit(url).query)
    assert params["date"] == ["Thu, 15 Oct 2026 01:02:03 GMT"]
    auth = base64.b64decode(params["authorization"][0]).decode()
    canonical = "host: api-dx.xf-yun.com\ndate: Thu, 15 Oct 2026 01:02:03 GMT\nPOST /v1/private/dts_create HTTP/1.1"
    signature = base64.b64encode(hmac.new(b"testsecret", canonical.encode(), hashlib.sha256).digest()).decode()
    assert f'signature="{signature}"' in auth
    assert 'api_key="testkey"' in auth
    assert "testsecret" not in url
    assert tts_signed_url(TTS_QUERY, "testkey", "testsecret", now=fixed) != url


def test_tts_body_uses_utf8_base64_and_mp3():
    body = tts_create_body("app", "介绍你的项目", "x4_mingge", "request-id")
    assert base64.b64decode(body["payload"]["text"]["text"]).decode() == "介绍你的项目"
    assert body["parameter"]["dts"]["audio"] == {"encoding": "lame", "sample_rate": 16000}
    assert body["header"]["request_id"] == "request-id"


def test_tts_success_decodes_audio_link_and_preserves_task_id():
    url = "https://cdn.xfyun.cn/audio.mp3"
    result = parse_tts_response({
        "header": {"code": 0, "task_id": "task", "task_status": "5"},
        "payload": {"audio": {"audio": base64.b64encode(url.encode()).decode()}},
    })
    assert result == {"provider_task_id": "task", "status": "completed", "audio_url": url}


@pytest.mark.parametrize("status, expected", [("1", "processing"), ("3", "processing"), ("2", "failed"), ("4", "failed")])
def test_tts_reports_provider_task_state(status, expected):
    assert parse_tts_response({"header": {"code": 0, "task_status": status}})["status"] == expected


@pytest.mark.parametrize("url", ["file:///tmp/key", "http://127.0.0.1/audio", "https://xfyun.cn.evil.test/audio", "https://user:pass@cdn.xfyun.cn/audio"])
def test_tts_rejects_untrusted_download_locations(url):
    with pytest.raises(TTSProviderError):
        validate_audio_url(url)


def test_media_routes_require_cookie_identity(media_client):
    assert media_client.get("/api/media/capabilities").status_code == 401
    assert media_client.get("/api/media/tts/not-owned").status_code == 401
    assert media_client.get("/api/media/tts/not-owned/audio").status_code == 401
    with pytest.raises(WebSocketDisconnect) as exc, media_client.websocket_connect(
        "/ws/asr/not-owned", headers={"origin": "https://evil.test"},
    ):
        pass
    assert exc.value.code == 1008


def test_disabled_capabilities_are_truthful(media_client, monkeypatch):
    authorize(media_client)
    monkeypatch.setattr(media, "_credentials", lambda: ("", "", ""))
    session = mock_session(None, None)
    monkeypatch.setattr(media, "async_session_maker", lambda: session)
    assert media_client.get("/api/media/capabilities").json() == {"asr": False, "tts": False, "vision": False}


def test_cross_user_media_task_returns_not_found(media_client, monkeypatch):
    authorize(media_client)
    session = mock_session(None)
    monkeypatch.setattr(media, "async_session_maker", lambda: session)
    assert media_client.get("/api/media/tts/other-task/audio").status_code == 404
    params = session.execute.call_args.args[0].compile().params
    assert "owner" in params.values()  # Ownership is enforced by the DB query, not a UI filter.


@pytest.mark.asyncio
async def test_owned_interview_is_filtered_before_media_access():
    session = mock_session(None)
    with pytest.raises(HTTPException) as exc:
        await media.owned_interview(session, "other-interview", "owner")
    assert exc.value.status_code == 404
    assert "owner" in session.execute.call_args.args[0].compile().params.values()


@pytest.mark.asyncio
async def test_tts_unknown_create_is_saved_and_never_retried(monkeypatch):
    session = mock_session(None)
    monkeypatch.setattr(media, "async_session_maker", lambda: session)
    monkeypatch.setattr(media, "_require_speech", lambda: ("app", "key", "secret"))
    monkeypatch.setattr(media, "owned_interview", AsyncMock())
    request = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(media, "tts_request", request)
    result = await media.create_tts(media.TTSRequest(interview_id="interview", text="你好"), User(id="owner"))
    assert result["status"] == "unknown"
    assert result["audio_url"] is None
    task = session.add.call_args.args[0]
    session.execute.side_effect = [SimpleNamespace(scalar_one_or_none=lambda: task)]
    repeated = await media.create_tts(media.TTSRequest(interview_id="interview", text="你好"), User(id="owner"))
    assert repeated == result
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_interrupted_tts_creation_becomes_unknown_on_read(monkeypatch):
    task = MediaTask(id="task", user_id="owner", interview_id="interview", status="creating",
                     text_hash="hash", created_at=utcnow() - timedelta(minutes=1))
    session = mock_session(task)
    monkeypatch.setattr(media, "async_session_maker", lambda: session)
    query = AsyncMock()
    monkeypatch.setattr(media, "tts_request", query)
    result = await media.query_tts("task", User(id="owner"))
    assert result["status"] == "unknown"
    query.assert_not_awaited()


class FakeBrowser:
    def __init__(self, messages):
        self.messages = asyncio.Queue()
        for message in messages:
            self.messages.put_nowait(message)
        self.events = []

    async def receive(self):
        return await self.messages.get()

    async def send_json(self, payload):
        self.events.append(payload)


class FakeProvider:
    def __init__(self, *, stalled=False):
        self.messages = asyncio.Queue()
        self.sent = []
        self.stalled = stalled

    async def send(self, payload):
        self.sent.append(payload)
        if self.stalled:
            await asyncio.Event().wait()
        if isinstance(payload, str):
            self.messages.put_nowait(json.dumps(packet(0, "最终文本", stable=True, final=True)))

    async def recv(self):
        return await self.messages.get()


async def run_stream(browser, provider):
    tasks = []
    try:
        await asyncio.wait_for(media._run_asr_stream(browser, provider, "provider-sid", {"action": "started"}, tasks), timeout=2)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert all(task.done() for task in tasks)


@pytest.mark.asyncio
async def test_asr_proxies_pcm_end_and_final_transcript_then_cleans_up():
    browser = FakeBrowser([
        {"type": "websocket.receive", "bytes": bytes(1280)},
        {"type": "websocket.receive", "text": '{"type":"end"}'},
    ])
    provider = FakeProvider()
    await run_stream(browser, provider)
    assert provider.sent[0] == bytes(1280)
    assert json.loads(provider.sent[1]) == {"end": True, "sessionId": "provider-sid"}
    assert browser.events[0]["display_text"] == "最终文本"
    assert browser.events[-1] == {"type": "status", "status": "completed"}


@pytest.mark.asyncio
async def test_asr_rejects_backpressure_instead_of_dropping_audio():
    browser = FakeBrowser([{"type": "websocket.receive", "bytes": bytes(1280)} for _ in range(100)])
    provider = FakeProvider(stalled=True)
    with pytest.raises(ValueError, match="发送过快|拥塞"):
        await run_stream(browser, provider)
    assert len(provider.sent) <= 1


@pytest.mark.asyncio
async def test_asr_disconnect_cancels_reader_and_writer():
    browser = FakeBrowser([{"type": "websocket.disconnect", "code": 1000}])
    with pytest.raises(WebSocketDisconnect):
        await run_stream(browser, FakeProvider())


@pytest.mark.asyncio
async def test_asr_revoked_login_stops_existing_stream_and_cleans_tasks():
    tasks = []
    revoked = AsyncMock(side_effect=ValueError("登录已撤销"))
    try:
        with pytest.raises(ValueError, match="登录已撤销"):
            await asyncio.wait_for(media._run_asr_stream(
                FakeBrowser([]), FakeProvider(), "sid", {"action": "started"}, tasks,
                revalidate=revoked, auth_interval=0.01,
            ), timeout=1)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    revoked.assert_awaited_once()
    assert all(task.done() for task in tasks)


@pytest.mark.parametrize("data, expected", [(b"\xff\xd8\xffjpeg", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"RIFFxxxxWEBP", "image/webp")])
def test_camera_requires_supported_image_magic(data, expected):
    assert media.image_mime(data) == expected
    with pytest.raises(HTTPException) as exc:
        media.image_mime(b"not an image")
    assert exc.value.status_code == 415


@pytest.mark.asyncio
async def test_frozen_disabled_vision_never_calls_provider(monkeypatch):
    from io import BytesIO

    from fastapi import UploadFile

    session = mock_session()
    monkeypatch.setattr(media, "async_session_maker", lambda: session)
    monkeypatch.setattr(media, "owned_interview", AsyncMock(return_value=SimpleNamespace(config_snapshot={
        "vision": {"enabled": False, "provider_id": "provider"},
    })))
    provider = AsyncMock()
    monkeypatch.setattr(media, "complete_vision", provider)
    result = await media.analyze_frame("interview", UploadFile(BytesIO(b"\xff\xd8\xffjpeg")), User(id="owner"))
    assert result["available"] is False
    provider.assert_not_awaited()
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_inflight_vision_is_rejected_before_call(monkeypatch):
    from io import BytesIO

    from fastapi import UploadFile

    session = mock_session()
    monkeypatch.setattr(media, "async_session_maker", lambda: session)
    monkeypatch.setattr(media, "owned_interview", AsyncMock(return_value=SimpleNamespace(config_snapshot={
        "vision": {"enabled": True, "provider_id": "provider"},
    })))
    monkeypatch.setattr(media, "_try_lock", AsyncMock(return_value=False))
    provider = AsyncMock()
    monkeypatch.setattr(media, "complete_vision", provider)
    with pytest.raises(HTTPException) as exc:
        await media.analyze_frame("interview", UploadFile(BytesIO(b"\xff\xd8\xffjpeg")), User(id="owner"))
    assert exc.value.status_code == 409
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_vision_uses_audited_gateway_with_owner_and_frozen_prompt(monkeypatch):
    from io import BytesIO

    from fastapi import UploadFile

    from app.platform import runtime

    session = mock_session(None)
    monkeypatch.setattr(media, "async_session_maker", lambda: session)
    config = {"scene": "vision", "enabled": True, "provider_id": "provider", "provider_version": 3,
              "prompt": "冻结提示词", "model": "vision-model", "base_url": "https://provider.invalid/v1"}
    monkeypatch.setattr(media, "owned_interview", AsyncMock(return_value=SimpleNamespace(config_snapshot={"vision": config})))
    monkeypatch.setattr(media, "_try_lock", AsyncMock(return_value=True))
    seen = []

    async def gateway(frozen_config, data_url):
        seen.append((frozen_config, data_url, runtime._call_context.get()))
        return "镜头高度适中，建议改善照明。"

    monkeypatch.setattr(media, "complete_vision", gateway)
    result = await media.analyze_frame("interview", UploadFile(BytesIO(b"\xff\xd8\xffjpeg")), User(id="owner"))
    assert result["available"] is True
    assert seen[0][2] == {"user_id": "owner", "interview_id": "interview"}
    assert seen[0][0]["provider_version"] == 3
    assert seen[0][0]["prompt"].startswith("冻结提示词")
    assert "不打分" in seen[0][0]["prompt"]
    assert seen[0][1].startswith("data:image/jpeg;base64,")
    persisted = session.add.call_args.args[0]
    assert persisted.feedback == result["feedback"]
    assert not hasattr(persisted, "image")
    assert runtime._call_context.get() is None


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("PLATFORM_TEST_DATABASE_URL"), reason="requires real PostgreSQL PLATFORM_TEST_DATABASE_URL")
async def test_postgresql_media_lock_excludes_second_connection_and_recovers():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(os.environ["PLATFORM_TEST_DATABASE_URL"], pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine)
    interview_id = str(uuid.uuid4())
    try:
        async with sessions() as first, sessions() as second:
            assert await media._try_lock(first, "vision", interview_id) is True
            assert await media._try_lock(second, "vision", interview_id) is False
            # Another interview is independent; release by process/transaction cleanup.
            assert await media._try_lock(second, "vision", str(uuid.uuid4())) is True
            await first.rollback()
            assert await media._try_lock(second, "vision", interview_id) is True
            await second.rollback()
    finally:
        await engine.dispose()
