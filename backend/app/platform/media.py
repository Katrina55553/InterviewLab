"""Authenticated speech proxy and opt-in camera feedback, separate from grading."""

import asyncio
import base64
import contextlib
import hashlib
import json
import time
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import async_session_maker
from app.platform.ai.guard import gate
from app.platform.auth import check_csrf, current_user, websocket_user
from app.platform.contracts import PlatformError, error_payload
from app.platform.media_asr import TranscriptAssembler, asr_signed_url, check_asr_error, decode_packet
from app.platform.media_locks import try_media_lock as _try_lock
from app.platform.media_models import MediaTask, VisionFeedback
from app.platform.media_tts import (
    TTS_CREATE,
    TTS_QUERY,
    TTSProviderError,
    download_audio,
    tts_create_body,
    tts_request,
)
from app.platform.models import Agent, Interview, User, new_id, utcnow
from app.platform.runtime import AIError, call_scope, complete_vision

router = APIRouter(tags=["platform-media"])
ws_router = APIRouter(tags=["platform-media"])
MAX_FRAME_BYTES = 1024 * 1024
AUDIO_QUEUE_SIZE = 25  # One second of PCM, never an unbounded replay buffer.
ASR_MAX_SECONDS = 3600


def _credentials() -> tuple[str, str, str]:
    return tuple(str(getattr(settings, key, "")).strip() for key in (
        "XFYUN_APP_ID", "XFYUN_API_KEY", "XFYUN_API_SECRET",
    ))


def _require_speech() -> tuple[str, str, str]:
    credentials = _credentials()
    if not all(credentials):
        raise HTTPException(503, "尚未配置讯飞语音服务")
    return credentials


async def owned_interview(db, interview_id: str, user_id: str, *, lock: bool = False) -> Interview:
    query = select(Interview).where(
        Interview.id == interview_id, Interview.user_id == user_id,
    )
    interview = (await db.execute(query.with_for_update() if lock else query)).scalar_one_or_none()
    if interview is None:
        raise HTTPException(404, "面试记录不存在")
    return interview


@router.get("/media/capabilities")
async def capabilities(interview_id: str | None = None, _user: User = Depends(current_user)) -> dict:
    async with async_session_maker() as db:
        if interview_id:
            interview = await owned_interview(db, interview_id, _user.id)
            vision_enabled = bool((interview.config_snapshot.get("vision") or {}).get("enabled"))
        else:
            agent = (await db.execute(select(Agent).where(Agent.scene == "vision"))).scalar_one_or_none()
            from app.platform.vision_settings import resolve_vision

            connection, _ = await resolve_vision(db, _user.id)
            vision_enabled = bool(connection and (agent is None or agent.enabled))
    configured = all(_credentials())
    return {"asr": configured, "tts": configured, "vision": vision_enabled}


@ws_router.websocket("/ws/asr/{interview_id}")
async def asr_socket(websocket: WebSocket, interview_id: str) -> None:
    user = await websocket_user(websocket)
    async with async_session_maker() as db:
        try:
            await owned_interview(db, interview_id, user.id)
        except HTTPException:
            await websocket.close(code=1008, reason="面试记录不存在")
            return
    await websocket.accept()
    try:
        credentials = _require_speech()
    except HTTPException as exc:
        await websocket.send_json({"type": "error", **error_payload(exc)})
        await websocket.close(code=1013)
        return
    tasks: list[asyncio.Task] = []
    try:
        initial = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if not isinstance(initial, dict) or initial.get("type") != "start":
            raise ValueError("请先发送 start 命令")
        # Imported lazily so deployments without speech extras can use text features.
        from websockets.asyncio.client import connect

        base_url = getattr(settings, "XFYUN_ASR_URL", "wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1")
        if not base_url.startswith("wss://"):
            raise ValueError("语音识别服务必须使用加密连接")
        async with gate("media").enter(time.monotonic() + 1), connect(asr_signed_url(base_url, *credentials), open_timeout=10, close_timeout=3,
                           max_size=1024 * 1024, max_queue=8, ping_interval=20) as upstream:
            first = decode_packet(await asyncio.wait_for(upstream.recv(), timeout=10))
            check_asr_error(first)
            provider_sid = first.get("sid") or first.get("sessionId")
            if not provider_sid:
                raise ValueError("语音服务未返回会话标识")
            await websocket.send_json({"type": "status", "status": "started"})
            async def revalidate() -> None:
                # Authentication is revocable while an hour-long microphone
                # socket stays open. Do not reuse the handshake's cached user.
                if getattr(websocket.state, "platform_identity", None) is not None:
                    del websocket.state.platform_identity
                await websocket_user(websocket)
                async with async_session_maker() as db:
                    await owned_interview(db, interview_id, user.id)

            await _run_asr_stream(websocket, upstream, str(provider_sid), first, tasks, revalidate=revalidate)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else "语音连接中断，请重新开启麦克风；已转写文字仍可编辑"
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "error", **error_payload(exc if isinstance(exc, PlatformError) else AIError(message, unknown=True))})
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await websocket.close()


