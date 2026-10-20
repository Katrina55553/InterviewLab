"""Durability tests; row-lock/concurrency assertions run only on real PostgreSQL."""

import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.platform import api, auth, coordination, graph, runtime, service, worker
from app.platform.models import Agent, Command, Event, Interview, LoginSession, Outbox, Resume, User, new_id, utcnow
from app.platform.runtime import DIMENSIONS, AIError
from tests.platform_db import isolated_database, postgres_test_url


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def platform_db(request, tmp_path, monkeypatch):
    kind = request.param
    if kind == "postgresql" and not postgres_test_url():
        pytest.skip("Set PLATFORM_TEST_DATABASE_URL for real PostgreSQL row-lock tests")
    async with isolated_database(kind, tmp_path) as database:
        monkeypatch.setattr(service, "async_session_maker", database.sessions)
        monkeypatch.setattr(runtime, "async_session_maker", database.sessions)
        monkeypatch.setattr(worker, "async_session_maker", database.sessions)
        monkeypatch.setattr(settings, "REDIS_URL", os.environ.get("PLATFORM_TEST_REDIS_URL", "redis://127.0.0.1:1/15"))
        monkeypatch.setattr(settings, "AI_COMMAND_LEASE_SECONDS", 150)
        yield database


@pytest.fixture
def ai(monkeypatch):
    async def respond(config, prompt, **kwargs):
        if config["scene"] == "report":
            return {"summary": "Saved evidence reviewed", "suggestions": ["Add concrete evidence"]}
        return {
            "score": 83,
            "dimensions": dict.fromkeys(DIMENSIONS, 83),
            "feedback": "Supported answer",
            "missing_points": [],
            "follow_up_needed": False,
        }

    stub = AsyncMock(side_effect=respond)
    monkeypatch.setattr(graph, "complete", stub)
    return stub


async def seed(database, *, active: bool = True):
    user_id, resume_id, interview_id = new_id(), new_id(), new_id()
    questions = [{"id": new_id(), "text": f"Technical question {index}", "category": "project"} for index in range(5)]
    current = {**questions[0], "main_index": 0, "is_followup": False} if active else None
    snapshot = {
        "questions": questions,
        "parsed": {"skills": ["Python"]},
        "resume_score": 76,
        "demo": False,
        "messages": [{"id": new_id(), "role": "assistant", "content": current["text"], "question_id": current["id"]}]
        if active
        else [],
        "evaluations": [],
        "main_index": 0 if active else -1,
        "follow_up_count": 0,
        "current_question": current,
        "status": "active" if active else "ready",
    }
    configs = {scene: {"scene": scene} for scene in ("evaluate", "followup", "report", "resume", "questions")}
    async with database.sessions() as db, db.begin():
        db.add(User(id=user_id, username="user_" + user_id[:8], password_hash="unused", role="user"))
        await db.flush()
        db.add(
            Resume(
                id=resume_id,
                user_id=user_id,
                filename="resume.txt",
                file_path="unused.txt",
                status="ready",
                parsed=snapshot["parsed"],
                preparation={"questions": questions, "resume_score": 76, "demo": False},
                config_snapshot=configs,
            )
        )
        await db.flush()
        db.add(
            Interview(
                id=interview_id,
                user_id=user_id,
                resume_id=resume_id,
                status=snapshot["status"],
                version=0,
                snapshot=snapshot,
                config_snapshot=configs,
            )
        )
    return SimpleNamespace(
        user_id=user_id, resume_id=resume_id, interview_id=interview_id, question=current, snapshot=snapshot
    )


def answer(run, *, request_id=None, content="I measured the workload and verified the design with traces."):
    return {
        "request_id": request_id or new_id(),
        "type": "answer",
        "version": 0,
        "expected_question_id": run.question["id"],
        "content": content,
    }


class BlockingGraph:
    def __init__(self, delegate):
        self.delegate = delegate
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def _wait_first(self):
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            await asyncio.wait_for(self.release.wait(), timeout=10)

    async def ainvoke(self, state):
        await self._wait_first()
        return await self.delegate.ainvoke(state)

    async def astream(self, state):
        await self._wait_first()
        async for update in self.delegate.astream(state):
            yield update


def require_postgres(database):
    if database.dialect != "postgresql":
        pytest.skip("SQLite has no PostgreSQL SELECT FOR UPDATE semantics")


