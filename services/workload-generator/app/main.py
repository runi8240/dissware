from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, Field


COMPUTE_WORKER_URL = os.getenv("COMPUTE_WORKER_URL", "http://localhost:8000")
TRACE_FILE = Path(os.getenv("TRACE_FILE", "./traces/sample-trace.jsonl"))
WORKLOAD_CONCURRENCY = int(os.getenv("WORKLOAD_CONCURRENCY", "8"))
WORKLOAD_REPEAT = int(os.getenv("WORKLOAD_REPEAT", "1"))


class TraceEntry(BaseModel):
    segment_id: str
    size_bytes: int = Field(..., gt=0)
    frequency: int = Field(..., ge=1)
    recency_seconds: int = Field(..., ge=0)


def load_trace_entries() -> list[TraceEntry]:
    entries: list[TraceEntry] = []
    for line in TRACE_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(TraceEntry.model_validate(json.loads(line)))
    return entries


async def send_request(client: httpx.AsyncClient, semaphore: asyncio.Semaphore, entry: TraceEntry) -> dict[str, object]:
    async with semaphore:
        started_at = time.perf_counter()
        response = await client.post(f"{COMPUTE_WORKER_URL}/read", json=entry.model_dump())
        latency_ms = (time.perf_counter() - started_at) * 1000
        return {
            "status_code": response.status_code,
            "latency_ms": latency_ms,
            "response": response.json(),
        }


async def main() -> None:
    entries = load_trace_entries()
    expanded_entries = entries * WORKLOAD_REPEAT
    semaphore = asyncio.Semaphore(WORKLOAD_CONCURRENCY)

    async with httpx.AsyncClient(timeout=10.0) as client:
        results = await asyncio.gather(
            *(send_request(client, semaphore, entry) for entry in expanded_entries)
        )

    successes = [result for result in results if result["status_code"] == 200]
    failures = [result for result in results if result["status_code"] != 200]
    average_latency_ms = (
        sum(float(result["latency_ms"]) for result in results) / len(results) if results else 0.0
    )

    print(
        json.dumps(
            {
                "requests_sent": len(results),
                "successful_requests": len(successes),
                "failed_requests": len(failures),
                "average_latency_ms": round(average_latency_ms, 3),
                "sample_response": successes[0]["response"] if successes else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
