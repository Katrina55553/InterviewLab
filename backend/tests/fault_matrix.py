"""Destructive actions hard-bound to the isolated engineering Compose project.

Run from repository root: python backend/tests/fault_matrix.py
Evidence contains internal IDs, states, and counts only.
"""

import concurrent.futures
import contextlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-p", "resumeagent-engineering-test", "-f", str(ROOT / "docker-compose.test.yml")]
EVIDENCE = ROOT / "backend" / ".test-work" / "faults"
BASE = "http://127.0.0.1:18081"
FAULTY = "http://127.0.0.1:18083"
PROVIDER = "http://127.0.0.1:18082"


def compose(*args, point=None, worker_point=None, dispatcher_point=None):
    env = {**os.environ, "FAULT_POINT": point or "disabled", "WORKER_FAULT_POINT": worker_point or "disabled",
           "DISPATCHER_FAULT_POINT": dispatcher_point or "disabled", "TEST_DEMO_MODE": "false"}
    subprocess.run([*COMPOSE, *args], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)


def output(*args):
    return subprocess.check_output([*COMPOSE, *args], cwd=ROOT, text=True).strip()


def sql(statement):
    return output("exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "resume_agent_test", "-Atc", statement)


def marker_after(point, stamps):
    return wait_for(lambda: [p for p in EVIDENCE.glob(f"*-{point}.json") if p.stat().st_mtime_ns > stamps.get(p, 0)], bool)


def clear_releases(point):
    for path in EVIDENCE.glob(f"*-{point}.release"):
        path.unlink()


def wait_for(read, accept, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = read()
            if accept(result):
                return result
        except (httpx.TransportError, KeyError):
            pass
        time.sleep(.1)
    raise AssertionError("Explicit state/checkpoint was not reached before deadline")


def checked(response):
    assert response.is_success, f"Unexpected response status {response.status_code}"
    return response.json()


@contextlib.contextmanager
def client():
    value = httpx.Client(base_url=BASE, timeout=60, trust_env=False)
    checked(value.post("/api/auth/register", json={"username": "fault_" + uuid.uuid4().hex[:16], "password": "test-only-password-2026"}))
    value.headers["X-CSRF-Token"] = value.cookies["ra_csrf"]
    try:
        yield value
    finally:
        value.close()


def prepared(value):
    resume = checked(value.post("/api/resumes", files={"file": ("test.txt", b"Python PostgreSQL test project responsibilities and measurable engineering evidence.", "text/plain")}))
    return wait_for(lambda: checked(value.get("/api/resumes/" + resume["id"])), lambda row: row["status"] == "ready")


def interview(value):
    resume = prepared(value)
    run = checked(value.post("/api/interviews", json={"resume_id": resume["id"]}))
    return checked(value.post(f"/api/interviews/{run['id']}/commands", json={"type": "start", "request_id": str(uuid.uuid4()), "version": 0}))


def calls():
    return httpx.get(PROVIDER + "/control", trust_env=False).json()["calls"]


def fault(point, expected_extra):
    clear_releases(point)
    compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "backend", point=point)
    with client() as value:
        run = interview(value)
        payload = {"type": "answer", "content": "Test evidence", "request_id": str(uuid.uuid4()), "version": run["version"],
                   "expected_question_id": run["current_question"]["id"]}
        before = calls()
        stamps = {p: p.stat().st_mtime_ns for p in EVIDENCE.glob(f"*-{point}.json")}
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(value.post, FAULTY + f"/api/interviews/{run['id']}/commands", json=payload)
            marker = marker_after(point, stamps)
            checkpoint = json.loads(marker[0].read_text(encoding="utf-8"))
            compose("kill", "-s", "SIGKILL", "backend", point=point)
            with contextlib.suppress(httpx.TransportError):
                pending.result(timeout=10)
        saved = wait_for(lambda: checked(value.get(f"/api/interviews/{run['id']}")),
                         lambda row: (row.get("pending_command") or {}).get("status") == "unknown")
        assert saved["version"] == run["version"] and saved["evaluations"] == []
        after_crash = calls()
        retried = checked(value.post(f"/api/interviews/{run['id']}/commands", json={**payload, "request_id": str(uuid.uuid4())}))
        assert retried["version"] == run["version"] + 1 and len(retried["evaluations"]) == 1
        assert calls() == after_crash + expected_extra
        return {"checkpoint": point, "pid": checkpoint["pid"], "trace_id": checkpoint.get("trace_id"),
                "calls_before": before, "calls_after_crash": after_crash, "calls_after_retry": calls(), "accepted_results": 1}


def worker_claim_loss():
    point = "after_job_claim"
    compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "celery", worker_point=point)
    stamps = {p: p.stat().st_mtime_ns for p in EVIDENCE.glob(f"*-{point}.json")}
    with client() as value:
        before = calls()
        resume = checked(value.post("/api/resumes", files={"file": ("worker.txt", b"Python PostgreSQL worker claim fault test with enough text to parse.", "text/plain")}))
        marker = marker_after(point, stamps)
        checkpoint = json.loads(marker[0].read_text(encoding="utf-8"))
        compose("kill", "-s", "SIGKILL", "celery")
        wait_for(lambda: checked(value.get("/api/resumes/" + resume["id"])), lambda row: row["status"] == "failed")
        assert calls() == before
        job = sql(f"SELECT active_job_id FROM platform_resumes WHERE id='{uuid.UUID(resume['id'])}'")
        assert sql(f"SELECT execution_status FROM platform_outbox WHERE id='{uuid.UUID(job)}'") == "unknown"
        compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "celery")
        checked(value.post(f"/api/resumes/{resume['id']}/retry"))
        wait_for(lambda: checked(value.get("/api/resumes/" + resume["id"])), lambda row: row["status"] == "ready")
        return {"checkpoint": point, "pid": checkpoint["pid"], "unknown_before_manual_retry": True,
                "calls_before_retry": before, "calls_after_retry": calls()}


