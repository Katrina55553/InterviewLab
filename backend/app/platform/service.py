"""Interview application service. Transport adapters never reconstruct domain state."""

import asyncio
import contextlib
import copy
import hashlib
import json
import logging
from datetime import timedelta

import anyio
from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.database import async_session_maker
from app.platform.coordination import singleflight
from app.platform.graph import interview_graph, preparation_graph, report_graph
from app.platform.models import Command, Event, Interview, Outbox, Resume, new_id, utcnow
from app.platform.runtime import AIError, call_scope, freeze_configs

logger = logging.getLogger(__name__)


async def owned(db, model, object_id: str, user_id: str, *, lock: bool = False):
    query = select(model).where(model.id == object_id, model.user_id == user_id)
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query)
    if not row:
        raise HTTPException(404, "记录不存在")
    return row


def resume_view(row: Resume) -> dict:
    return {"id": row.id, "filename": row.filename, "status": row.status, "parsed": row.parsed,
            "preparation": row.preparation, "error": row.error, "created_at": row.created_at.isoformat()}


async def _expire_job(db, aggregate) -> None:
    """Called with the aggregate locked; reads can surface an abandoned worker promptly."""
    if not aggregate.active_job_id:
        return
    job = await db.scalar(select(Outbox).where(Outbox.id == aggregate.active_job_id).with_for_update())
    if job and job.execution_status == "running" and job.lease_until and job.lease_until <= utcnow():
        job.execution_status, job.owner_token = "unknown", None
        job.error = "任务执行者租约已过期，调用结果未知；请确认后手动重试"
        aggregate.error = job.error
        if isinstance(aggregate, Resume):
            aggregate.status = "failed"


async def get_resume(user_id: str, resume_id: str) -> dict:
    async with async_session_maker() as db, db.begin():
        row = await owned(db, Resume, resume_id, user_id, lock=True)
        if row.status in ("queued", "processing"):
            await _expire_job(db, row)
        return resume_view(row)


def interview_view(row: Interview, pending: Command | None = None) -> dict:
    snapshot = row.snapshot or {}
    return {
        "id": row.id, "resume_id": row.resume_id, "status": row.status, "version": row.version,
        "current_question": snapshot.get("current_question"), "messages": snapshot.get("messages", []),
        "evaluations": snapshot.get("evaluations", []), "report": row.report, "error": row.error,
        "demo": snapshot.get("demo", False), "created_at": row.created_at.isoformat(),
        "pending_command": ({"request_id": pending.request_id, "status": pending.status,
                             "error": pending.error, **(pending.result or {}).get("input", {})} if pending else None),
    }


async def _event(db, interview: Interview, event_type: str) -> dict:
    sequence = (await db.scalar(select(func.max(Event.sequence)).where(Event.interview_id == interview.id)) or 0) + 1
    event = Event(id=new_id(), interview_id=interview.id, sequence=sequence,
                  payload={"type": event_type, "snapshot": interview_view(interview)})
    db.add(event)
    await db.flush()
    return {"id": event.id, "sequence": event.sequence, "payload": event.payload}


def queue_job(db, kind: str, aggregate_id: str) -> Outbox:
    job = Outbox(id=new_id(), kind=kind, aggregate_id=aggregate_id, payload={}, status="pending", execution_status="queued")
    db.add(job)
    return job


async def create_interview(user_id: str, resume_id: str) -> dict:
    # Preparation retains its own immutable generation configuration. A new attempt
    # freezes the CURRENT interview/vision configuration, not the resume's old one.
    configs = await freeze_configs()
    async with async_session_maker() as db, db.begin():
        resume = await owned(db, Resume, resume_id, user_id)
        if resume.status != "ready":
            raise HTTPException(409, "请等待简历准备完成")
        preparation = resume.preparation
        interview = Interview(
            id=new_id(), user_id=user_id, resume_id=resume.id, status="ready", version=0,
            config_snapshot=configs,
            snapshot={"questions": copy.deepcopy(preparation["questions"]), "parsed": resume.parsed,
                      "resume_score": preparation["resume_score"], "demo": preparation["demo"],
                      "messages": [], "evaluations": [], "main_index": -1, "follow_up_count": 0,
                      "current_question": None, "status": "ready"},
        )
        db.add(interview)
        await db.flush()
        await _event(db, interview, "created")
        return interview_view(interview)


