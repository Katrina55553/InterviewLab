"""Interview application service. Transport adapters never reconstruct domain state."""

import asyncio
import contextlib
import copy
import hashlib
import json
import logging
from datetime import timedelta
from pathlib import Path

import anyio
from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, update

from app.core.config import settings
from app.core.database import async_session_maker
from app.platform.ai.guard import database_now
from app.platform.contracts import PlatformError, error_payload, trace_id
from app.platform.coordination import singleflight
from app.platform.domain.state import read_snapshot, validate_command
from app.platform.graph import diagnosis_graph, interview_graph, parsing_graph, report_graph
from app.platform.media_locks import try_media_lock
from app.platform.media_models import MediaTask
from app.platform.models import Command, Event, Interview, Outbox, Resume, ResumeDiagnosis, new_id, utcnow
from app.platform.operations.faults import checkpoint
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


def resume_view(row: Resume, diagnosis: ResumeDiagnosis | None = None) -> dict:
    return {"id": row.id, "filename": row.filename, "status": row.status, "parsed": row.parsed,
            "demo": row.demo, "diagnosis": ({"id": diagnosis.id, "status": diagnosis.status,
                "result": diagnosis.result, "error": diagnosis.error} if diagnosis else None),
            "error": row.error, "created_at": row.created_at.isoformat()}


async def _expire_job(db, aggregate) -> None:
    """Called with the aggregate locked; reads can surface an abandoned worker promptly."""
    if not aggregate.active_job_id:
        return
    job = await db.scalar(select(Outbox).where(Outbox.id == aggregate.active_job_id).with_for_update())
    if job and job.execution_status == "running" and job.lease_until and job.lease_until <= await database_now(db):
        job.execution_status, job.owner_token = "unknown", None
        job.finished_at = await database_now(db)
        job.error = "任务执行者租约已过期，调用结果未知；请确认后手动重试"
        aggregate.error = job.error
        if isinstance(aggregate, (Resume, ResumeDiagnosis)):
            aggregate.status = "failed"


async def get_resume(user_id: str, resume_id: str) -> dict:
    async with async_session_maker() as db, db.begin():
        row = await owned(db, Resume, resume_id, user_id, lock=True)
        if row.status in ("queued", "processing"):
            await _expire_job(db, row)
        diagnosis = await db.scalar(select(ResumeDiagnosis).where(ResumeDiagnosis.resume_id == row.id).with_for_update())
        if diagnosis and diagnosis.status in ("queued", "processing"):
            await _expire_job(db, diagnosis)
        return resume_view(row, diagnosis)


async def _delete_jobs(db, condition) -> None:
    # Aggregate locks prevent new claims; job locks also serialize heartbeats.
    jobs = list((await db.scalars(select(Outbox).where(condition).with_for_update())).all())
    now = await database_now(db)
    if any(job.execution_status == "running" and job.lease_until and job.lease_until > now for job in jobs):
        raise HTTPException(409, "后台任务仍在执行，请稍后再删除")
    await db.execute(delete(Outbox).where(condition))


async def _remove_files(files: list[tuple[str, Path]]) -> None:
    for filename, root in files:
        try:
            path = Path(filename).resolve()
            if path.parent != root.resolve():
                logger.warning("refused deletion outside private storage")
                continue
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            logger.warning("private file cleanup failed", exc_info=True)


async def delete_resume(user_id: str, resume_id: str) -> None:
    async with async_session_maker() as db, db.begin():
        row = await owned(db, Resume, resume_id, user_id, lock=True)
        if await db.scalar(select(Interview.id).where(Interview.resume_id == row.id).limit(1)):
            raise HTTPException(409, "这份简历有关联面试，请先在面试记录中删除相关面试")
        diagnoses = list((await db.scalars(select(ResumeDiagnosis).where(
            ResumeDiagnosis.resume_id == row.id,
        ).with_for_update())).all())
        await _delete_jobs(db, or_(
            (Outbox.kind == "prepare") & (Outbox.aggregate_id == row.id),
            (Outbox.kind == "diagnose") & (Outbox.aggregate_id.in_([item.id for item in diagnoses])),
        ))
        filename = row.file_path
        await db.delete(row)
    await _remove_files([(filename, Path("uploads/platform") / user_id)])


