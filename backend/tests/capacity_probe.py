"""Bounded two-instance probe against docker-compose.test.yml only.

This measures the local protocol fixture, not production/provider capacity.
"""

import concurrent.futures
import contextlib
import json
import os
import platform
import statistics
import subprocess
import time
import uuid

import httpx
from fault_matrix import BASE, EVIDENCE, FAULTY, PROVIDER, checked, client, interview, output, sql, wait_for


def percentile(values, fraction):
    return round(sorted(values)[int((len(values) - 1) * fraction)] * 1000, 2)


def control(**changes):
    return checked(httpx.post(PROVIDER + "/control", json=changes, trust_env=False))


def resources():
    containers = []
    for name in ("backend", "backend2", "celery"):
        data = json.loads(output("stats", "--no-stream", "--format", "json", name))
        containers.extend(data if isinstance(data, list) else [data])
    return {"containers": containers,
            "database_connections": int(sql("SELECT count(*) FROM pg_stat_activity WHERE datname='resume_agent_test'")),
            "queued_jobs": int(sql("SELECT count(*) FROM platform_outbox WHERE execution_status='queued'"))}


def measure_reads(value, run, count=30):
    samples = []
    for index in range(count):
        started = time.perf_counter()
        assert value.get(f"/api/interviews/{run['id']}").status_code == 200
        if index % 5 == 0:
            assert value.get("/api/auth/me").status_code == 200
        samples.append(time.perf_counter() - started)
    return {"p50_ms": percentile(samples, .5), "p95_ms": percentile(samples, .95), "p99_ms": percentile(samples, .99)}


def send(value, base, chat):
    started = time.perf_counter()
    response = value.post(base + f"/api/chats/{chat['id']}/messages",
        json={"content": "Fixture capacity probe", "request_id": str(uuid.uuid4())})
    error = None
    for block in response.text.split("\n\n"):
        if "event: error" in block:
            error = json.loads(block.split("data: ", 1)[1])["code"]
    assert response.status_code == 200
    finished = time.perf_counter()
    return {"seconds": finished - started, "finished": finished, "error": error, "completed": "event: done" in response.text}


def main():
    docker_cpu, docker_memory = subprocess.check_output(["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"], text=True).split()
    evidence = {"host": platform.platform(), "provider": "isolated fixture", "shared_provider_limit": 2,
        "hardware": {"host_logical_cpus": os.cpu_count(), "machine": platform.machine(),
                     "docker_cpus": int(docker_cpu), "docker_memory_bytes": int(docker_memory)},
        "fixture_users": 10, "fixture_interviews": 1, "read_samples_per_phase": 30}
    with contextlib.ExitStack() as stack:
        # Build the control-plane client before the burst: repeatedly loading
        # the host TLS trust store can outlast the short in-flight window.
        observer = stack.enter_context(httpx.Client(base_url=PROVIDER, timeout=5, trust_env=False))
        users = [stack.enter_context(client()) for _ in range(10)]
        chats = [checked(value.post("/api/chats", json={})) for value in users]
        run = interview(users[0])
        evidence["baseline_reads"] = measure_reads(users[0], run)
        control(reset=True, delay=3, mode="normal")
        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            pending = [pool.submit(send, value, BASE if i % 2 else FAULTY, chat) for i, (value, chat) in enumerate(zip(users, chats, strict=True))]
            wait_for(lambda: checked(observer.get("/control"))["inflight"], lambda n: n == 2, timeout=10)
            evidence["slow_provider_reads"] = measure_reads(users[0], run)
            evidence["resources"] = resources()
            results = [future.result(timeout=30) for future in pending]
        elapsed = max(row["finished"] for row in results) - started
        provider = checked(observer.get("/control"))
        rejected = [row["seconds"] for row in results if row["error"] == "AI_OVERLOADED"]
        assert provider["peak"] <= 2 and rejected and all(row["completed"] or row["error"] == "AI_OVERLOADED" for row in results)
        evidence["burst"] = {"requests": len(results), "concurrency": 10, "throughput": round(len(results) / elapsed, 2),
            "provider_peak": provider["peak"], "rejected": len(rejected), "rejection_rate": len(rejected) / len(results),
            "rejection_p95_ms": percentile(rejected, .95), "p95_ms": percentile([r["seconds"] for r in results], .95)}
        # Sustained failures open this process's breaker; the next probe succeeds after cooldown.
        control(mode="error", delay=0)
        for _ in range(25):
            result = send(users[0], BASE, chats[0])
            if result["error"] == "AI_CIRCUIT_OPEN":
                break
        else:
            raise AssertionError("The process circuit did not open")
        evidence["failed_provider_reads"] = measure_reads(users[0], run)
        control(mode="normal", delay=0)
        time.sleep(3.2)  # Configured test-only circuit cooldown, not a fault checkpoint.
        assert send(users[0], BASE, chats[0])["completed"]
        evidence["circuit_recovered"] = True
        control(mode="hang", delay=0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(send, users[-1], FAULTY, chats[-1])
            wait_for(lambda: checked(observer.get("/control"))["inflight"], lambda n: n > 0)
            evidence["timeout_provider_reads"] = measure_reads(users[0], run)
            timed_out = pending.result(timeout=30)
        assert timed_out["error"] == "AI_TIMEOUT" and timed_out["seconds"] <= 25
        evidence["timeout"] = {"code": timed_out["error"], "elapsed_seconds": round(timed_out["seconds"], 2)}
        control(mode="normal", delay=0)
        evidence["read_p95_ratio_under_failure"] = round(evidence["failed_provider_reads"]["p95_ms"] / max(.01, evidence["baseline_reads"]["p95_ms"]), 3)
        evidence["read_p95_target_met"] = evidence["read_p95_ratio_under_failure"] <= 1.2
        evidence["rejection_p95_target_met"] = evidence["burst"]["rejection_p95_ms"] < 500
        evidence["median_attempt_ms"] = round(statistics.median(r["seconds"] for r in results) * 1000, 2)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "capacity.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    try:
        main()
    finally:
        control(mode="normal", delay=0)
