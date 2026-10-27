"""The load tool must enforce budgets and fail its SLO gate without leaking credentials."""

import asyncio
import importlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tests import load_platform


@pytest.mark.asyncio
async def test_load_respects_request_and_concurrency_limits():
    active = peak = calls = 0

    async def handle(request):
        nonlocal active, peak, calls
        active += 1
        calls += 1
        peak = max(peak, active)
        await asyncio.sleep(.005)
        active -= 1
        return httpx.Response(200)

    args = load_platform.parse_args(["--requests", "17", "--concurrency", "3"])
    evidence = await load_platform.run_load(args, transport=httpx.MockTransport(handle))
    assert calls == evidence["requests"] == 17
    assert peak == evidence["peak_inflight"] == 3
    assert evidence["passed"] is True


@pytest.mark.asyncio
async def test_duration_stops_issuing_after_current_requests():
    async def handle(request):
        await asyncio.sleep(.03)
        return httpx.Response(200)

    args = load_platform.parse_args(["--duration", ".01", "--concurrency", "2"])
    evidence = await load_platform.run_load(args, transport=httpx.MockTransport(handle))
    assert evidence["requests"] == 2
    assert evidence["passed"] is True


@pytest.mark.asyncio
async def test_errors_latency_and_successful_throughput_gate():
    calls = 0

    async def handle(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(.003)
        if calls == 1:
            raise httpx.ConnectError("Do not include secret request details in evidence", request=request)
        return httpx.Response(429 if calls == 2 else 200)

    args = load_platform.parse_args(["--requests", "4", "--concurrency", "1", "--max-error-rate", ".25",
                                     "--max-p95-ms", ".01", "--min-throughput", "1000000"])
    evidence = await load_platform.run_load(args, transport=httpx.MockTransport(handle))
    assert evidence["statuses"] == {0: 1, 429: 1, 200: 2}
    assert evidence["error_rate"] == .5
    assert evidence["successful_requests"] == 2
    assert evidence["checks"] == {"requests_completed": True, "error_rate": False,
                                  "p95_ms": False, "successful_throughput": False}
    assert evidence["passed"] is False
    assert "secret" not in json.dumps(evidence)


@pytest.mark.asyncio
async def test_total_timeout_bounds_trickling_request():
    async def handle(request):
        await asyncio.sleep(.1)
        return httpx.Response(200)

    args = load_platform.parse_args(["--requests", "1", "--timeout", ".005"])
    evidence = await load_platform.run_load(args, transport=httpx.MockTransport(handle))
    assert evidence["statuses"] == {0: 1}
    assert evidence["passed"] is False


@pytest.mark.asyncio
async def test_authenticated_reads_do_not_save_cookie_or_query(monkeypatch):
    secret = "ra_session=private-session-value"
    monkeypatch.setenv("LOAD_TEST_COOKIE", secret)

    async def handle(request):
        assert request.headers["Cookie"] == secret
        assert request.url.query == b"access_token=private-query-value"
        return httpx.Response(200)

    args = load_platform.parse_args(["--requests", "1", "--cookie-env", "LOAD_TEST_COOKIE",
                                     "--path", "/api/auth/me?access_token=private-query-value"])
    evidence = await load_platform.run_load(args, transport=httpx.MockTransport(handle))
    assert evidence["authenticated"] is True
    assert evidence["path"] == "/api/auth/me"
    assert "private" not in json.dumps(evidence)


def test_failed_gate_writes_evidence_and_returns_nonzero(monkeypatch, tmp_path, capsys):
    async def failed(args):
        return {"passed": False, "checks": {"error_rate": False}}

    monkeypatch.setattr(load_platform, "run_load", failed)
    output = tmp_path / "evidence" / "load.json"
    assert load_platform.main(["--output", str(output)]) == 1
    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("argv", [
    ["--duration", "0"], ["--duration", "nan"], ["--duration", "inf"],
    ["--requests", "0"], ["--requests", "1000001"], ["--concurrency", "257"],
    ["--timeout", "inf"], ["--max-error-rate", "nan"], ["--max-p95-ms", "nan"],
    ["--min-throughput", "inf"], ["--expected-status", "0"],
    ["--url", "https://user:secret@example.org"], ["--url", "https://example.org?secret=hidden"],
    ["--path", "https://another-target.org/"], ["--path", "//another-target.org/"],
])
def test_invalid_or_unbounded_options_rejected(argv):
    with pytest.raises(SystemExit) as raised:
        load_platform.parse_args(argv)
    assert raised.value.code == 2


def test_small_sample_percentile_includes_tail():
    assert load_platform.percentile_ms([.001, .020], .95) == 20


def test_fixture_performance_failure_cannot_pass_ci(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    probe = importlib.import_module("capacity_probe")
    probe.require_performance_targets({"read_p95_target_met": True, "rejection_p95_target_met": True})
    with pytest.raises(AssertionError, match="read_p95_target_met"):
        probe.require_performance_targets({"read_p95_target_met": False, "rejection_p95_target_met": True})


@pytest.fixture
def capacity_probe(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    return importlib.import_module("capacity_probe")


def test_capacity_reads_measure_history_and_auth_independently(capacity_probe, monkeypatch):
    clock = 0.0
    requests = Counter()

    def get(path):
        nonlocal clock
        requests[path] += 1
        clock += .05 if path == "/api/auth/me" else .01
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(capacity_probe, "time", SimpleNamespace(perf_counter=lambda: clock))
    evidence = capacity_probe.measure_reads(SimpleNamespace(get=get), {"id": "private-interview-id"})
    # A history observation must never include the following auth request.
    assert evidence["p95_ms"] == pytest.approx(10)
    assert requests == {"/api/interviews/private-interview-id": 100, "/api/auth/me": 100}
    for endpoint, milliseconds in (("history", 10), ("auth", 50)):
        assert evidence[endpoint]["samples_ms"] == pytest.approx([milliseconds] * 100)
        for percentile in ("p50_ms", "p95_ms", "p99_ms"):
            assert evidence[endpoint][percentile] == pytest.approx(milliseconds)
    assert evidence["p95_ms"] == evidence["history"]["p95_ms"]
    assert "private-interview-id" not in json.dumps(evidence)


@pytest.mark.parametrize(("samples", "fraction", "expected_ms"), [
    ([.001, .020], .95, 20),
    ([.001] * 7 + [.8], .95, 800),
    ([index / 1000 for index in range(1, 101)], .95, 95),
    ([index / 1000 for index in range(1, 101)], .99, 99),
])
def test_capacity_percentiles_use_nearest_rank_including_small_sample_tail(
    capacity_probe, samples, fraction, expected_ms
):
    assert capacity_probe.percentile(samples, fraction) == pytest.approx(expected_ms)


@pytest.mark.parametrize(("history_ratio", "auth_ratio", "rejection_ms", "failed_gate"), [
    (1, 1, 499, None),
    (1.3, 1, 499, "read_p95_target_met"),
    (1, 1.3, 499, "read_p95_target_met"),
    (1.2001, 1, 499, "read_p95_target_met"),
    (1, 1, 500, "rejection_p95_target_met"),
])
def test_capacity_gate_checks_each_endpoint_and_preserves_raw_failed_evidence(
    capacity_probe, monkeypatch, tmp_path, capsys, history_ratio, auth_ratio, rejection_ms, failed_gate
):
    clock = 0.0
    failed_phase = False

    def get(path):
        nonlocal clock
        ratio = auth_ratio if path == "/api/auth/me" else history_ratio
        baseline_seconds = .05 if path == "/api/auth/me" else .01
        clock += baseline_seconds * (ratio if failed_phase else 1)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(capacity_probe, "time", SimpleNamespace(perf_counter=lambda: clock))
    monkeypatch.setattr(capacity_probe, "EVIDENCE", tmp_path / "capacity-evidence")
    value, run = SimpleNamespace(get=get), {"id": "private-run-identifier"}
    baseline = capacity_probe.measure_reads(value, run)
    failed_phase = True
    failed = capacity_probe.measure_reads(value, run)
    evidence = {"baseline_reads": baseline, "failed_provider_reads": failed,
                "burst": {"rejection_p95_ms": rejection_ms}}
    if failed_gate:
        with pytest.raises(AssertionError, match=failed_gate):
            capacity_probe.finalize_evidence(evidence)
    else:
        capacity_probe.finalize_evidence(evidence)
    saved = json.loads((capacity_probe.EVIDENCE / "capacity.json").read_text(encoding="utf-8"))
    assert saved == evidence == json.loads(capsys.readouterr().out)
    assert saved["read_p95_ratios_under_failure"] == pytest.approx({"history": history_ratio, "auth": auth_ratio})
    assert saved["read_p95_target_met"] is (history_ratio <= 1.2 and auth_ratio <= 1.2)
    assert saved["rejection_p95_target_met"] is (rejection_ms < 500)
    assert saved["read_p95_ratio_under_failure"] == saved["read_p95_ratios_under_failure"]["history"]
    for phase in ("baseline_reads", "failed_provider_reads"):
        for endpoint in ("history", "auth"):
            assert len(saved[phase][endpoint]["samples_ms"]) == 100
    assert "private-run-identifier" not in json.dumps(saved)