def published_before_ack():
    point = "after_publish_before_ack"
    compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "dispatcher", dispatcher_point=point)
    stamps = {p: p.stat().st_mtime_ns for p in EVIDENCE.glob(f"*-{point}.json")}
    with client() as value:
        resume = checked(value.post("/api/resumes", files={"file": ("publish.txt", b"Python PostgreSQL dispatch publish acknowledgement fault test resume.", "text/plain")}))
        marker_after(point, stamps)
        compose("kill", "-s", "SIGKILL", "dispatcher")
        wait_for(lambda: checked(value.get("/api/resumes/" + resume["id"])), lambda row: row["status"] == "ready")
        job = sql(f"SELECT active_job_id FROM platform_resumes WHERE id='{uuid.UUID(resume['id'])}'")
        before = calls()
        deliveries = [str(uuid.uuid4()), str(uuid.uuid4())]
        code = "import sys; from app.tasks.celery_app import celery_app; [celery_app.send_task('app.platform.worker.execute_job', args=[sys.argv[1]], task_id=i, queue='prepare') for i in sys.argv[2:]]"
        output("exec", "-T", "backend2", "python", "-c", code, job, *deliveries)
        code = "import sys; from app.tasks.celery_app import celery_app; print(all(celery_app.AsyncResult(i).successful() for i in sys.argv[1:]))"
        wait_for(lambda: output("exec", "-T", "backend2", "python", "-c", code, *deliveries), lambda result: result == "True")
        assert calls() == before
        compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "dispatcher")
        return {"checkpoint": point, "duplicate_deliveries": 2, "completed_result_reused": True, "extra_calls": calls() - before}


