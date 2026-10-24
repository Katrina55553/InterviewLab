"""An explicit retry of a failed synthesis is atomic and never replays unknown calls."""

import asyncio
import hashlib
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.platform import media
from app.platform.media_models import MediaTask
from app.platform.media_tts import TTSProviderError
from app.platform.models import Interview, Resume, User, new_id, utcnow
from tests.platform_db import isolated_database, postgres_test_url


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def synthesis(request, tmp_path, monkeypatch):
    if request.param == "postgresql" and not postgres_test_url():
        pytest.skip("Set PLATFORM_TEST_DATABASE_URL for PostgreSQL synthesis retries")
    async with isolated_database(request.param, tmp_path) as database:
        user = User(id=new_id(), username=new_id(), password_hash="unused")
        resume_id, interview_id = new_id(), new_id()
        async with database.sessions() as db, db.begin():
            db.add(user)
            await db.flush()
            db.add(Resume(id=resume_id, user_id=user.id, filename="resume.txt", file_path="unused.txt"))
            await db.flush()
            db.add(Interview(id=interview_id, user_id=user.id, resume_id=resume_id))
        monkeypatch.setattr(media, "async_session_maker", database.sessions)
        monkeypatch.setattr(media, "_require_speech", lambda: ("app", "key", "secret"))
        monkeypatch.setattr(media.settings, "XFYUN_TTS_VOICE", "test-voice")
        yield database, user, media.TTSRequest(interview_id=interview_id, text="介绍你的项目")


@pytest.mark.asyncio
async def test_failed_synthesis_can_be_retried_and_success_is_reused(synthesis, monkeypatch):
    database, user, body = synthesis
    request = AsyncMock(side_effect=[TTSProviderError("供应商拒绝请求"), {"provider_task_id": "recovered"}])
    monkeypatch.setattr(media, "tts_request", request)
    failed = await media.create_tts(body, user)
    assert failed["status"] == "failed"
    retry = await media.create_tts(body, user)
    assert retry["status"] == "processing" and retry["error"] is None
    assert request.await_count == 2
    attempts = [call.args[1]["header"]["request_id"] for call in request.await_args_list]
    assert len(set(attempts)) == 2
    assert await media.create_tts(body, user) == retry
    assert request.await_count == 2
    async with database.sessions() as db:
        row = await db.scalar(select(MediaTask))
        assert row.provider_task_id == "recovered" and row.error is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["creating", "processing", "completed", "unknown"])
async def test_nonfailed_synthesis_never_creates_another_provider_call(synthesis, status, monkeypatch):
    database, user, body = synthesis
    async with database.sessions() as db, db.begin():
        db.add(MediaTask(user_id=user.id, interview_id=body.interview_id, status=status,
                         text_hash=hashlib.sha256(f"test-voice\0{body.text}".encode()).hexdigest()))
    request = AsyncMock()
    monkeypatch.setattr(media, "tts_request", request)
    assert (await media.create_tts(body, user))["status"] == status
    request.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_failed_synthesis_retries_only_call_provider_once(synthesis, monkeypatch):
    database, user, body = synthesis
    async with database.sessions() as db, db.begin():
        db.add(MediaTask(user_id=user.id, interview_id=body.interview_id, status="failed", error="old error",
                         created_at=utcnow() - timedelta(minutes=5),
                         text_hash=hashlib.sha256(f"test-voice\0{body.text}".encode()).hexdigest()))
    entered, release = asyncio.Event(), asyncio.Event()

    async def respond(*_args):
        entered.set()
        await release.wait()
        return {"provider_task_id": "one-retry"}

    request = AsyncMock(side_effect=respond)
    monkeypatch.setattr(media, "tts_request", request)
    tasks = [asyncio.create_task(media.create_tts(body, user)) for _ in range(3)]
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        done, pending = await asyncio.wait(tasks, timeout=1)
        assert len(done) == 2 and len(pending) == 1
        assert all(task.result()["status"] == "creating" for task in done)
        request.assert_awaited_once()
        async with database.sessions() as db:
            row = await db.scalar(select(MediaTask))
            assert row.status == "creating" and row.error is None
            assert row.created_at > utcnow() - timedelta(seconds=10)
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert {result["status"] for result in results} == {"creating", "processing"}
    request.assert_awaited_once()
