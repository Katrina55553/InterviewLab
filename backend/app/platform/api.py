"""Thin authenticated REST and WebSocket adapters for the platform application service."""

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
)
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from app.core.database import async_session_maker
from app.platform.auth import check_csrf, current_user, websocket_user
from app.platform.contracts import PlatformError, error_payload, traced
from app.platform.models import Agent, Interview, Resume, ResumeDiagnosis, User, new_id
from app.platform.runtime import freeze_configs
from app.platform.service import (
    create_interview,
    delete_interview,
    delete_resume,
    events_after,
    execute_command,
    get_interview,
    interview_view,
    owned,
    queue_job,
    resume_view,
    retry_report,
    retry_resume,
    start_diagnosis,
)
from app.platform.service import (
    get_resume as read_resume,
)
from app.utils.security.file_upload import validate_upload_file

router = APIRouter(tags=["platform"])
ws_router = APIRouter()


class StartInterview(BaseModel):
    resume_id: str = Field(min_length=1, max_length=36)


class InterviewCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=128)
    type: Literal["start", "answer", "skip", "rephrase", "end"]
    expected_question_id: str | None = Field(default=None, max_length=36)
    version: int = Field(ge=0)
    content: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def nonempty_answer(self):
        if self.type == "answer":
            self.content = (self.content or "").strip()
            if not self.content:
                raise ValueError("回答不能为空")
        return self


@router.get("/resumes")
async def list_resumes(user: User = Depends(current_user)):
    async with async_session_maker() as db:
        rows = (await db.execute(select(Resume, ResumeDiagnosis).outerjoin(
            ResumeDiagnosis, ResumeDiagnosis.resume_id == Resume.id,
        ).where(Resume.user_id == user.id).order_by(Resume.created_at.desc()).limit(100))).all()
        return [resume_view(row, diagnosis) for row, diagnosis in rows]


@router.post("/resumes", status_code=201)
async def upload_resume(file: UploadFile = File(...), user: User = Depends(current_user)):
    await validate_upload_file(file)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix == ".doc":
        raise HTTPException(400, "请将旧版 DOC 转换为 DOCX、PDF 或 TXT 后上传")
    configs = await freeze_configs(user_id=user.id)
    resume_id = new_id()
    path = Path("uploads/platform") / user.id / (resume_id + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, await file.read())
    try:
        async with async_session_maker() as db, db.begin():
            row = Resume(id=resume_id, user_id=user.id, filename=Path(file.filename or "resume").name,
                         file_path=str(path), status="queued", config_snapshot=configs)
            db.add(row)
            job = queue_job(db, "prepare", resume_id)
            row.active_job_id = job.id
            await db.flush()
            return resume_view(row)
    except Exception:
        path.unlink(missing_ok=True)
        raise


@router.get("/resumes/{resume_id}")
async def get_resume(resume_id: str, user: User = Depends(current_user)):
    return await read_resume(user.id, resume_id)


@router.get("/resumes/{resume_id}/file")
async def download_resume(resume_id: str, user: User = Depends(current_user)):
    async with async_session_maker() as db:
        row = await owned(db, Resume, resume_id, user.id)
    if not Path(row.file_path).is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(row.file_path, filename=row.filename)


@router.delete("/resumes/{resume_id}", status_code=204, dependencies=[Depends(check_csrf)])
async def remove_resume(resume_id: str, user: User = Depends(current_user)):
    await delete_resume(user.id, resume_id)
    return Response(status_code=204)


@router.post("/resumes/{resume_id}/retry")
async def retry_preparation(resume_id: str, user: User = Depends(current_user)):
    return await retry_resume(user.id, resume_id)


@router.post("/resumes/{resume_id}/diagnosis")
async def diagnose_resume(resume_id: str, user: User = Depends(current_user)):
    return await start_diagnosis(user.id, resume_id)


@router.get("/interviews")
async def list_interviews(user: User = Depends(current_user)):
    async with async_session_maker() as db:
        rows = (await db.scalars(select(Interview).where(Interview.user_id == user.id).order_by(Interview.created_at.desc()).limit(100))).all()
        return [interview_view(row) for row in rows]