async def get_interview(user_id: str, interview_id: str) -> dict:
    async with async_session_maker() as db, db.begin():
        row = await owned(db, Interview, interview_id, user_id, lock=True)
        if row.status == "reporting":
            await _expire_job(db, row)
        pending = await db.scalar(select(Command).where(
            Command.interview_id == interview_id, Command.status != "completed",
        ).order_by(Command.created_at.desc()).limit(1).with_for_update())
        if pending and (pending.result or {}).get("input", {}).get("version") != row.version:
            pending = None
        if pending and pending.status == "running" and pending.lease_until < utcnow():
            pending.status = "unknown"
            pending.error = "执行租约已过期，调用结果未知；可确认后重新提交"
            pending.owner_token = None
        return interview_view(row, pending)


async def _heartbeat(model, object_id: str, owner: str, *, interval: float = 20) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            async with async_session_maker() as db, db.begin():
                where = [model.id == object_id, model.owner_token == owner, model.lease_until > utcnow()]
                where.append(model.status == "running" if model is Command else model.execution_status == "running")
                result = await db.execute(update(model).where(*where).values(
                    lease_until=utcnow() + timedelta(seconds=settings.AI_COMMAND_LEASE_SECONDS),
                ))
                if not result.rowcount:
                    return
        except Exception:
            logger.warning("job heartbeat unavailable; fenced commit remains required", exc_info=True)


