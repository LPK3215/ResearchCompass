"""Measure ResearchCompass search and task API latency.

This is a repeatable measurement tool, not a synthetic pass/fail report. It
requires a running API and credentials supplied by the caller and exits
non-zero when a caller-provided budget is exceeded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class Sample:
    latency_ms: float
    status_code: int | None
    error: str | None = None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _summary(samples: list[Sample]) -> dict[str, Any]:
    latencies = [sample.latency_ms for sample in samples if sample.error is None]
    errors = [sample.error for sample in samples if sample.error is not None]
    status_counts: dict[str, int] = {}
    for sample in samples:
        key = str(sample.status_code) if sample.status_code is not None else "transport_error"
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "requests": len(samples),
        "successful_samples": len(latencies),
        "errors": len(errors),
        "error_examples": errors[:3],
        "status_counts": status_counts,
        "latency_ms": {
            "min": min(latencies) if latencies else None,
            "mean": statistics.mean(latencies) if latencies else None,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": max(latencies) if latencies else None,
        },
    }


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None,
    expected_statuses: set[int],
) -> Sample:
    started = time.perf_counter()
    try:
        response = await client.request(method, path, json=payload)
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code not in expected_statuses:
            return Sample(latency_ms, response.status_code, f"unexpected status {response.status_code}")
        return Sample(latency_ms, response.status_code)
    except httpx.HTTPError as exc:
        return Sample((time.perf_counter() - started) * 1000, None, type(exc).__name__)


async def _measure_endpoint(
    client: httpx.AsyncClient,
    path: str,
    *,
    method: str,
    payload: dict[str, Any] | None,
    requests: int,
    concurrency: int,
    expected_statuses: set[int],
) -> list[Sample]:
    semaphore = asyncio.Semaphore(concurrency)

    async def run_one() -> Sample:
        async with semaphore:
            return await _request(
                client,
                method,
                path,
                payload=payload,
                expected_statuses=expected_statuses,
            )

    return list(await asyncio.gather(*(run_one() for _ in range(requests))))


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("RESEARCH_BASE_URL", "http://localhost:5051"))
    parser.add_argument("--token", default=os.getenv("RESEARCH_ACCESS_TOKEN"), help="Bearer token")
    parser.add_argument("--kb-id", default=os.getenv("RESEARCH_KB_ID"), required=not os.getenv("RESEARCH_KB_ID"))
    parser.add_argument("--query", default=os.getenv("RESEARCH_QUERY", "evidence"))
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--search-p95-ms", type=float)
    parser.add_argument("--tasks-p95-ms", type=float)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.concurrency > args.requests:
        parser.error("--requests must be >= 1 and --concurrency must be between 1 and --requests")
    if not args.token:
        parser.error("--token or RESEARCH_ACCESS_TOKEN is required")
    return args


async def _main(args: argparse.Namespace) -> int:
    headers = {"Authorization": f"Bearer {args.token}"}
    search_path = f"{args.base_url.rstrip('/')}/api/research/databases/{args.kb_id}/search"
    tasks_path = f"{args.base_url.rstrip('/')}/api/tasks?limit=100"
    search_payload = {"query": args.query, "retrieval_mode": "local_hybrid", "top_k": 10, "recall_top_k": 50}
    async with httpx.AsyncClient(headers=headers, timeout=args.timeout) as client:
        search_samples, task_samples = await asyncio.gather(
            _measure_endpoint(
                client,
                search_path,
                method="POST",
                payload=search_payload,
                requests=args.requests,
                concurrency=args.concurrency,
                expected_statuses={200},
            ),
            _measure_endpoint(
                client,
                tasks_path,
                method="GET",
                payload=None,
                requests=args.requests,
                concurrency=args.concurrency,
                expected_statuses={200},
            ),
        )
    result = {
        "config": {
            "base_url": args.base_url,
            "kb_id": args.kb_id,
            "requests": args.requests,
            "concurrency": args.concurrency,
            "query": args.query,
        },
        "search": _summary(search_samples),
        "tasks": _summary(task_samples),
    }
    budgets = {"search": args.search_p95_ms, "tasks": args.tasks_p95_ms}
    failures = []
    for name, budget in budgets.items():
        p95 = result[name]["latency_ms"]["p95"]
        if budget is not None and (p95 is None or p95 > budget):
            failures.append(f"{name} p95 {p95}ms exceeds budget {budget}ms")
        if result[name]["errors"]:
            failures.append(f"{name} had {result[name]['errors']} failed requests")
    result["budgets"] = budgets
    result["budget_failures"] = failures
    print(json.dumps(result, ensure_ascii=False, indent=None if args.json else 2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_args())))