@router.post("/interviews", status_code=201)
async def new_interview(body: StartInterview, user: User = Depends(current_user)):
    return await create_interview(user.id, body.resume_id)


@router.get("/interviews/{interview_id}")
async def interview_snapshot(interview_id: str, user: User = Depends(current_user)):
    return await get_interview(user.id, interview_id)


@router.delete("/interviews/{interview_id}", status_code=204, dependencies=[Depends(check_csrf)])
async def remove_interview(interview_id: str, user: User = Depends(current_user)):
    await delete_interview(user.id, interview_id)
    return Response(status_code=204)


@router.post("/interviews/{interview_id}/commands")
async def command(interview_id: str, body: InterviewCommand, user: User = Depends(current_user)):
    return await execute_command(user.id, interview_id, body.model_dump())


@router.get("/interviews/{interview_id}/events")
async def event_history(interview_id: str, after: int = Query(default=0, ge=0), user: User = Depends(current_user)):
    return await events_after(user.id, interview_id, after)


@router.get("/interviews/{interview_id}/report")
async def report(interview_id: str, user: User = Depends(current_user)):
    snapshot = await get_interview(user.id, interview_id)
    if snapshot["report"] is None:
        raise HTTPException(409, snapshot["error"] or "报告尚未生成，请稍后重试")
    return snapshot["report"]


@router.post("/interviews/{interview_id}/report/retry")
async def regenerate_report(interview_id: str, user: User = Depends(current_user)):
    return await retry_report(user.id, interview_id)


@router.get("/agents")
async def available_agents(user: User = Depends(current_user)):
    async with async_session_maker() as db:
        rows = (await db.scalars(select(Agent).where(Agent.enabled.is_(True), (Agent.scene == "chat") | Agent.scene.is_(None)))).all()
        return [{"id": row.id, "name": row.name} for row in rows]


@ws_router.websocket("/ws/interviews/{interview_id}")
async def interview_socket(websocket: WebSocket, interview_id: str, after: int = 0):
    user = await websocket_user(websocket)
    try:
        await get_interview(user.id, interview_id)
    except HTTPException:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    cursor = max(after, 0)
    send_lock = asyncio.Lock()

    async def send(payload):
        async with send_lock:
            await websocket.send_json(payload)

    async def push():
        nonlocal cursor
        while True:
            # Revalidate revocation and ownership even when the socket stays open.
            identity = getattr(websocket.state, "platform_identity", None)
            if identity:
                del websocket.state.platform_identity
            await websocket_user(websocket)
            for event in await events_after(user.id, interview_id, cursor):
                await send(event)
                cursor = event["sequence"]
            await asyncio.sleep(1)

    async def receive():
        while True:
            try:
                data = await websocket.receive_json()
                if not isinstance(data, dict):
                    raise ValueError("消息必须是 JSON 对象")
                if data.get("type") == "ping":
                    await send({"type": "pong"})
                    continue
                body = InterviewCommand.model_validate(data)
                with traced(new=True, request_id=body.request_id, connection_id=getattr(websocket.state, "trace_id", new_id())) as context:
                    try:
                        result = await execute_command(user.id, interview_id, body.model_dump())
                    except (HTTPException, PlatformError) as exc:
                        await send({"type": "error", "status": getattr(exc, "status", getattr(exc, "status_code", 503)),
                                    **error_payload(exc)})
                        continue
                await send({"type": "command_result", "request_id": body.request_id, "trace_id": context["trace_id"], "snapshot": result})
            except ValidationError:
                await send({"type": "error", **error_payload(HTTPException(422, "命令格式无效"))})
            except (ValueError, json.JSONDecodeError):
                await send({"type": "error", **error_payload(HTTPException(422, "消息必须是 JSON 对象"))})
            except HTTPException as exc:
                await send({"type": "error", "status": exc.status_code, **error_payload(exc)})

    tasks = [asyncio.create_task(push()), asyncio.create_task(receive())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except WebSocketException as exc:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=exc.code, reason=exc.reason)
    except (WebSocketDisconnect, RuntimeError, HTTPException):
        pass
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, WebSocketException, RuntimeError):
                await task
