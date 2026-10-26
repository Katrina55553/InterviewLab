"""Deletion exercises the HTTP boundary, foreign keys, files and stale job deliveries."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event, select

from app.platform import api, auth, service, worker
from app.platform.media_locks import try_media_lock
from app.platform.media_models import MediaTask, VisionFeedback
from app.platform.models import (
    AiCall,
    Command,
    Event,
    Interview,
    LoginSession,
    Outbox,
    Resume,
    ResumeDiagnosis,
    User,
    new_id,
    utcnow,
)
from tests.platform_db import isolated_database, postgres_test_url


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def deletion(request, tmp_path, monkeypatch):
    if request.param == "postgresql" and not postgres_test_url():
        pytest.skip("Set PLATFORM_TEST_DATABASE_URL for real PostgreSQL deletion tests")
    async with isolated_database(request.param, tmp_path) as database:
        if request.param == "sqlite":
            event.listen(database.engine.sync_engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
            await database.engine.dispose()
        for module in (api, auth, service, worker):
            monkeypatch.setattr(module, "async_session_maker", database.sessions)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(service.settings, "MEDIA_STORAGE_DIR", str(tmp_path / "uploads/media"))
        user_id, other_id, resume_id, interview_id = (new_id() for _ in range(4))
        token, csrf = "deletion-test-session", "deletion-test-csrf"
        path = tmp_path / "uploads/platform" / user_id / f"{resume_id}.txt"
        path.parent.mkdir(parents=True)
        path.write_text("Private resume facts", encoding="utf-8")
        async with database.sessions() as db, db.begin():
            db.add_all([User(id=user_id, username="owner", password_hash="unused"),
                        User(id=other_id, username="other", password_hash="unused")])
            await db.flush()
            db.add(LoginSession(id=auth._token_hash(token), user_id=user_id, csrf_token=csrf,
                                expires_at=utcnow() + timedelta(hours=1)))
            db.add(Resume(id=resume_id, user_id=user_id, filename="resume.txt", file_path=str(path),
                          status="ready", parsed={"skills": ["Python"]}, config_snapshot={}))
            await db.flush()
            db.add(Interview(id=interview_id, user_id=user_id, resume_id=resume_id, status="ready",
                             snapshot={}, config_snapshot={}))
        app = FastAPI()
        app.include_router(api.router, prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                    cookies={auth.SESSION_COOKIE: token, auth.CSRF_COOKIE: csrf}) as client:
            yield SimpleNamespace(db=database, client=client, csrf={"X-CSRF-Token": csrf}, user_id=user_id,
                                  other_id=other_id, resume_id=resume_id, interview_id=interview_id, file=path)


@pytest.mark.asyncio
async def test_delete_requires_auth_csrf_and_ownership(deletion):
    run = deletion
    for kind, object_id, model in [("resumes", run.resume_id, Resume), ("interviews", run.interview_id, Interview)]:
        path = f"/api/{kind}/{object_id}"
        assert (await run.client.delete(path)).status_code == 403
        assert (await run.client.delete(path, headers={"X-CSRF-Token": "wrong"})).status_code == 403
        async with run.db.sessions() as db, db.begin():
            row = await db.get(model, object_id)
            row.user_id = run.other_id
        assert (await run.client.delete(path, headers=run.csrf)).status_code == 404
        async with run.db.sessions() as db:
            assert await db.get(model, object_id) is not None
    run.client.cookies.clear()
    assert (await run.client.delete(f"/api/interviews/{run.interview_id}")).status_code == 401
    assert run.file.exists()


@pytest.mark.asyncio
async def test_resume_dependency_then_complete_cleanup(deletion):
    run = deletion
    diagnosis_id, prepare_id, diagnose_id = (new_id() for _ in range(3))
    async with run.db.sessions() as db, db.begin():
        db.add(ResumeDiagnosis(id=diagnosis_id, resume_id=run.resume_id, user_id=run.user_id, status="queued"))
        db.add_all([Outbox(id=prepare_id, kind="prepare", aggregate_id=run.resume_id),
                    Outbox(id=diagnose_id, kind="diagnose", aggregate_id=diagnosis_id)])
    path = f"/api/resumes/{run.resume_id}"
    rejected = await run.client.delete(path, headers=run.csrf)
    assert rejected.status_code == 409 and "关联面试" in rejected.text
    assert run.file.exists()
    assert (await run.client.delete(f"/api/interviews/{run.interview_id}", headers=run.csrf)).status_code == 204
    assert (await run.client.delete(path, headers=run.csrf)).status_code == 204
    assert not run.file.exists()
    async with run.db.sessions() as db:
        for model, object_id in [(Resume, run.resume_id), (ResumeDiagnosis, diagnosis_id),
                                 (Outbox, prepare_id), (Outbox, diagnose_id)]:
            assert await db.get(model, object_id) is None
    assert (await run.client.get(path)).status_code == 404
    assert (await run.client.get(path + "/file")).status_code == 404
    assert (await run.client.delete(path, headers=run.csrf)).status_code == 404
    # A broker delivery already in transit must neither call AI nor recreate data.
    await service.run_job(prepare_id)
    await service.run_job(diagnose_id)


@pytest.mark.asyncio
async def test_interview_deletes_dependents_but_keeps_resume_and_other_interview(deletion, tmp_path):
    run = deletion
    audio = tmp_path / "uploads/media/audio.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    other_interview, report_job, audit_id = (new_id() for _ in range(3))
    async with run.db.sessions() as db, db.begin():
        db.add(Interview(id=other_interview, user_id=run.user_id, resume_id=run.resume_id,
                         status="ready", snapshot={}, config_snapshot={}))
        db.add(Command(interview_id=run.interview_id, request_id=new_id(), input_hash="hash", status="completed"))
        db.add(Event(interview_id=run.interview_id, sequence=1, payload={"private": "answer"}))
        db.add(MediaTask(user_id=run.user_id, interview_id=run.interview_id, status="completed",
                         text_hash="hash", audio_path=str(audio)))
        db.add(VisionFeedback(user_id=run.user_id, interview_id=run.interview_id,
                              provider_id="p", provider_version="1", feedback="feedback"))
        db.add(Outbox(id=report_job, kind="report", aggregate_id=run.interview_id))
        db.add(AiCall(id=audit_id, user_id=run.user_id, interview_id=run.interview_id,
                      scene="report", status="completed"))
    path = f"/api/interviews/{run.interview_id}"
    assert (await run.client.delete(path, headers=run.csrf)).status_code == 204
    assert not audio.exists() and run.file.exists()
    async with run.db.sessions() as db:
        assert await db.get(Interview, run.interview_id) is None
        assert await db.get(Interview, other_interview) is not None
        assert await db.get(Resume, run.resume_id) is not None
        assert await db.get(Outbox, report_job) is None
        assert (await db.get(AiCall, audit_id)).interview_id is None
        for model in (Command, Event, MediaTask, VisionFeedback):
            assert not list(await db.scalars(select(model).where(model.interview_id == run.interview_id)))
    for suffix in ("", "/report", "/events"):
        assert (await run.client.get(path + suffix)).status_code == 404
    assert [item["id"] for item in (await run.client.get("/api/interviews")).json()] == [other_interview]
    await service.run_job(report_job)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["command", "report", "prepare", "diagnose"])
async def test_running_work_blocks_deletion_then_expired_work_can_be_deleted(deletion, kind):
    run = deletion
    job_id = new_id()
    async with run.db.sessions() as db, db.begin():
        if kind in ("prepare", "diagnose"):
            await db.delete(await db.get(Interview, run.interview_id))
            aggregate_id = run.resume_id
            if kind == "diagnose":
                aggregate_id = new_id()
                db.add(ResumeDiagnosis(id=aggregate_id, resume_id=run.resume_id, user_id=run.user_id, status="processing"))
            path = f"/api/resumes/{run.resume_id}"
        else:
            aggregate_id = run.interview_id
            path = f"/api/interviews/{run.interview_id}"
        if kind == "command":
            record = Command(id=job_id, interview_id=aggregate_id, request_id=new_id(), input_hash="hash",
                             status="running", owner_token="owner", lease_until=utcnow() + timedelta(minutes=1))
        else:
            record = Outbox(id=job_id, kind=kind, aggregate_id=aggregate_id, execution_status="running",
                            owner_token="owner", lease_until=utcnow() + timedelta(minutes=1))
        db.add(record)
    assert (await run.client.delete(path, headers=run.csrf)).status_code == 409
    assert run.file.exists()
    async with run.db.sessions() as db, db.begin():
        record = await db.get(Command if kind == "command" else Outbox, job_id)
        record.lease_until = utcnow() - timedelta(seconds=1)
    assert (await run.client.delete(path, headers=run.csrf)).status_code == 204


@pytest.mark.asyncio
async def test_dispatcher_handles_deletion_during_publish(deletion, monkeypatch):
    run = deletion
    job_id = new_id()
    async with run.db.sessions() as db, db.begin():
        db.add(Outbox(id=job_id, kind="report", aggregate_id=run.interview_id))
    async def delete_before_ack(_):
        await service.delete_interview(run.user_id, run.interview_id)
    monkeypatch.setattr(worker, "checkpoint", delete_before_ack)
    monkeypatch.setattr(worker.celery_app, "send_task", lambda *args, **kwargs: None)
    assert await worker.dispatch_once() == 0
    monkeypatch.setattr(service.parsing_graph, "ainvoke", AsyncMock(side_effect=AssertionError("AI must not run")))
    await service.run_job(job_id)


@pytest.mark.asyncio
async def test_expired_worker_cannot_restore_a_deleted_interview(deletion, monkeypatch):
    run = deletion
    job_id = new_id()
    entered, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def coordination_stub(*_):
        yield

    async def generate_report(_):
        entered.set()
        await release.wait()
        return {"report": {"summary": "late result"}}

    monkeypatch.setattr(service, "singleflight", coordination_stub)
    monkeypatch.setattr(service.report_graph, "ainvoke", generate_report)
    async with run.db.sessions() as db, db.begin():
        row = await db.get(Interview, run.interview_id)
        row.status, row.active_job_id = "reporting", job_id
        db.add(Outbox(id=job_id, kind="report", aggregate_id=row.id))
    task = asyncio.create_task(service.run_job(job_id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        # Deletion is refused until the worker loses its execution lease.
        path = f"/api/interviews/{run.interview_id}"
        assert (await run.client.delete(path, headers=run.csrf)).status_code == 409
        async with run.db.sessions() as db, db.begin():
            job = await db.get(Outbox, job_id)
            job.lease_until = utcnow() - timedelta(seconds=1)
        assert (await run.client.delete(path, headers=run.csrf)).status_code == 204
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=5)
    async with run.db.sessions() as db:
        assert await db.get(Interview, run.interview_id) is None
        assert await db.get(Outbox, job_id) is None
        assert not list(await db.scalars(select(Event).where(Event.interview_id == run.interview_id)))


@pytest.mark.asyncio
async def test_file_cleanup_never_deletes_outside_private_storage(deletion, tmp_path):
    run = deletion
    outside = tmp_path / "keep.txt"
    outside.write_text("unrelated file", encoding="utf-8")
    async with run.db.sessions() as db, db.begin():
        row = await db.get(Resume, run.resume_id)
        row.file_path = str(outside)
    await service.delete_interview(run.user_id, run.interview_id)
    await service.delete_resume(run.user_id, run.resume_id)
    assert outside.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace", ["vision", "tts-query"])
async def test_deletion_waits_for_media_processing(deletion, namespace):
    run = deletion
    if run.db.dialect != "postgresql":
        pytest.skip("Advisory lock conflicts require real PostgreSQL")
    task_id = new_id()
    async with run.db.sessions() as db, db.begin():
        db.add(MediaTask(id=task_id, user_id=run.user_id, interview_id=run.interview_id,
                         status="processing", text_hash="hash"))
    async with run.db.sessions() as db, db.begin():
        assert await try_media_lock(db, namespace, run.interview_id if namespace == "vision" else task_id)
        assert (await run.client.delete(f"/api/interviews/{run.interview_id}", headers=run.csrf)).status_code == 409
    assert (await run.client.delete(f"/api/interviews/{run.interview_id}", headers=run.csrf)).status_code == 204