async def execute_command(user_id: str, interview_id: str, payload: dict) -> dict:
    """Reserve -> run outside transaction -> fenced commit. A request never spans AI with a SQL lock."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    owner = new_id()
    async with async_session_maker() as db, db.begin():
        interview = await owned(db, Interview, interview_id, user_id, lock=True)
        duplicate = await db.scalar(select(Command).where(
            Command.interview_id == interview_id, Command.request_id == payload["request_id"],
        ))
        if duplicate:
            if duplicate.input_hash != digest:
                raise HTTPException(409, "同一 request_id 不能用于不同命令")
            if duplicate.status == "completed":
                return copy.deepcopy(duplicate.result)
            if duplicate.status == "running" and duplicate.lease_until > utcnow():
                return interview_view(interview, duplicate)
            if duplicate.status == "running":
                duplicate.status, duplicate.owner_token = "unknown", None
                duplicate.error = "调用结果未知，请使用新请求 ID 显式重试"
            return interview_view(interview, duplicate)
        running = list((await db.scalars(select(Command).where(
            Command.interview_id == interview_id, Command.status == "running",
        ))).all())
        for command in running:
            if command.lease_until > utcnow():
                raise HTTPException(409, "当前命令正在执行，请等待或刷新状态")
            command.status, command.owner_token = "unknown", None
            command.error = "执行者租约过期，已阻止过期结果写入"
        if interview.version != payload["version"]:
            raise HTTPException(409, "面试状态已更新，请刷新后操作")
        action = payload["type"]
        current = (interview.snapshot or {}).get("current_question")
        if action == "start":
            if interview.status != "ready":
                raise HTTPException(409, "面试已经开始")
        elif action == "end":
            if interview.status not in ("ready", "active"):
                raise HTTPException(409, "面试已结束")
        elif interview.status != "active" or not current or current["id"] != payload.get("expected_question_id"):
            raise HTTPException(409, "问题已变化，请刷新后操作")
        checkpoint = None
        prior = await db.scalar(select(Command).where(
            Command.interview_id == interview_id, Command.status.in_(("failed", "unknown")),
        ).order_by(Command.created_at.desc()).limit(1))
        if prior:
            prior_input = (prior.result or {}).get("input", {})
            if {k: v for k, v in prior_input.items() if k != "request_id"} == {k: v for k, v in payload.items() if k != "request_id"}:
                checkpoint = copy.deepcopy((prior.result or {}).get("checkpoint"))
        command = Command(id=new_id(), interview_id=interview.id, request_id=payload["request_id"],
                          input_hash=digest, status="running", owner_token=owner,
                          lease_until=utcnow() + timedelta(seconds=settings.AI_COMMAND_LEASE_SECONDS),
                          result={"input": payload, "checkpoint": checkpoint})
        db.add(command)
        await db.flush()
        command_id = command.id
        snapshot, configs = copy.deepcopy(interview.snapshot), copy.deepcopy(interview.config_snapshot)
    heartbeat = asyncio.create_task(_heartbeat(Command, command_id, owner))
    try:
        with call_scope(user_id, interview_id):
            async with singleflight(f"interview:{interview_id}:{payload['request_id']}", owner):
                result = {"snapshot": snapshot, "configs": configs, "command": payload, "resume_from": "collect"}
                if checkpoint:
                    result.update(checkpoint)
                async for updates in interview_graph.astream(result):
                    for node, fields in updates.items():
                        result.update(fields)
                        resume_from = ({"done": "finished", "rephrase": "followup", "evaluate": "evaluate"}[result["next"]]
                                       if node == "collect" else result["next"] if node == "evaluate" else "finished")
                        saved = {"snapshot": copy.deepcopy(result["snapshot"]), "next": result.get("next"), "resume_from": resume_from}
                        async with async_session_maker() as db, db.begin():
                            row = await owned(db, Interview, interview_id, user_id, lock=True)
                            running = await db.scalar(select(Command).where(Command.id == command_id).with_for_update())
                            if (running.owner_token != owner or running.status != "running" or running.lease_until <= utcnow()
                                    or row.version != payload["version"]):
                                raise HTTPException(409, "执行租约已过期，停止后续节点")
                            running.result = {"input": payload, "checkpoint": saved}
        async with async_session_maker() as db, db.begin():
            interview = await owned(db, Interview, interview_id, user_id, lock=True)
            command = await db.scalar(select(Command).where(Command.id == command_id).with_for_update())
            if (command.status != "running" or command.owner_token != owner or command.lease_until <= utcnow()
                    or interview.version != payload["version"]):
                raise HTTPException(409, "执行租约或状态已变化，过期结果未写入")
            interview.snapshot = result["snapshot"]
            interview.status = result["snapshot"]["status"]
            interview.version += 1
            interview.error = None
            interview.updated_at = utcnow()
            if interview.status == "reporting":
                job = queue_job(db, "report", interview.id)
                interview.active_job_id = job.id
            await db.flush()
            view = interview_view(interview)
            command.status, command.result = "completed", view
            await _event(db, interview, "complete" if interview.status == "reporting" else "question")
            return view
    except (Exception, asyncio.CancelledError) as exc:
        with anyio.CancelScope(shield=True):
            async with async_session_maker() as db, db.begin():
                row = await owned(db, Interview, interview_id, user_id, lock=True)
                command = await db.scalar(select(Command).where(Command.id == command_id).with_for_update())
                if (command and command.owner_token == owner and command.status == "running"
                        and command.lease_until > utcnow() and row.version == payload["version"]):
                    command.status = "unknown" if isinstance(exc, asyncio.CancelledError) or (isinstance(exc, AIError) and exc.unknown) else "failed"
                    command.error = ("连接中断，调用结果未知；答案已保留，请确认后重试" if isinstance(exc, asyncio.CancelledError)
                                     else str(exc.detail) if isinstance(exc, HTTPException) else str(exc) if isinstance(exc, AIError) else "执行失败，答案已保留")
                    command.owner_token = None
        if isinstance(exc, asyncio.CancelledError):
            raise
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(503, str(exc) if isinstance(exc, AIError) else "执行失败，答案已保留，可重试") from exc
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat


async def events_after(user_id: str, interview_id: str, after: int) -> list[dict]:
    async with async_session_maker() as db:
        await owned(db, Interview, interview_id, user_id)
        rows = (await db.scalars(select(Event).where(Event.interview_id == interview_id, Event.sequence > after)
                                 .order_by(Event.sequence).limit(100))).all()
        return [{"id": e.id, "sequence": e.sequence, "payload": e.payload} for e in rows]


async def retry_resume(user_id: str, resume_id: str) -> dict:
    configs = await freeze_configs()
    async with async_session_maker() as db, db.begin():
        row = await owned(db, Resume, resume_id, user_id, lock=True)
        if row.status not in ("failed", "processing", "queued"):
            raise HTTPException(409, "简历已准备完成")
        previous = await db.get(Outbox, row.active_job_id) if row.active_job_id else None
        if previous and previous.execution_status == "running" and previous.lease_until > utcnow():
            raise HTTPException(409, "准备任务仍在执行")
        if previous and previous.execution_status == "queued":
            raise HTTPException(409, "准备任务正在等待 Worker，请检查任务服务")
        if previous:
            previous.owner_token = None
        row.status, row.error, row.config_snapshot = "queued", None, configs
        job = queue_job(db, "prepare", row.id)
        row.active_job_id = job.id
        return resume_view(row)


async def retry_report(user_id: str, interview_id: str) -> dict:
    async with async_session_maker() as db, db.begin():
        row = await owned(db, Interview, interview_id, user_id, lock=True)
        await _expire_job(db, row)
        if row.status != "reporting" or not row.error:
            raise HTTPException(409, "报告未失败或已完成")
        old = await db.get(Outbox, row.active_job_id) if row.active_job_id else None
        if old:
            old.owner_token = None
        row.error = None
        job = queue_job(db, "report", row.id)
        row.active_job_id = job.id
        await _event(db, row, "status")
        return interview_view(row)


async def run_job(job_id: str) -> None:
    """Idempotent Celery entrypoint. Unknown provider outcomes require explicit retry."""
    owner = new_id()
    async with async_session_maker() as db, db.begin():
        job = await db.get(Outbox, job_id)
        if not job or job.execution_status in ("completed", "failed", "unknown"):
            return
        model = Resume if job.kind == "prepare" else Interview
        aggregate = await db.scalar(select(model).where(model.id == job.aggregate_id).with_for_update())
        job = await db.scalar(select(Outbox).where(Outbox.id == job_id).with_for_update().execution_options(populate_existing=True))
        if job.execution_status in ("completed", "failed", "unknown"):
            return
        if not aggregate or aggregate.active_job_id != job.id:
            return
        if job.execution_status == "running":
            if job.lease_until > utcnow():
                return
            job.execution_status, job.owner_token = "unknown", None
            aggregate.error = "任务执行者退出，调用结果未知，请手动重试"
            if model is Resume:
                aggregate.status = "failed"
            return
        job.execution_status, job.owner_token = "running", owner
        job.lease_until = utcnow() + timedelta(seconds=settings.AI_COMMAND_LEASE_SECONDS)
        if model is Resume:
            aggregate.status = "processing"
            filename, configs = aggregate.file_path, copy.deepcopy(aggregate.config_snapshot)
        else:
            snapshot, configs = copy.deepcopy(aggregate.snapshot), copy.deepcopy(aggregate.config_snapshot)
        aggregate_id, kind, user_id = aggregate.id, job.kind, aggregate.user_id
    heartbeat = asyncio.create_task(_heartbeat(Outbox, job_id, owner))
    try:
        if kind == "prepare":
            from app.core.documents import extract_text, mask_raw_text

            raw = mask_raw_text(await asyncio.to_thread(extract_text, filename))
            if len(raw.strip()) < 20:
                raise AIError("未提取到足够文本，请上传可提取文字的 PDF、DOCX 或 TXT")
            with call_scope(user_id):
                async with singleflight(f"prepare:{job_id}", owner):
                    result = await preparation_graph.ainvoke({"raw_text": raw, "configs": configs, "demo": settings.PLATFORM_DEMO_MODE})
        else:
            with call_scope(user_id, aggregate_id):
                async with singleflight(f"report:{job_id}", owner):
                    result = await report_graph.ainvoke({"snapshot": snapshot, "configs": configs})
        async with async_session_maker() as db, db.begin():
            aggregate = await db.scalar(select(model).where(model.id == aggregate_id).with_for_update())
            job = await db.scalar(select(Outbox).where(Outbox.id == job_id).with_for_update())
            if (job.owner_token != owner or job.execution_status != "running" or job.lease_until <= utcnow()
                    or aggregate.active_job_id != job.id):
                return
            if kind == "prepare":
                aggregate.raw_text, aggregate.parsed = raw, result["parsed"]
                aggregate.preparation, aggregate.status, aggregate.error = result["preparation"], "ready", None
            else:
                aggregate.report, aggregate.status, aggregate.error = result["preparation"], "completed", None
                aggregate.version += 1
                await _event(db, aggregate, "complete")
            job.execution_status = "completed"
    except Exception as exc:
        logger.warning("platform job failed kind=%s error_type=%s", kind, type(exc).__name__)
        async with async_session_maker() as db, db.begin():
            aggregate = await db.scalar(select(model).where(model.id == aggregate_id).with_for_update())
            job = await db.scalar(select(Outbox).where(Outbox.id == job_id).with_for_update())
            if (job and job.owner_token == owner and job.execution_status == "running" and job.lease_until > utcnow()
                    and aggregate and aggregate.active_job_id == job.id):
                job.execution_status = "unknown" if isinstance(exc, AIError) and exc.unknown else "failed"
                job.error = str(exc) if isinstance(exc, AIError) else "任务执行失败，请检查日志后重试"
                aggregate.error = job.error
                if kind == "prepare":
                    aggregate.status = "failed"
                else:
                    await _event(db, aggregate, "error")
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