def simultaneous_commands():
    with client() as value:
        run = interview(value)
        payload = {"type": "answer", "content": "Same answer", "request_id": str(uuid.uuid4()), "version": run["version"],
                   "expected_question_id": run["current_question"]["id"]}
        httpx.post(PROVIDER + "/control", json={"delay": 1}, trust_env=False).raise_for_status()
        before = calls()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                pending = [pool.submit(value.post, base + f"/api/interviews/{run['id']}/commands", json=payload) for base in (BASE, FAULTY)]
                results = [p.result() for p in pending]
            assert all(r.status_code in {200, 409} for r in results) and any(r.status_code == 200 for r in results)
        finally:
            httpx.post(PROVIDER + "/control", json={"delay": 0}, trust_env=False).raise_for_status()
        saved = checked(value.get(f"/api/interviews/{run['id']}"))
        assert len(saved["evaluations"]) == 1 and saved["version"] == run["version"] + 1 and calls() == before + 1
        mismatch = value.post(f"/api/interviews/{run['id']}/commands", json={**payload, "content": "Different answer"})
        competitor = value.post(f"/api/interviews/{run['id']}/commands", json={**payload, "request_id": str(uuid.uuid4())})
        assert mismatch.status_code == competitor.status_code == 409
        return {"checkpoint": "two_instance_same_command", "accepted_results": 1, "physical_calls": calls() - before,
                "mismatched_content_rejected": True, "competing_command_rejected": True}


def stale_owner_resumes():
    point = "after_flight_save"
    clear_releases(point)
    compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "backend", point=point)
    with client() as value:
        run = interview(value)
        payload = {"type": "answer", "content": "Late owner evidence", "request_id": str(uuid.uuid4()), "version": run["version"],
                   "expected_question_id": run["current_question"]["id"]}
        stamps = {p: p.stat().st_mtime_ns for p in EVIDENCE.glob(f"*-{point}.json")}
        before = calls()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(value.post, FAULTY + f"/api/interviews/{run['id']}/commands", json=payload)
            marker = marker_after(point, stamps)[0]
            sql(f"UPDATE platform_commands SET lease_until=timezone('UTC', clock_timestamp())-interval '1 second' WHERE interview_id='{uuid.UUID(run['id'])}' AND status='running'")
            wait_for(lambda: checked(value.get(f"/api/interviews/{run['id']}")),
                     lambda row: (row.get("pending_command") or {}).get("status") == "unknown")
            retried = checked(value.post(f"/api/interviews/{run['id']}/commands", json={**payload, "request_id": str(uuid.uuid4())}))
            marker.with_suffix(".release").touch()
            assert pending.result(timeout=10).status_code == 409
        saved = checked(value.get(f"/api/interviews/{run['id']}"))
        assert saved["version"] == retried["version"] == run["version"] + 1 and len(saved["evaluations"]) == 1
        assert calls() == before + 1
    clear_releases(point)
    compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "backend")
    return {"checkpoint": "stale_owner_resumed", "late_commit_rejected": True, "accepted_results": 1, "physical_calls": calls() - before}


def redis_loss():
    compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "backend")
    with client() as value:
        run = interview(value)
        before = calls()
        compose("stop", "redis")
        try:
            restored = checked(value.get(f"/api/interviews/{run['id']}"))
            assert restored["current_question"] == run["current_question"] and calls() == before
            ready = checked(value.get("/health/ready"))
            assert ready["status"] == "degraded" and ready["capabilities"]["facts"]
            compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "backend")
            cold_read = checked(value.get(FAULTY + f"/api/interviews/{run['id']}"))
            assert cold_read["current_question"] == run["current_question"] and calls() == before
            resume = checked(value.post("/api/resumes", files={"file": ("queued.txt", b"Queued test resume with Python development and system design experience.", "text/plain")}))
        finally:
            compose("start", "redis")
        compose("exec", "-T", "redis", "redis-cli", "FLUSHALL")
        saved = wait_for(lambda: checked(value.get("/api/resumes/" + resume["id"])), lambda row: row["status"] == "ready")
        assert saved["status"] == "ready"
        return {"checkpoint": "redis_loss", "facts_unchanged": True, "cold_start_degraded": True, "queued_recovered": True}


if __name__ == "__main__":
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    results = []
    try:
        for point, extra in (("before_provider_send", 1), ("after_provider_response", 1), ("before_flight_save", 1), ("after_flight_save", 0)):
            results.append(fault(point, extra))
        results.append(redis_loss())
        results.append(simultaneous_commands())
        results.append(worker_claim_loss())
        results.append(published_before_ack())
        results.append(stale_owner_resumes())
    finally:
        (EVIDENCE / "evidence.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "experiments": results}, indent=2))