async def _run_asr_stream(websocket, upstream, provider_sid: str, first: dict,
                          tasks: list[asyncio.Task], *, revalidate=None, auth_interval: float = 5) -> None:
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=AUDIO_QUEUE_SIZE)
    assembler = TranscriptAssembler()
    completed = asyncio.Event()
    send_lock = asyncio.Lock()

    async def emit(payload: dict) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    async def browser_input() -> None:
        frames = 0
        while not completed.is_set():
            message = await asyncio.wait_for(websocket.receive(), timeout=20)
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            if message.get("bytes") is not None:
                chunk = message["bytes"]
                if not chunk or len(chunk) > 1280 or len(chunk) % 2:
                    raise ValueError("音频帧须为最多 1280 字节的 16kHz 单声道 PCM16")
                frames += len(chunk)
                if frames > ASR_MAX_SECONDS * 32000:
                    raise ValueError("本次语音已达一小时，请重新开启麦克风")
                try:
                    queue.put_nowait(chunk)
                except asyncio.QueueFull as exc:
                    from app.platform.operations.telemetry import record
                    await record("media_backpressure", workload="media")
                    raise ValueError("音频发送过快或网络拥塞，请重新开启麦克风") from exc
            elif message.get("text"):
                command = json.loads(message["text"])
                if not isinstance(command, dict):
                    raise ValueError("语音控制命令必须是 JSON 对象")
                if command.get("type") == "end":
                    await asyncio.wait_for(queue.put(None), timeout=2)
                    return
                if command.get("type") == "start":
                    await emit({"type": "status", "status": "started"})
                else:
                    raise ValueError("语音连接只支持 start/end 命令和 PCM 音频帧")

    async def provider_input() -> None:
        packet = first
        while True:
            update = assembler.apply(packet)
            if update is not None:
                await emit(update)
            if assembler.final:
                completed.set()
                await emit({"type": "status", "status": "completed"})
                return
            packet = decode_packet(await asyncio.wait_for(upstream.recv(), timeout=25))

    async def provider_output() -> None:
        next_send = time.monotonic()
        while not completed.is_set():
            chunk = await asyncio.wait_for(queue.get(), timeout=15)
            if chunk is None:
                await upstream.send(json.dumps({"end": True, "sessionId": provider_sid}))
                return
            delay = next_send - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            await upstream.send(chunk)
            next_send = time.monotonic() + len(chunk) / 32000

    async def check_identity() -> None:
        while not completed.is_set():
            await asyncio.sleep(auth_interval)
            await revalidate()

    browser = asyncio.create_task(browser_input())
    reader = asyncio.create_task(provider_input())
    writer = asyncio.create_task(provider_output())
    tasks.extend((browser, reader, writer))
    pending = {browser, reader, writer}
    if revalidate is not None:
        validator = asyncio.create_task(check_identity())
        tasks.append(validator)
        pending.add(validator)
    while reader in pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()  # Any disconnect/backpressure/provider error cancels all tasks.
        if reader in done:
            break


class TTSRequest(BaseModel):
    interview_id: str = Field(min_length=1, max_length=36)
    text: str = Field(min_length=1, max_length=100000)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("合成文本不能为空")
        return value.strip()


def _tts_json(task: MediaTask) -> dict:
    return {
        "id": task.id, "status": task.status,
        "audio_url": f"/api/media/tts/{task.id}/audio" if task.status == "completed" else None,
        "error": task.error,
    }


