"""Governance invariants, with PostgreSQL-only coordination assertions explicitly separated."""

import asyncio
import importlib.util
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, func, select, text

from app.core.config import settings
from app.platform import runtime, worker
from app.platform.admin import encrypt_key
from app.platform.ai import flights, guard
from app.platform.contracts import PlatformError, error_payload, traced
from app.platform.domain.state import read_snapshot, validate_command
from app.platform.models import AiCall, AiFlight, AiPermit, Outbox, User, new_id, utcnow
from app.platform.operations import recovery
from tests.platform_db import isolated_database, postgres_test_url


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def database(request, tmp_path, monkeypatch):
    if request.param == "postgresql" and not postgres_test_url():
        pytest.skip("Set PLATFORM_TEST_DATABASE_URL for PostgreSQL engineering tests")
    async with isolated_database(request.param, tmp_path) as db:
        monkeypatch.setattr(runtime, "async_session_maker", db.sessions)
        monkeypatch.setattr(worker, "async_session_maker", db.sessions)
        monkeypatch.setattr(recovery, "async_session_maker", db.sessions)
        monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")
        yield db


def config():
    return {"scene": "evaluate", "model": "test", "encrypted_key": encrypt_key("one-shared-credential")}


async def user(database):
    value = new_id()
    async with database.sessions() as db, db.begin():
        db.add(User(id=value, username=value, password_hash="unused", role="user"))
    return value


def test_snapshot_and_transition_contract_is_pure():
    assert read_snapshot({"status": "ready"})["schema_version"] == 1
    with pytest.raises(ValueError):
        read_snapshot({"schema_version": 99})
    for status in ("ready", "active", "reporting", "completed", "failed"):
        for action in ("start", "answer", "end", "skip", "rephrase"):
            valid = action in {"ready": {"start", "end"}, "active": {"answer", "end", "skip", "rephrase"}}.get(status, set())
            command = {"type": action, "expected_question_id": "q"}
            if valid:
                validate_command(status, {"current_question": {"id": "q"}}, command)
            else:
                with pytest.raises(ValueError):
                    validate_command(status, {}, command)


def test_error_contract_preserves_server_trace_and_classification():
    with traced(new=True) as context:
        payload = error_payload(PlatformError("AI_OVERLOADED", "full"))
        assert payload == {"code": "AI_OVERLOADED", "message": "full", "retry_policy": "manual",
                           "outcome": "not_sent", "trace_id": context["trace_id"]}


def test_log_sink_rejects_payloads_from_app_and_dependency_loggers():
    from app.platform.operations.logging import SafeJSONFormatter

    formatter = SafeJSONFormatter()
    for logger in ("app.core.llm", "httpx"):
        record = logging.LogRecord(logger, logging.ERROR, __file__, 1,
                                   "LLM result: secret-resume-and-key", (), None)
        value = formatter.format(record)
        assert "secret-resume-and-key" not in value
        assert json.loads(value)["event"] == "log"
    record = logging.LogRecord("app.platform.worker", logging.INFO, __file__, 1,
                               "job_finished", (), None)
    assert json.loads(formatter.format(record))["event"] == "job_finished"