@pytest.mark.asyncio
async def test_restore_is_exact_and_second_attempt_preserves_history(platform_db, ai):
    run = await seed(platform_db)
    result = await service.execute_command(run.user_id, run.interview_id, answer(run))
    assert ai.await_count == 1
    assert result["evaluations"][0]["score"] == 83
    for _ in range(2):
        restored = await service.get_interview(run.user_id, run.interview_id)
        assert restored == result
    assert ai.await_count == 1, "Restore must not regenerate questions or evaluations"
    second = await service.create_interview(run.user_id, run.resume_id)
    assert second["id"] != run.interview_id and second["status"] == "ready"
    assert (await service.get_interview(run.user_id, run.interview_id))["evaluations"] == result["evaluations"]
    events = await service.events_after(run.user_id, run.interview_id, 0)
    assert len(events) == 1 and events[0]["sequence"] == 1
    assert await service.events_after(run.user_id, run.interview_id, 1) == []
    with pytest.raises(HTTPException) as denied:
        await service.get_interview(new_id(), run.interview_id)
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_each_new_attempt_freezes_current_config_without_rewriting_prior_attempt(platform_db):
    run = await seed(platform_db)
    agent_id = new_id()
    async with platform_db.sessions() as db, db.begin():
        db.add(Agent(id=agent_id, name="评分官", scene="evaluate", prompt="first revision", enabled=True, version=1))
    first = await service.create_interview(run.user_id, run.resume_id)
    async with platform_db.sessions() as db, db.begin():
        agent = await db.get(Agent, agent_id)
        agent.prompt, agent.version = "second revision", 2
    second = await service.create_interview(run.user_id, run.resume_id)
    async with platform_db.sessions() as db:
        first_run = await db.get(Interview, first["id"])
        second_run = await db.get(Interview, second["id"])
        assert first_run.config_snapshot["evaluate"]["prompt"] == "first revision"
        assert first_run.config_snapshot["evaluate"]["agent_version"] == 1
        assert second_run.config_snapshot["evaluate"]["prompt"] == "second revision"
        assert second_run.config_snapshot["evaluate"]["agent_version"] == 2
        assert (await db.get(Resume, run.resume_id)).config_snapshot["evaluate"] == {"scene": "evaluate"}