@router.post("/media/tts", dependencies=[Depends(check_csrf)], status_code=202)
async def create_tts(body: TTSRequest, user: User = Depends(current_user)) -> dict:
    app_id, api_key, api_secret = _require_speech()
    voice = getattr(settings, "XFYUN_TTS_VOICE", "x4_mingge")
    text_hash = hashlib.sha256(f"{voice}\0{body.text}".encode()).hexdigest()
    async with async_session_maker() as db:
        await owned_interview(db, body.interview_id, user.id, lock=True)
        task = (await db.execute(select(MediaTask).where(
            MediaTask.user_id == user.id, MediaTask.interview_id == body.interview_id,
            MediaTask.text_hash == text_hash,
        ))).scalar_one_or_none()
        if task is not None:
            if task.status != "failed":
                return _tts_json(task)
            # Only an explicit new POST retries a definite failure. A conditional
            # update allows one caller to claim it; unknown outcomes stay cached.
            claimed = await db.execute(update(MediaTask).where(
                MediaTask.id == task.id, MediaTask.status == "failed",
            ).values(status="creating", provider_task_id=None, audio_path=None, error=None,
                     created_at=utcnow()).execution_options(synchronize_session=False))
            await db.commit()
            await db.refresh(task)
            if claimed.rowcount != 1:
                return _tts_json(task)
        else:
            task = MediaTask(id=new_id(), user_id=user.id, interview_id=body.interview_id, text_hash=text_hash)
            db.add(task)
            try:
                await db.commit()  # Record intent before making an external chargeable call.
            except IntegrityError:
                await db.rollback()
                task = (await db.execute(select(MediaTask).where(
                    MediaTask.user_id == user.id, MediaTask.interview_id == body.interview_id,
                    MediaTask.text_hash == text_hash,
                ))).scalar_one()
                return _tts_json(task)
        try:
            result = await tts_request(TTS_CREATE, tts_create_body(app_id, body.text, voice, new_id()), api_key, api_secret)
            if not result["provider_task_id"]:
                raise ValueError("Provider task id missing")
            task.provider_task_id = result["provider_task_id"]
            task.status = "processing"
        except TTSProviderError as exc:
            task.status, task.error = "failed", str(exc)
        except Exception:
            task.status = "unknown"
            task.error = "合成请求结果未知，未自动重试以避免重复计费"
        await db.commit()
        return _tts_json(task)


@router.get("/media/tts/{task_id}")
async def query_tts(task_id: str, user: User = Depends(current_user)) -> dict:
    async with async_session_maker() as db:
        task = (await db.execute(select(MediaTask).where(
            MediaTask.id == task_id, MediaTask.user_id == user.id,
        ))).scalar_one_or_none()
        if task is None:
            raise HTTPException(404, "语音任务不存在")
        if task.status == "creating" and task.created_at < utcnow() - timedelta(seconds=45):
            task.status, task.error = "unknown", "合成请求被中断，结果未知；未自动重试"
            await db.commit()
        if task.status != "processing" or not await _try_lock(db, "tts-query", task.id):
            return _tts_json(task)
        # Deletion takes the same advisory lock. Re-read after acquiring it so
        # a query that waited behind deletion cannot recreate an orphan file.
        task = await db.scalar(select(MediaTask).where(
            MediaTask.id == task_id, MediaTask.user_id == user.id,
        ).execution_options(populate_existing=True))
        if task is None:
            raise HTTPException(404, "语音任务不存在")
        if task.status != "processing":
            return _tts_json(task)
        app_id, api_key, api_secret = _require_speech()
        try:
            result = await tts_request(TTS_QUERY, {"header": {
                "app_id": app_id, "task_id": task.provider_task_id,
            }}, api_key, api_secret)
            if result["status"] == "completed":
                destination = Path(getattr(settings, "MEDIA_STORAGE_DIR", "uploads/media")).resolve() / f"{task.id}.mp3"
                await download_audio(result["audio_url"], destination)
                task.audio_path, task.status, task.error = str(destination), "completed", None
            elif result["status"] == "failed":
                task.status, task.error = "failed", "语音合成任务失败"
            else:
                task.error = None
        except TTSProviderError as exc:
            task.status, task.error = "failed", str(exc)
        except Exception:
            # Queries/downloads are read-only and can safely be attempted again.
            task.error = "暂时无法查询或下载音频，请稍后重试"
        await db.commit()
        return _tts_json(task)