def test_incremental_upgrade_preserves_usage_and_creates_honest_configuration_baseline():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    def migrate(connection, filename):
        path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / filename
        spec = importlib.util.spec_from_file_location("engineering_upgrade_fixture", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()

    engine = create_engine("sqlite://")
    with engine.begin() as db:
        migrate(db, "20261015_0001_platform.py")
        db.execute(text("INSERT INTO platform_ai_calls(id,scene,status,duration_ms,input_tokens,output_tokens,created_at) VALUES ('old-call','evaluate','completed',10,100,23,'2026-10-20')"))
        db.execute(text("INSERT INTO platform_agents(id,name,prompt,tools,enabled,version,created_at) VALUES ('old-agent','test','test','[\"lookup_resume_field\"]',1,3,'2026-10-20')"))
        migrate(db, "20261020_0002_engineering.py")
        assert db.scalar(text("SELECT reserved_tokens FROM platform_ai_calls")) == 123
        row = db.execute(text("SELECT config,actor_id,version FROM platform_config_revisions")).one()
        assert json.loads(row.config)["tools"] == ["lookup_resume_field"]
        assert row.actor_id is None and row.version == 3
    engine.dispose()


@pytest.mark.asyncio
async def test_gate_has_finite_waiting_and_releases_after_cancellation(monkeypatch):
    monkeypatch.setattr(settings, "AI_MAX_WAITERS", 1)
    gate = guard.Gate(1)
    async with gate.enter(time.monotonic() + 5):
        async def wait():
            async with gate.enter(time.monotonic() + 5):
                pass
        queued = asyncio.create_task(wait())
        while gate.waiters != 1:
            await asyncio.sleep(0)
        started = time.monotonic()
        with pytest.raises(PlatformError, match="队列已满"):
            async with gate.enter(time.monotonic() + 5):
                pass
        assert time.monotonic() - started < .5
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
    assert gate.active == gate.waiters == 0


def test_circuit_opens_and_allows_only_one_half_open_probe(monkeypatch):
    monkeypatch.setattr(settings, "AI_CIRCUIT_MIN_CALLS", 10)
    breaker = guard.Circuit()
    for _ in range(10):
        breaker.enter()
        breaker.finish(True)
    with pytest.raises(PlatformError) as failure:
        breaker.enter()
    assert failure.value.code == "AI_CIRCUIT_OPEN"
    breaker.open_until = time.monotonic() - 1
    breaker.enter()
    with pytest.raises(PlatformError):
        breaker.enter()
    breaker.finish(False)
    breaker.enter()
    assert breaker.open_until == 0


@pytest.mark.asyncio
async def test_quota_reserves_input_and_maximum_output_before_send(database, monkeypatch):
    owner = await user(database)
    monkeypatch.setattr(settings, "TOKEN_QUOTA_PER_USER", 1000)
    cfg = {**config(), "_input_budget": 200, "_output_budget": 700}
    first = await guard.reserve(database.sessions, cfg, {"user_id": owner})
    with pytest.raises(PlatformError) as failure:
        await guard.reserve(database.sessions, cfg, {"user_id": owner})
    assert failure.value.code == "AI_QUOTA_EXCEEDED"
    await guard.settle(database.sessions, first, time.monotonic(), "completed", {"prompt_tokens": 100, "completion_tokens": 10})
    async with database.sessions() as db:
        assert (await db.get(AiCall, first)).reserved_tokens == 110
        assert await db.scalar(select(func.count()).select_from(AiPermit)) == 0


@pytest.mark.asyncio
async def test_shared_credential_alias_cannot_bypass_capacity(database, monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER_CONCURRENCY", 1)
    first, second = config(), config()
    assert first["encrypted_key"] != second["encrypted_key"]
    assert guard.credential_scope(first) == guard.credential_scope(second)
    await guard.reserve(database.sessions, first, {})
    with pytest.raises(PlatformError) as failure:
        await guard.reserve(database.sessions, second, {})
    assert failure.value.code == "AI_OVERLOADED"


@pytest.mark.asyncio
async def test_healthy_inflight_requests_do_not_trip_unknown_outcome_limit(database, monkeypatch):
    monkeypatch.setattr(settings, "AI_UNKNOWN_LIMIT", 1)
    monkeypatch.setattr(settings, "AI_PROVIDER_CONCURRENCY", 4)
    first = await guard.reserve(database.sessions, config(), {})
    await guard.mark_sent(database.sessions, first)
    # In-flight requests are pending, rather than abandoned unknown attempts.
    second = await guard.reserve(database.sessions, config(), {})
    assert second != first
    await guard.settle(database.sessions, first, time.monotonic(), "unknown")
    with pytest.raises(PlatformError) as failure:
        await guard.reserve(database.sessions, config(), {})
    assert failure.value.code == "AI_RESULT_UNKNOWN" and failure.value.outcome == "not_sent"


@pytest.mark.asyncio
async def test_known_flight_result_survives_business_failure_and_manual_retry(database):
    calls = []
    context = {"operation_id": new_id(), "user_id": "u", "resource_id": "r", "snapshot_version": 1}
    async def invoke():
        calls.append(1)
        return {"score": 83}
    assert await flights.run(database.sessions, context, {}, {"node": "evaluate"}, invoke) == {"score": 83}
    assert await flights.run(database.sessions, {**context, "generation": 1}, {}, {"node": "evaluate"}, invoke) == {"score": 83}
    assert calls == [1]


@pytest.mark.asyncio
async def test_expired_flight_is_unknown_and_never_automatically_taken_over(database):
    operation = new_id()
    context = {"operation_id": operation}
    key = flights.digest([[None, None, operation, None, {}, {}], 0])
    async with database.sessions() as db, db.begin():
        db.add(AiFlight(id=key, operation_id=operation, generation=0, status="running", owner_token=new_id(),
                        lease_until=utcnow() - timedelta(seconds=5)))
    calls = []
    async def invoke():
        calls.append(1)
        return "known"
    with pytest.raises(PlatformError) as failure:
        await flights.run(database.sessions, context, {}, {}, invoke)
    assert failure.value.unknown and not calls
    assert await flights.run(database.sessions, {**context, "generation": 1}, {}, {}, invoke) == "known"
    assert calls == [1]


@pytest.mark.asyncio
async def test_flight_owner_and_follower_share_one_result_without_redis(database):
    if database.dialect != "postgresql":
        pytest.skip("Concurrent ownership requires PostgreSQL advisory locks")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    context = {"operation_id": new_id()}
    async def invoke():
        calls.append(1)
        entered.set()
        await release.wait()
        return {"ok": True}
    owner = asyncio.create_task(flights.run(database.sessions, context, {}, {}, invoke))
    await asyncio.wait_for(entered.wait(), 5)
    follower = asyncio.create_task(flights.run(database.sessions, context, {}, {}, invoke))
    release.set()
    assert await asyncio.gather(owner, follower) == [{"ok": True}, {"ok": True}]
    assert calls == [1]


@pytest.mark.asyncio
async def test_dispatch_publish_holds_no_transaction_and_repairs_broker_loss(database, monkeypatch):
    published = []
    async with database.sessions() as db, db.begin():
        job = Outbox(id=new_id(), kind="report", aggregate_id=new_id(), payload={}, status="pending", execution_status="queued")
        db.add(job)
    def send(*args, **kwargs):
        published.append(kwargs)
    monkeypatch.setattr(worker.celery_app, "send_task", send)
    assert await worker.dispatch_once() == 1
    async with database.sessions() as db, db.begin():
        row = await db.get(Outbox, job.id)
        row.published_at = row.next_delivery_at = utcnow() - timedelta(minutes=10)
    assert await worker.dispatch_once() == 1
    assert len(published) == 2 and published[0]["task_id"] != published[1]["task_id"]
    assert published[0]["args"][0] == published[1]["args"][0] == job.id
    assert published[0]["queue"] == "report"
    async with database.sessions() as db, db.begin():
        row = await db.get(Outbox, job.id)
        row.execution_status = "completed"
        row.published_at = row.next_delivery_at = utcnow() - timedelta(minutes=10)
    assert await worker.dispatch_once() == 0


@pytest.mark.asyncio
async def test_late_owner_cannot_commit_or_release_replacement_permit(database):
    call_id = await guard.reserve(database.sessions, config(), {})
    async with database.sessions() as db, db.begin():
        row = await db.get(AiPermit, call_id)
        row.owner_token = "replacement-owner"
    with pytest.raises(PlatformError, match="迟到结果"):
        await guard.settle(database.sessions, call_id, time.monotonic(), "completed", {"prompt_tokens": 1, "completion_tokens": 1})
    async with database.sessions() as db:
        assert (await db.get(AiPermit, call_id)).owner_token == "replacement-owner"
        assert (await db.get(AiCall, call_id)).status == "running"


@pytest.mark.asyncio
async def test_invalid_known_result_is_never_reused_as_success(database):
    from app.platform.domain.results import validate_result

    context = {"operation_id": new_id()}
    async def invalid():
        return validate_result("evaluate", {"score": 999})
    with pytest.raises(PlatformError) as failure:
        await flights.run(database.sessions, context, {}, {}, invalid)
    assert failure.value.outcome == "known"
    async with database.sessions() as db:
        row = await db.scalar(select(AiFlight))
        assert row.status == "failed" and row.result is None
    async def valid():
        return {"score": 80}
    assert await flights.run(database.sessions, {**context, "generation": 1}, {}, {}, valid) == {"score": 80}


@pytest.mark.asyncio
async def test_rekey_rotates_current_and_historical_keys_in_one_transaction(database, monkeypatch):
    from app.platform.models import ConfigRevision, Provider
    from app.platform.operations import rekey

    monkeypatch.setattr(rekey, "async_session_maker", database.sessions)
    identity = new_id()
    encrypted = encrypt_key("test-provider-secret")
    async with database.sessions() as db, db.begin():
        db.add(Provider(id=identity, name="test", model="test", base_url="https://example.invalid", encrypted_key=encrypted, version=3))
        db.add(ConfigRevision(id=new_id(), kind="provider", entity_id=identity, version=2,
                              config={"encrypted_key": encrypted}, deleted=False))
    new_secret = "test-only-new-stable-secret-at-least-32-characters"
    await rekey.reencrypt(settings.SECRET_KEY, new_secret)
    async with database.sessions() as db:
        row = await db.get(Provider, identity)
        revision = await db.scalar(select(ConfigRevision))
        assert row.version == 3 and revision.version == 2
        for value in (row.encrypted_key, revision.config["encrypted_key"]):
            assert rekey.cipher(new_secret).decrypt(value.encode()) == b"test-provider-secret"
            assert value != encrypted


@pytest.mark.asyncio
async def test_monitoring_deadline_drops_observation_without_replacing_business_result(monkeypatch):
    from app.platform.operations import telemetry

    @asynccontextmanager
    async def unavailable():
        await asyncio.sleep(10)
        yield

    monkeypatch.setattr(runtime, "async_session_maker", unavailable)
    monkeypatch.setattr(telemetry, "WRITE_TIMEOUT_SECONDS", .01)
    with traced(new=True):
        await asyncio.wait_for(telemetry.record("admission"), .5)
        await asyncio.wait_for(telemetry.observe_circuit(config(), guard.Circuit()), .5)


@pytest.mark.asyncio
async def test_metrics_aggregate_processes_and_export_valid_histograms(database, monkeypatch):
    from app.platform.operations import metrics, telemetry
    from app.platform.operations.telemetry import observe_circuit, record

    monkeypatch.setattr(metrics, "async_session_maker", database.sessions)
    # This asserts aggregation, independently of the best-effort write deadline.
    # NullPool fixtures open a new TCP connection per observation on Windows.
    monkeypatch.setattr(telemetry, "WRITE_TIMEOUT_SECONDS", 5)
    with traced(new=True):
        await record("admission", scene="evaluate", workload="interactive", duration_ms=10)
        await record("admission", scene="evaluate", workload="interactive", duration_ms=20)
        breaker = guard.Circuit()
        breaker.open_until = time.monotonic() + 30
        await observe_circuit(config(), breaker)
    exported = await metrics.render_metrics()
    assert 'platform_admission_total{scene="evaluate",workload="interactive"} 2.0' in exported
    assert 'platform_admission_seconds_sum{scene="evaluate",workload="interactive"} 0.03' in exported
    assert 'platform_circuits{scene="evaluate",state="open"} 1.0' in exported
    assert 'platform_ai_duration_seconds_count 0.0' in exported
    assert 'platform_job_wait_seconds_count{workload="prepare"} 0.0' in exported
    assert "one-shared-credential" not in exported
    call_id = new_id()
    async with database.sessions() as db, db.begin():
        db.add(AiCall(id=call_id, scene="evaluate", status="running", duration_ms=0))
    exported = await metrics.render_metrics()
    assert 'platform_ai_inflight_attempts{scene="evaluate"} 1.0' in exported
    assert 'platform_ai_requests_total{scene="evaluate"} 1.0' in exported
    assert 'platform_ai_attempts_total{scene="evaluate",outcome="running"}' not in exported
    async with database.sessions() as db, db.begin():
        row = await db.get(AiCall, call_id)
        row.status, row.duration_ms = "completed", 500
    exported = await metrics.render_metrics()
    assert 'platform_ai_requests_total{scene="evaluate"} 1.0' in exported
    assert 'platform_ai_attempts_total{scene="evaluate",outcome="completed"} 1.0' in exported