async def delete_interview(user_id: str, interview_id: str) -> None:
    async with async_session_maker() as db, db.begin():
        row = await owned(db, Interview, interview_id, user_id, lock=True)
        commands = list((await db.scalars(select(Command).where(
            Command.interview_id == row.id, Command.status == "running",
        ).with_for_update())).all())
        now = await database_now(db)
        if any(command.lease_until and command.lease_until > now for command in commands):
            raise HTTPException(409, "面试命令仍在执行，请稍后再删除")
        tasks = list((await db.scalars(select(MediaTask).where(MediaTask.interview_id == row.id))).all())
        if any(task.status == "creating" and task.created_at > now - timedelta(seconds=45) for task in tasks):
            raise HTTPException(409, "语音任务仍在创建，请稍后再删除")
        if db.bind.dialect.name == "postgresql":
            if not await try_media_lock(db, "vision", row.id):
                raise HTTPException(409, "仪态分析仍在执行，请稍后再删除")
            for task in tasks:
                if not await try_media_lock(db, "tts-query", task.id):
                    raise HTTPException(409, "语音任务仍在执行，请稍后再删除")
        await _delete_jobs(db, (Outbox.kind == "report") & (Outbox.aggregate_id == row.id))
        files = [(task.audio_path, Path(settings.MEDIA_STORAGE_DIR)) for task in tasks if task.audio_path]
        await db.delete(row)
    await _remove_files(files)


def interview_view(row: Interview, pending: Command | None = None) -> dict:
    snapshot = read_snapshot(row.snapshot or {})
    return {
        "id": row.id, "resume_id": row.resume_id, "status": row.status, "version": row.version,
        "current_question": snapshot.get("current_question"), "messages": snapshot.get("messages", []),
        "evaluations": snapshot.get("evaluations", []), "report": row.report, "error": row.error,
        "demo": snapshot.get("demo", False), "created_at": row.created_at.isoformat(),
        "pending_command": ({"request_id": pending.request_id, "status": pending.status,
                             "error": pending.error, "error_contract": (pending.result or {}).get("error_contract"), **(pending.result or {}).get("input", {})} if pending else None),
    }


async def _event(db, interview: Interview, event_type: str) -> dict:
    sequence = (await db.scalar(select(func.max(Event.sequence)).where(Event.interview_id == interview.id)) or 0) + 1
    event = Event(id=new_id(), interview_id=interview.id, sequence=sequence,
                  payload={"type": event_type, "trace_id": trace_id(), "snapshot": interview_view(interview)})
    db.add(event)
    await db.flush()
    return {"id": event.id, "sequence": event.sequence, "payload": event.payload}


def queue_job(db, kind: str, aggregate_id: str) -> Outbox:
    job = Outbox(id=new_id(), kind=kind, aggregate_id=aggregate_id, payload={}, trace_id=trace_id(), status="pending", execution_status="queued")
    db.add(job)
    return job


