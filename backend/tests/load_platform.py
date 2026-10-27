"""Bounded HTTP GET load evidence; this does not measure end-to-end AI capacity."""

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--url", default="http://127.0.0.1:18081")
    value.add_argument("--path", default="/health/ready")
    value.add_argument("--requests", type=int, help="Maximum requests (default: 100 unless --duration is supplied)")
    value.add_argument("--duration", type=float, help="Stop issuing requests after this many seconds (maximum: 3600)")
    value.add_argument("--concurrency", type=int, default=8)
    value.add_argument("--timeout", type=float, default=30, help="Per-request timeout in seconds")
    value.add_argument("--expected-status", type=int, nargs="+", default=[200])
    value.add_argument("--max-error-rate", type=float, default=0)
    value.add_argument("--max-p95-ms", type=float)
    value.add_argument("--min-throughput", type=float, help="Minimum successful requests per second")
    value.add_argument("--cookie-env", help="Environment variable containing a Cookie header; never saved in evidence")
    value.add_argument("--output", type=Path, help="Write the same JSON evidence printed to stdout")
    return value


def parse_args(argv=None):
    value = parser()
    args = value.parse_args(argv)
    if not 1 <= args.concurrency <= 256:
        value.error("Concurrency must be between 1 and 256")
    if args.requests is None:
        args.requests = 1_000_000 if args.duration is not None else 100
    if not 1 <= args.requests <= 1_000_000:
        value.error("Requests must be between 1 and 1000000")
    if args.duration is not None and not 0 < args.duration <= 3600:
        value.error("Duration must be positive and at most 3600 seconds")
    if not 0 < args.timeout <= 300:
        value.error("Timeout must be positive and at most 300 seconds")
    if not 0 <= args.max_error_rate <= 1:
        value.error("Maximum error rate must be between 0 and 1")
    if args.max_p95_ms is not None and not 0 < args.max_p95_ms < math.inf:
        value.error("Maximum P95 must be finite and positive")
    if args.min_throughput is not None and not 0 < args.min_throughput < math.inf:
        value.error("Minimum throughput must be finite and positive")
    if any(not 100 <= status <= 599 for status in args.expected_status):
        value.error("Expected statuses must be HTTP status codes")
    try:
        target, path = httpx.URL(args.url), httpx.URL(args.path)
    except httpx.InvalidURL:
        value.error("URL and path must be valid HTTP URL components")
    if target.scheme not in {"http", "https"} or not target.host or target.userinfo or target.query or target.fragment:
        value.error("URL must use HTTP(S) without credentials, query or fragment")
    if not args.path.startswith("/") or path.is_absolute_url or args.path.startswith("//") or path.fragment:
        value.error("Path must be an absolute path on the selected target without a fragment")
    if args.cookie_env and not os.environ.get(args.cookie_env):
        value.error("The Cookie environment variable is empty or missing")
    return args


def percentile_ms(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] * 1000 if values else None


async def run_load(args, *, transport=None):
    timings, statuses = [], {}
    issued = active = peak = 0
    headers = {"Cookie": os.environ[args.cookie_env]} if args.cookie_env else {}
    async with httpx.AsyncClient(base_url=args.url, timeout=args.timeout, trust_env=False, headers=headers,
                                 limits=httpx.Limits(max_connections=args.concurrency), transport=transport) as client:
        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        deadline = started + args.duration if args.duration is not None else None

        async def worker():
            nonlocal issued, active, peak
            while issued < args.requests and (deadline is None or time.perf_counter() < deadline):
                issued += 1
                active += 1
                peak = max(peak, active)
                request_started = time.perf_counter()
                try:
                    # A total deadline also bounds responses that trickle bytes forever.
                    async with asyncio.timeout(args.timeout):
                        response = await client.get(args.path)
                    status = response.status_code
                except (httpx.HTTPError, TimeoutError):
                    status = 0
                finally:
                    active -= 1
                timings.append(time.perf_counter() - request_started)
                statuses[status] = statuses.get(status, 0) + 1
        await asyncio.gather(*(worker() for _ in range(args.concurrency)))
    elapsed = time.perf_counter() - started
    successes = sum(count for status, count in statuses.items() if status in args.expected_status)
    completed = len(timings)
    error_rate = (completed - successes) / completed if completed else 1
    p95 = percentile_ms(timings, .95)
    successful_throughput = successes / elapsed
    checks = {"requests_completed": completed > 0, "error_rate": error_rate <= args.max_error_rate}
    if args.max_p95_ms is not None:
        checks["p95_ms"] = p95 is not None and p95 <= args.max_p95_ms
    if args.min_throughput is not None:
        checks["successful_throughput"] = successful_throughput >= args.min_throughput
    return {
        "scope": "HTTP GET only; not end-to-end AI or production capacity acceptance",
        "started_at": started_at, "host": platform.platform(),
        "target": str(httpx.URL(args.url)), "path": httpx.URL(args.path).path,
        "authenticated": bool(args.cookie_env), "concurrency": args.concurrency, "peak_inflight": peak,
        "request_limit": args.requests, "duration_limit_seconds": args.duration, "timeout_seconds": args.timeout,
        "requests": completed, "successful_requests": successes, "elapsed_seconds": elapsed,
        "throughput": completed / elapsed, "successful_throughput": successful_throughput,
        "p50_ms": statistics.median(timings) * 1000 if timings else None,
        "p95_ms": p95, "p99_ms": percentile_ms(timings, .99), "statuses": statuses,
        "error_rate": error_rate, "thresholds": {"expected_status": args.expected_status,
            "max_error_rate": args.max_error_rate, "max_p95_ms": args.max_p95_ms,
            "min_successful_throughput": args.min_throughput},
        "checks": checks, "passed": all(checks.values()),
    }


def main(argv=None):
    args = parse_args(argv)
    evidence = asyncio.run(run_load(args))
    payload = json.dumps(evidence, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