@pytest.mark.asyncio
async def test_replay_committed_command_never_calls_ai_twice(platform_db, ai):
    run = await seed(platform_db)
    payload = answer(run)
    result = await service.execute_command(run.user_id, run.interview_id, payload)
    assert await service.execute_command(run.user_id, run.interview_id, payload) == result
    assert ai.await_count == 1
    with pytest.raises(HTTPException) as conflict:
        await service.execute_command(run.user_id, run.interview_id, {**payload, "content": "Different input"})
    assert conflict.value.status_code == 409
    async with platform_db.sessions() as db:
        assert await db.scalar(select(func.count(Command.id))) == 1
        assert await db.scalar(select(func.count(Event.id))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("redis_available", [True, False], ids=["redis-online", "redis-unavailable"])
async def test_real_postgres_concurrent_identical_request_reserves_once(platform_db, ai, monkeypatch, redis_available):
    require_postgres(platform_db)
    if not redis_available:
        monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/15")
    run = await seed(platform_db)
    blocking = BlockingGraph(service.interview_graph)
    monkeypatch.setattr(service, "interview_graph", blocking)
    payload = answer(run)
    first = asyncio.create_task(service.execute_command(run.user_id, run.interview_id, payload))
    await asyncio.wait_for(blocking.entered.wait(), timeout=5)
    try:
        duplicate = await service.execute_command(run.user_id, run.interview_id, payload)
        assert duplicate["pending_command"]["status"] == "running"
        assert duplicate["version"] == 0
    finally:
        blocking.release.set()
    result = await asyncio.wait_for(first, timeout=5)
    assert await service.execute_command(run.user_id, run.interview_id, payload) == result
    assert blocking.calls == 1 and ai.await_count == 1
    assert len(result["evaluations"]) == 1 and result["version"] == 1


@pytest.mark.asyncio
async def test_heartbeat_does_not_resurrect_expired_command(platform_db):
    run = await seed(platform_db)
    command_id = new_id()
    expired = utcnow() - timedelta(seconds=10)
    async with platform_db.sessions() as db, db.begin():
        db.add(
            Command(
                id=command_id,
                interview_id=run.interview_id,
                request_id=new_id(),
                input_hash="a" * 64,
                status="running",
                owner_token="expired-owner",
                lease_until=expired,
            )
        )
    await asyncio.wait_for(service._heartbeat(Command, command_id, "expired-owner", interval=0.01), timeout=2)
    async with platform_db.sessions() as db:
        assert (await db.get(Command, command_id)).lease_until == expired


@pytest.mark.asyncio
async def test_real_postgres_different_requests_for_same_question_conflict(platform_db, ai, monkeypatch):
    require_postgres(platform_db)
    run = await seed(platform_db)
    blocking = BlockingGraph(service.interview_graph)
    monkeypatch.setattr(service, "interview_graph", blocking)
    first = asyncio.create_task(service.execute_command(run.user_id, run.interview_id, answer(run)))
    await asyncio.wait_for(blocking.entered.wait(), timeout=5)
    second = answer(run)
    try:
        with pytest.raises(HTTPException) as conflict:
            await service.execute_command(run.user_id, run.interview_id, second)
        assert conflict.value.status_code == 409
    finally:
        blocking.release.set()
    await asyncio.wait_for(first, timeout=5)
    with pytest.raises(HTTPException) as stale:
        await service.execute_command(run.user_id, run.interview_id, second)
    assert stale.value.status_code == 409 and blocking.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", [False, True], ids=["failed", "unknown"])
async def test_failure_retains_answer_and_exact_question(platform_db, ai, unknown):
    run = await seed(platform_db)
    payload = answer(run)
    successful_response = ai.side_effect
    ai.side_effect = AIError("Provider unavailable", unknown=unknown)
    with pytest.raises(HTTPException) as failure:
        await service.execute_command(run.user_id, run.interview_id, payload)
    assert failure.value.status_code == 503
    restored = await service.get_interview(run.user_id, run.interview_id)
    assert restored["version"] == 0 and restored["current_question"] == run.question
    assert restored["evaluations"] == []
    assert restored["pending_command"]["status"] == ("unknown" if unknown else "failed")
    assert restored["pending_command"]["content"] == payload["content"]
    ai.side_effect = successful_response
    retried = await service.execute_command(run.user_id, run.interview_id, {**payload, "request_id": new_id()})
    assert retried["version"] == 1 and len(retried["evaluations"]) == 1
    assert ai.await_count == 2


@pytest.mark.asyncio
async def test_cancelled_answer_immediately_preserves_unknown_draft_and_retry(platform_db, ai):
    run = await seed(platform_db)
    payload = answer(run)
    entered = asyncio.Event()
    blocked = asyncio.Event()
    successful_response = ai.side_effect

    async def wait_for_disconnect(*args, **kwargs):
        entered.set()
        await blocked.wait()
        return await successful_response(*args, **kwargs)

    ai.side_effect = wait_for_disconnect
    task = asyncio.create_task(service.execute_command(run.user_id, run.interview_id, payload))
    await asyncio.wait_for(entered.wait(), timeout=5)
    async with platform_db.sessions() as db:
        command = await db.scalar(select(Command).where(Command.request_id == payload["request_id"]))
        assert command.result["checkpoint"]["resume_from"] == "evaluate"
        assert command.lease_until > utcnow(), "The test cancels before lease expiry"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    restored = await service.get_interview(run.user_id, run.interview_id)
    assert restored["version"] == 0 and restored["current_question"] == run.question
    assert restored["pending_command"]["status"] == "unknown"
    assert restored["pending_command"]["content"] == payload["content"]
    async with platform_db.sessions() as db:
        command = await db.scalar(select(Command).where(Command.request_id == payload["request_id"]))
        assert command.owner_token is None
        assert command.result["checkpoint"]["snapshot"]["messages"][-1]["content"] == payload["content"]
    ai.side_effect = successful_response
    result = await service.execute_command(run.user_id, run.interview_id, {**payload, "request_id": new_id()})
    assert result["version"] == 1 and result["evaluations"][0]["score"] == 83
    assert sum(message["role"] == "user" for message in result["messages"]) == 1
    assert ai.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", [False, True], ids=["followup-failed", "followup-unknown"])
async def test_completed_evaluation_survives_followup_failure_and_retry(platform_db, monkeypatch, unknown):
    run = await seed(platform_db)
    payload = answer(run)
    calls = []
    fail_followup = True

    async def respond(config, prompt, **kwargs):
        calls.append(config["scene"])
        if config["scene"] == "evaluate":
            return {
                "score": 45,
                "dimensions": dict.fromkeys(DIMENSIONS, 45),
                "feedback": "Need implementation evidence",
                "missing_points": ["implementation detail"],
                "follow_up_needed": True,
            }
        if fail_followup:
            raise AIError("Followup provider unavailable", unknown=unknown)
        return {"question": "Which traces proved the bottleneck?"}

    monkeypatch.setattr(graph, "complete", respond)
    with pytest.raises(HTTPException) as failure:
        await service.execute_command(run.user_id, run.interview_id, payload)
    assert failure.value.status_code == 503
    restored = await service.get_interview(run.user_id, run.interview_id)
    assert restored["version"] == 0 and restored["current_question"] == run.question
    async with platform_db.sessions() as db:
        failed = await db.scalar(select(Command).where(Command.request_id == payload["request_id"]))
        checkpoint = failed.result["checkpoint"]
        assert checkpoint["resume_from"] == "followup"
        assert checkpoint["snapshot"]["evaluations"][0]["score"] == 45
        assert failed.status == ("unknown" if unknown else "failed")
    fail_followup = False
    result = await service.execute_command(run.user_id, run.interview_id, {**payload, "request_id": new_id()})
    assert calls == ["evaluate", "followup", "followup"], "An already saved evaluation must never be charged again"
    assert result["version"] == 1
    assert result["current_question"]["is_followup"] is True
    assert [evaluation["score"] for evaluation in result["evaluations"]] == [45]
    assert sum(message["role"] == "user" for message in result["messages"]) == 1


@pytest.mark.asyncio
async def test_real_postgres_expired_owner_cannot_overwrite_successor(platform_db, ai, monkeypatch):
    require_postgres(platform_db)
    run = await seed(platform_db)
    blocking = BlockingGraph(service.interview_graph)
    monkeypatch.setattr(service, "interview_graph", blocking)
    old_payload = answer(run, content="Old answer that must never commit")
    first = asyncio.create_task(service.execute_command(run.user_id, run.interview_id, old_payload))
    await asyncio.wait_for(blocking.entered.wait(), timeout=5)
    try:
        async with platform_db.sessions() as db, db.begin():
            command = await db.scalar(select(Command).where(Command.request_id == old_payload["request_id"]))
            command.lease_until = utcnow() - timedelta(seconds=1)
        restored = await service.get_interview(run.user_id, run.interview_id)
        assert restored["pending_command"]["status"] == "unknown"
        new_payload = answer(run, content="New verified answer")
        successor = await service.execute_command(run.user_id, run.interview_id, new_payload)
    finally:
        blocking.release.set()
    with pytest.raises(HTTPException) as stale:
        await asyncio.wait_for(first, timeout=5)
    assert stale.value.status_code == 409
    restored = await service.get_interview(run.user_id, run.interview_id)
    assert restored["version"] == successor["version"] == 1
    assert [evaluation["answer"] for evaluation in restored["evaluations"]] == ["New verified answer"]


@pytest.mark.asyncio
async def test_report_retry_and_celery_redelivery_preserve_saved_result(platform_db, ai, monkeypatch):
    run = await seed(platform_db)
    await service.execute_command(run.user_id, run.interview_id, {"request_id": new_id(), "type": "end", "version": 0})
    async with platform_db.sessions() as db:
        first_job_id = (await db.get(Interview, run.interview_id)).active_job_id
    original = service.report_graph
    monkeypatch.setattr(
        service, "report_graph", SimpleNamespace(ainvoke=AsyncMock(side_effect=AIError("Report provider unavailable")))
    )
    await service.run_job(first_job_id)
    failed = await service.get_interview(run.user_id, run.interview_id)
    assert failed["status"] == "reporting" and failed["error"]
    await service.retry_report(run.user_id, run.interview_id)
    async with platform_db.sessions() as db:
        next_job_id = (await db.get(Interview, run.interview_id)).active_job_id
    assert next_job_id != first_job_id
    monkeypatch.setattr(service, "report_graph", original)
    await service.run_job(next_job_id)
    completed = await service.get_interview(run.user_id, run.interview_id)
    assert completed["status"] == "completed"
    assert completed["report"]["overall_score"] is None
    assert completed["report"]["summary"] == "Saved evidence reviewed"
    assert ai.await_count == 1
    await service.run_job(next_job_id)
    await service.run_job(first_job_id)
    assert await service.get_interview(run.user_id, run.interview_id) == completed
    assert ai.await_count == 1


@pytest.mark.asyncio
async def test_real_postgres_celery_duplicate_job_invokes_graph_once(platform_db, ai, monkeypatch):
    require_postgres(platform_db)
    run = await seed(platform_db)
    await service.execute_command(run.user_id, run.interview_id, {"request_id": new_id(), "type": "end", "version": 0})
    async with platform_db.sessions() as db:
        job_id = (await db.get(Interview, run.interview_id)).active_job_id
    blocking = BlockingGraph(service.report_graph)
    monkeypatch.setattr(service, "report_graph", blocking)
    first = asyncio.create_task(service.run_job(job_id))
    await asyncio.wait_for(blocking.entered.wait(), timeout=5)
    try:
        await service.run_job(job_id)
        assert blocking.calls == 1
    finally:
        blocking.release.set()
    await asyncio.wait_for(first, timeout=5)
    assert (await service.get_interview(run.user_id, run.interview_id))["status"] == "completed"
    assert ai.await_count == 1


@pytest.mark.asyncio
async def test_restore_detects_expired_report_and_manual_retry_creates_fresh_job(platform_db, ai):
    run = await seed(platform_db)
    await service.execute_command(run.user_id, run.interview_id, {"request_id": new_id(), "type": "end", "version": 0})
    async with platform_db.sessions() as db, db.begin():
        interview = await db.get(Interview, run.interview_id)
        old_id = interview.active_job_id
        job = await db.get(Outbox, old_id)
        job.execution_status = "running"
        job.owner_token = "expired-report-worker"
        job.lease_until = utcnow() - timedelta(seconds=1)
    restored = await service.get_interview(run.user_id, run.interview_id)
    assert restored["status"] == "reporting" and restored["error"]
    async with platform_db.sessions() as db:
        job = await db.get(Outbox, old_id)
        assert job.execution_status == "unknown" and job.owner_token is None
    await service.retry_report(run.user_id, run.interview_id)
    async with platform_db.sessions() as db:
        new_job_id = (await db.get(Interview, run.interview_id)).active_job_id
    assert new_job_id != old_id
    await service.run_job(new_job_id)
    completed = await service.get_interview(run.user_id, run.interview_id)
    assert completed["status"] == "completed" and completed["error"] is None
    await service.run_job(old_id)
    assert await service.get_interview(run.user_id, run.interview_id) == completed
    assert ai.await_count == 1


@pytest.mark.asyncio
async def test_expired_report_failure_does_not_write_after_lease_loss(platform_db, monkeypatch):
    run = await seed(platform_db)
    await service.execute_command(run.user_id, run.interview_id, {"request_id": new_id(), "type": "end", "version": 0})
    async with platform_db.sessions() as db:
        job_id = (await db.get(Interview, run.interview_id)).active_job_id
    failing = SimpleNamespace(ainvoke=AsyncMock(side_effect=AIError("Late provider failure")))
    blocking = BlockingGraph(failing)
    monkeypatch.setattr(service, "report_graph", blocking)
    first = asyncio.create_task(service.run_job(job_id))
    await asyncio.wait_for(blocking.entered.wait(), timeout=5)
    try:
        async with platform_db.sessions() as db, db.begin():
            job = await db.get(Outbox, job_id)
            job.lease_until = utcnow() - timedelta(seconds=1)
    finally:
        blocking.release.set()
    await asyncio.wait_for(first, timeout=5)
    async with platform_db.sessions() as db:
        assert (await db.get(Outbox, job_id)).execution_status == "running"
        assert (await db.get(Interview, run.interview_id)).error is None
    restored = await service.get_interview(run.user_id, run.interview_id)
    assert restored["error"] and "Late provider failure" not in restored["error"]
    async with platform_db.sessions() as db:
        assert (await db.get(Outbox, job_id)).execution_status == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("detector", ["worker", "restore"])
async def test_expired_job_requires_manual_retry_and_new_identity(platform_db, monkeypatch, detector):
    run = await seed(platform_db)
    async with platform_db.sessions() as db, db.begin():
        resume = await db.get(Resume, run.resume_id)
        resume.status = "processing"
        job = service.queue_job(db, "prepare", resume.id)
        job.execution_status, job.owner_token = "running", "expired-owner"
        job.lease_until = utcnow() - timedelta(seconds=1)
        resume.active_job_id = job.id
        old_id = job.id
    if detector == "worker":
        await service.run_job(old_id)
    else:
        restored = await service.get_resume(run.user_id, run.resume_id)
        assert restored["status"] == "failed" and restored["error"]
    async with platform_db.sessions() as db:
        assert (await db.get(Outbox, old_id)).execution_status == "unknown"
        assert (await db.get(Resume, run.resume_id)).status == "failed"
    monkeypatch.setattr(service, "freeze_configs", AsyncMock(return_value={"new": "config"}))
    await service.retry_resume(run.user_id, run.resume_id)
    async with platform_db.sessions() as db:
        resume = await db.get(Resume, run.resume_id)
        assert resume.active_job_id != old_id and resume.status == "queued"
        assert resume.config_snapshot == {"new": "config"}
        assert (await db.get(Outbox, old_id)).execution_status == "unknown"


@pytest.mark.asyncio
async def test_real_postgres_dispatcher_cannot_publish_uncommitted_outbox(platform_db, monkeypatch):
    require_postgres(platform_db)
    published = []
    monkeypatch.setattr(worker.celery_app, "send_task", lambda name, **kwargs: published.append((name, kwargs)))
    async with platform_db.sessions() as db:
        job = service.queue_job(db, "prepare", new_id())
        await db.flush()
        assert await worker.dispatch_once() == 0
        assert published == []
        await db.commit()
    assert await worker.dispatch_once() == 1
    assert published[0][1]["args"] == [job.id]
    assert await worker.dispatch_once() == 0
    async with platform_db.sessions() as db:
        assert (await db.get(Outbox, job.id)).status == "published"


@pytest.mark.asyncio
async def test_real_postgres_rest_and_websocket_share_idempotency_and_ownership(platform_db, ai, monkeypatch):
    require_postgres(platform_db)
    run = await seed(platform_db)
    monkeypatch.setattr(auth, "async_session_maker", platform_db.sessions)
    monkeypatch.setattr(api, "async_session_maker", platform_db.sessions)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://localhost:5173"])
    first_token, second_token = new_id(), new_id()
    other_id = new_id()
    async with platform_db.sessions() as db, db.begin():
        db.add(User(id=other_id, username="other_" + other_id[:8], password_hash="unused", role="user"))
        await db.flush()
        db.add_all(
            [
                LoginSession(
                    id=auth._token_hash(first_token),
                    user_id=run.user_id,
                    csrf_token="csrf-test",
                    expires_at=utcnow() + timedelta(hours=1),
                ),
                LoginSession(
                    id=auth._token_hash(second_token),
                    user_id=other_id,
                    csrf_token="csrf-other",
                    expires_at=utcnow() + timedelta(hours=1),
                ),
            ]
        )
    app = FastAPI()
    app.add_middleware(auth.AuthCSRFMiddleware)
    app.include_router(api.router, prefix="/api")
    app.include_router(api.ws_router)
    with TestClient(app, headers={"Origin": "http://localhost:5173"}) as client:
        assert client.get(f"/api/interviews/{run.interview_id}").status_code == 401
        client.cookies.set(auth.SESSION_COOKIE, second_token)
        client.cookies.set(auth.CSRF_COOKIE, "csrf-other")
        assert client.get(f"/api/interviews/{run.interview_id}").status_code == 404
        with (
            pytest.raises(WebSocketDisconnect) as denied,
            client.websocket_connect(f"/ws/interviews/{run.interview_id}"),
        ):
            pass
        assert denied.value.code == 4404
        client.cookies.clear()
        client.cookies.set(auth.SESSION_COOKIE, first_token)
        client.cookies.set(auth.CSRF_COOKIE, "csrf-test")
        assert client.get(f"/api/interviews/{run.interview_id}").status_code == 200
        payload = answer(run)
        with client.websocket_connect(f"/ws/interviews/{run.interview_id}") as socket:
            socket.send_json(payload)
            while True:
                message = socket.receive_json()
                if message.get("type") == "command_result":
                    break
            assert message["snapshot"]["version"] == 1
        replay = client.post(
            f"/api/interviews/{run.interview_id}/commands", json=payload, headers={"X-CSRF-Token": "csrf-test"}
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == message["snapshot"]
        assert ai.await_count == 1


@pytest.mark.asyncio
async def test_real_redis_release_does_not_delete_replacement_owner(monkeypatch):
    url = os.environ.get("PLATFORM_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set PLATFORM_TEST_REDIS_URL for real Redis Lua tests")
    monkeypatch.setattr(settings, "REDIS_URL", url)
    key = "test:" + new_id()
    redis_key = f"platform:flight:{key}"
    client = redis.from_url(url, decode_responses=True)
    try:
        assert await client.ping()
        async with coordination.singleflight(key, "owner-one"):
            assert await client.get(redis_key) == "owner-one"
            await client.set(redis_key, "owner-two", ex=30)
        assert await client.get(redis_key) == "owner-two"
    finally:
        await client.delete(redis_key)
        await client.aclose()