@router.get("/media/tts/{task_id}/audio")
async def tts_audio(task_id: str, user: User = Depends(current_user)) -> FileResponse:
    async with async_session_maker() as db:
        task = (await db.execute(select(MediaTask).where(
            MediaTask.id == task_id, MediaTask.user_id == user.id,
        ))).scalar_one_or_none()
        if task is None or task.status != "completed" or not task.audio_path:
            raise HTTPException(404, "音频不存在或尚未生成")
    root = Path(getattr(settings, "MEDIA_STORAGE_DIR", "uploads/media")).resolve()
    path = Path(task.audio_path).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "音频文件不存在")
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "private, no-store"})


def image_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise HTTPException(415, "摄像头帧仅支持 JPEG、PNG 或 WebP")


@router.post("/interviews/{interview_id}/vision", dependencies=[Depends(check_csrf)])
async def analyze_frame(interview_id: str, file: UploadFile = File(...),
                        user: User = Depends(current_user)) -> dict:
    try:
        data = await file.read(MAX_FRAME_BYTES + 1)
    finally:
        await file.close()
    if len(data) > MAX_FRAME_BYTES:
        raise HTTPException(413, "摄像头帧不得超过 1 MB")
    mime = image_mime(data)
    async with async_session_maker() as db:
        interview = await owned_interview(db, interview_id, user.id)
        config = interview.config_snapshot.get("vision") or {}
        if not config.get("enabled") or not config.get("user_provider_id"):
            return {"feedback": None, "available": False, "error": "本次面试未配置视觉模型"}
        if not await _try_lock(db, "vision", interview_id):
            raise HTTPException(409, "上一帧仍在分析中")
        await owned_interview(db, interview_id, user.id)
        latest = (await db.execute(select(VisionFeedback).where(
            VisionFeedback.interview_id == interview_id,
        ).order_by(VisionFeedback.created_at.desc()).limit(1))).scalar_one_or_none()
        if latest and latest.created_at > utcnow() - timedelta(seconds=5):
            raise HTTPException(429, "请每 5 秒提交一帧", headers={"Retry-After": "5"})
        prompt = (config.get("prompt", "") + "\n" +
            "你是面试表达教练。只描述单张图片可见的构图、光线、镜头高度和姿态，并给出最多两条简短建议。"
            "不要推断身份、情绪、性格、健康、能力或诚信，不打分。若图片不清晰或无人，明确说明。"
            "反馈不参与答题评分。用中文，100字以内。"
        )
        try:
            with call_scope(user.id, interview_id):
                feedback = await asyncio.wait_for(complete_vision(
                    {**config, "prompt": prompt}, f"data:{mime};base64,{base64.b64encode(data).decode()}",
                ), timeout=30)
            if not feedback:
                raise ValueError("Empty vision response")
        except PlatformError:
            raise
        except Exception:
            raise HTTPException(502, "视觉服务暂时不可用，文本和语音面试可继续") from None
        record = VisionFeedback(
            # Historical column name: personal runs identify the actual private connection.
            id=new_id(), interview_id=interview_id, user_id=user.id, provider_id=config["user_provider_id"],
            provider_version=str(config.get("provider_version", 0)), feedback=feedback,
        )
        db.add(record)
        await db.commit()
        return {"id": record.id, "feedback": feedback, "available": True}


@router.get("/interviews/{interview_id}/vision")
async def vision_history(interview_id: str, user: User = Depends(current_user)) -> list[dict]:
    async with async_session_maker() as db:
        await owned_interview(db, interview_id, user.id)
        rows = (await db.execute(select(VisionFeedback).where(
            VisionFeedback.interview_id == interview_id,
        ).order_by(VisionFeedback.created_at.desc()).limit(100))).scalars().all()
        return [{"id": row.id, "feedback": row.feedback, "created_at": row.created_at.isoformat()} for row in rows]