async def create_interview(user_id: str, resume_id: str) -> dict:
    # Interviews freeze their own configuration and depend only on parsed facts.
    configs = await freeze_configs(user_id=user_id)
    async with async_session_maker() as db, db.begin():
        resume = await owned(db, Resume, resume_id, user_id, lock=True)
        if resume.status != "ready":
            raise HTTPException(409, "请等待简历解析完成")
        interview = Interview(
            id=new_id(), user_id=user_id, resume_id=resume.id, status="ready", version=0,
            config_snapshot=configs,
            snapshot={"schema_version": 1, "questions": [], "parsed": resume.parsed,
                      "demo": resume.demo,
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
        if pending and pending.status == "running" and pending.lease_until < await database_now(db):
            pending.status = "unknown"
            pending.error = "执行租约已过期，调用结果未知；可确认后重新提交"
            pending.owner_token = None
        return interview_view(row, pending)


async def assert_execution(model, object_id, owner):
    async with async_session_maker() as db:
        now = await database_now(db)
        row = await db.get(model, object_id)
        status = row.status if model is Command and row else row.execution_status if row else None
        if not row or row.owner_token != owner or status != "running" or row.lease_until <= now:
            raise PlatformError("STATE_CONFLICT", "执行租约已过期，停止后续调用", status=409, retry_policy="never")


async def _heartbeat(model, object_id: str, owner: str, *, interval: float = 20) -> None:
    while True:
        await asyncio.sleep(min(interval, max(1, settings.AI_COMMAND_LEASE_SECONDS / 3)))
        try:
            async with async_session_maker() as db, db.begin():
                where = [model.id == object_id, model.owner_token == owner, model.lease_until > await database_now(db)]
                where.append(model.status == "running" if model is Command else model.execution_status == "running")
                result = await db.execute(update(model).where(*where).values(
                    lease_until=await database_now(db) + timedelta(seconds=settings.AI_COMMAND_LEASE_SECONDS),
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
                raise HTTPException(409, error_payload(PlatformError("IDEMPOTENCY_MISMATCH", "同一 request_id 不能用于不同命令", status=409, retry_policy="never")))
            if duplicate.status == "completed":
                return copy.deepcopy(duplicate.result)
            if duplicate.status == "running" and duplicate.lease_until > await database_now(db):
                return interview_view(interview, duplicate)
            if duplicate.status == "running":
                duplicate.status, duplicate.owner_token = "unknown", None
                duplicate.error = "调用结果未知，请使用新请求 ID 显式重试"
            return interview_view(interview, duplicate)
        running = list((await db.scalars(select(Command).where(
            Command.interview_id == interview_id, Command.status == "running",
        ))).all())
        for command in running:
            if command.lease_until > await database_now(db):
                raise HTTPException(409, "当前命令正在执行，请等待或刷新状态")
            command.status, command.owner_token = "unknown", None
            command.error = "执行者租约过期，已阻止过期结果写入"
        if interview.version != payload["version"]:
            raise HTTPException(409, "面试状态已更新，请刷新后操作")
        try:
            validate_command(interview.status, interview.snapshot or {}, payload)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        checkpoint = None
        operation_id, generation = new_id(), 0
        prior = await db.scalar(select(Command).where(
            Command.interview_id == interview_id, Command.status.in_(("failed", "unknown")),
        ).order_by(Command.created_at.desc()).limit(1))
        if prior:
            prior_input = (prior.result or {}).get("input", {})
            if {k: v for k, v in prior_input.items() if k != "request_id"} == {k: v for k, v in payload.items() if k != "request_id"}:
                checkpoint = copy.deepcopy((prior.result or {}).get("checkpoint"))
                operation_id = (prior.result or {}).get("operation_id", prior.id)
                generation = (prior.result or {}).get("generation", 0) + 1
        command = Command(id=new_id(), interview_id=interview.id, request_id=payload["request_id"],
                          input_hash=digest, status="running", owner_token=owner,
                          lease_until=await database_now(db) + timedelta(seconds=settings.AI_COMMAND_LEASE_SECONDS),
                          result={"input": payload, "checkpoint": checkpoint, "operation_id": operation_id, "generation": generation, "trace_id": trace_id()})
        db.add(command)
        await db.flush()
        command_id = command.id
        snapshot, configs = copy.deepcopy(interview.snapshot), copy.deepcopy(interview.config_snapshot)
    heartbeat = asyncio.create_task(_heartbeat(Command, command_id, owner))
    try:
        with call_scope(user_id, interview_id, resource_id=interview_id, operation_id=operation_id,
                        generation=generation, snapshot_version=payload["version"], command_id=command_id, request_id=payload["request_id"],
                        fence=lambda: assert_execution(Command, command_id, owner)):
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
                            if (running.owner_token != owner or running.status != "running" or running.lease_until <= await database_now(db)
                                    or row.version != payload["version"]):
                                raise HTTPException(409, "执行租约已过期，停止后续节点")
                            running.result = {"input": payload, "checkpoint": saved, "operation_id": operation_id,
                                              "generation": generation, "trace_id": trace_id()}
        async with async_session_maker() as db, db.begin():
            interview = await owned(db, Interview, interview_id, user_id, lock=True)
            command = await db.scalar(select(Command).where(Command.id == command_id).with_for_update())
            if (command.status != "running" or command.owner_token != owner or command.lease_until <= await database_now(db)
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
                row = await db.scalar(select(Interview).where(Interview.id == interview_id).with_for_update())
                command = await db.scalar(select(Command).where(Command.id == command_id).with_for_update())
                if (row and command and command.owner_token == owner and command.status == "running"
                        and command.lease_until > await database_now(db) and row.version == payload["version"]):
                    command.status = "unknown" if isinstance(exc, asyncio.CancelledError) or (isinstance(exc, PlatformError) and exc.unknown) else "failed"
                    command.error = ("连接中断，调用结果未知；答案已保留，请确认后重试" if isinstance(exc, asyncio.CancelledError)
                                     else str(exc.detail) if isinstance(exc, HTTPException) else str(exc) if isinstance(exc, PlatformError) else "执行失败，答案已保留")
                    command.result = {**(command.result or {}), "error_contract": error_payload(exc)}
                    command.owner_token = None
        if isinstance(exc, asyncio.CancelledError):
            raise
        if isinstance(exc, PlatformError):
            raise HTTPException(exc.status, error_payload(exc)) from exc
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(503, str(exc) if isinstance(exc, PlatformError) else "执行失败，答案已保留，可重试") from exc
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
    configs = await freeze_configs(user_id=user_id)
    async with async_session_maker() as db, db.begin():
        row = await owned(db, Resume, resume_id, user_id, lock=True)
        if row.status not in ("failed", "processing", "queued"):
            raise HTTPException(409, "简历已准备完成")
        previous = await db.get(Outbox, row.active_job_id) if row.active_job_id else None
        if previous and previous.execution_status == "running" and previous.lease_until > await database_now(db):
            raise HTTPException(409, "准备任务仍在执行")
        if previous and previous.execution_status == "queued":
            raise HTTPException(409, "准备任务正在等待 Worker，请检查任务服务")
        if previous:
            previous.owner_token = None
        row.status, row.error, row.config_snapshot = "queued", None, configs
        job = queue_job(db, "prepare", row.id)
        if previous:
            job.payload = {"operation_id": (previous.payload or {}).get("operation_id", previous.id),
                           "generation": (previous.payload or {}).get("generation", 0) + 1}
        row.active_job_id = job.id
        return resume_view(row)


async def start_diagnosis(user_id: str, resume_id: str) -> dict:
    configs = await freeze_configs(user_id=user_id)
    async with async_session_maker() as db, db.begin():
        # The resume lock serializes initial creation as well as duplicate POSTs.
        resume = await owned(db, Resume, resume_id, user_id, lock=True)
        if resume.status != "ready":
            raise HTTPException(409, "请等待简历解析完成后再诊断")
        diagnosis = await db.scalar(select(ResumeDiagnosis).where(ResumeDiagnosis.resume_id == resume_id).with_for_update())
        if diagnosis:
            await _expire_job(db, diagnosis)
            if diagnosis.status in ("queued", "processing", "ready"):
                return resume_view(resume, diagnosis)
        else:
            diagnosis = ResumeDiagnosis(id=new_id(), resume_id=resume_id, user_id=user_id)
            db.add(diagnosis)
        previous = await db.get(Outbox, diagnosis.active_job_id) if diagnosis.active_job_id else None
        diagnosis.status, diagnosis.error, diagnosis.config_snapshot = "queued", None, configs
        job = queue_job(db, "diagnose", diagnosis.id)
        if previous:
            previous.owner_token = None
            job.payload = {"operation_id": (previous.payload or {}).get("operation_id", previous.id),
                           "generation": (previous.payload or {}).get("generation", 0) + 1}
        diagnosis.active_job_id = job.id
        await db.flush()
        return resume_view(resume, diagnosis)


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
        if old:
            job.payload = {"operation_id": (old.payload or {}).get("operation_id", old.id),
                           "generation": (old.payload or {}).get("generation", 0) + 1}
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
        if job.kind in {"probe:default", "probe:prepare", "probe:report"}:
            job.execution_status = "completed"
            return
        model = {"prepare": Resume, "diagnose": ResumeDiagnosis, "report": Interview}[job.kind]
        aggregate = await db.scalar(select(model).where(model.id == job.aggregate_id).with_for_update())
        job = await db.scalar(select(Outbox).where(Outbox.id == job_id).with_for_update().execution_options(populate_existing=True))
        if not job or job.execution_status in ("completed", "failed", "unknown"):
            return
        if not aggregate or aggregate.active_job_id != job.id:
            return
        if job.execution_status == "running":
            if job.lease_until > await database_now(db):
                return
            job.execution_status, job.owner_token = "unknown", None
            aggregate.error = "任务执行者退出，调用结果未知，请手动重试"
            if model in (Resume, ResumeDiagnosis):
                aggregate.status = "failed"
            return
        job.execution_status, job.owner_token = "running", owner
        job.started_at = await database_now(db)
        job.lease_until = await database_now(db) + timedelta(seconds=settings.AI_COMMAND_LEASE_SECONDS)
        if model is Resume:
            aggregate.status = "processing"
            filename, configs = aggregate.file_path, copy.deepcopy(aggregate.config_snapshot)
            demo = settings.PLATFORM_DEMO_MODE
        elif model is ResumeDiagnosis:
            aggregate.status = "processing"
            resume = await db.get(Resume, aggregate.resume_id)
            parsed, demo = copy.deepcopy(resume.parsed), resume.demo
            configs = copy.deepcopy(aggregate.config_snapshot)
        else:
            snapshot, configs = copy.deepcopy(aggregate.snapshot), copy.deepcopy(aggregate.config_snapshot)
        aggregate_id, kind, user_id = aggregate.id, job.kind, aggregate.user_id
        job_context = {"resource_id": aggregate.id, "operation_id": (job.payload or {}).get("operation_id", job.id),
                       "generation": (job.payload or {}).get("generation", 0), "snapshot_version": 1,
                       "fence": lambda: assert_execution(Outbox, job_id, owner)}
    heartbeat = asyncio.create_task(_heartbeat(Outbox, job_id, owner))
    try:
        await checkpoint("after_job_claim")
        if kind == "prepare":
            from app.core.documents import mask_raw_text
            from app.platform.operations.parsing import parse_document

            raw = mask_raw_text(await parse_document(filename))
            if len(raw.strip()) < 20:
                raise AIError("未提取到足够文本，请上传可提取文字的 PDF、DOCX 或 TXT")
            with call_scope(user_id, **job_context):
                async with singleflight(f"prepare:{job_id}", owner):
                    result = await parsing_graph.ainvoke({"raw_text": raw, "configs": configs, "demo": demo})
        elif kind == "diagnose":
            with call_scope(user_id, **job_context):
                async with singleflight(f"diagnose:{job_id}", owner):
                    result = await diagnosis_graph.ainvoke({"parsed": parsed, "configs": configs, "demo": demo})
        else:
            with call_scope(user_id, aggregate_id, **job_context):
                async with singleflight(f"report:{job_id}", owner):
                    result = await report_graph.ainvoke({"snapshot": snapshot, "configs": configs})
        async with async_session_maker() as db, db.begin():
            aggregate = await db.scalar(select(model).where(model.id == aggregate_id).with_for_update())
            job = await db.scalar(select(Outbox).where(Outbox.id == job_id).with_for_update())
            if (not aggregate or not job or job.owner_token != owner or job.execution_status != "running" or job.lease_until <= await database_now(db)
                    or aggregate.active_job_id != job.id):
                return
            if kind == "prepare":
                aggregate.raw_text, aggregate.parsed = raw, result["parsed"]
                aggregate.demo, aggregate.status, aggregate.error = demo, "ready", None
            elif kind == "diagnose":
                aggregate.result, aggregate.status, aggregate.error = result["diagnosis"], "ready", None
            else:
                aggregate.report, aggregate.status, aggregate.error = result["report"], "completed", None
                aggregate.version += 1
                await _event(db, aggregate, "complete")
            job.execution_status = "completed"
            job.finished_at = await database_now(db)
    except Exception as exc:
        logger.warning("job_failed", exc_info=True)
        async with async_session_maker() as db, db.begin():
            aggregate = await db.scalar(select(model).where(model.id == aggregate_id).with_for_update())
            job = await db.scalar(select(Outbox).where(Outbox.id == job_id).with_for_update())
            if (job and job.owner_token == owner and job.execution_status == "running" and job.lease_until > await database_now(db)
                    and aggregate and aggregate.active_job_id == job.id):
                job.execution_status = "unknown" if isinstance(exc, PlatformError) and exc.unknown else "failed"
                job.finished_at = await database_now(db)
                job.error = str(exc) if isinstance(exc, PlatformError) else "任务执行失败，请检查日志后重试"
                aggregate.error = job.error
                if kind in ("prepare", "diagnose"):
                    aggregate.status = "failed"
                else:
                    await _event(db, aggregate, "error")
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
