"""Bounded load generator emits latency/rejection evidence, never capacity claims."""

import argparse
import asyncio
import json
import platform
import statistics
import time

import httpx


async def main(args):
    timings, statuses = [], {}
    queue = asyncio.Queue(maxsize=args.concurrency)
    async with httpx.AsyncClient(base_url=args.url, timeout=30, trust_env=False, limits=httpx.Limits(max_connections=args.concurrency)) as client:
        async def worker():
            while await queue.get() is not None:
                started = time.perf_counter()
                try:
                    response = await client.get(args.path)
                    status = response.status_code
                except httpx.HTTPError:
                    status = 0
                timings.append(time.perf_counter() - started)
                statuses[status] = statuses.get(status, 0) + 1
                queue.task_done()
        tasks = [asyncio.create_task(worker()) for _ in range(args.concurrency)]
        started = time.perf_counter()
        for _ in range(args.requests):
            await queue.put(True)
        await queue.join()
        for _ in tasks:
            await queue.put(None)
        await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - started
    values = sorted(timings)
    print(json.dumps({"host": platform.platform(), "concurrency": args.concurrency, "requests": args.requests,
        "throughput": len(values) / elapsed, "p50_ms": statistics.median(values) * 1000,
        "p95_ms": values[int((len(values) - 1) * .95)] * 1000, "p99_ms": values[int((len(values) - 1) * .99)] * 1000,
        "statuses": statuses}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18081")
    parser.add_argument("--path", default="/health/ready")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 256 or args.requests < 1:
        parser.error("Choose finite positive concurrency and request counts")
    asyncio.run(main(args))
