from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections import Counter
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field


ROUTER_URL = os.getenv("ROUTER_URL", "http://localhost:8000")
TELEMETRY_URL = os.getenv("TELEMETRY_URL", "http://localhost:8002")
TRACE_FILE = Path(os.getenv("TRACE_FILE", "./traces/sample-trace.jsonl"))
WORKLOAD_CONFIG_FILE = Path(os.getenv("WORKLOAD_CONFIG_FILE", "/app/configs/default-workload.json"))
WORKLOAD_CONCURRENCY = int(os.getenv("WORKLOAD_CONCURRENCY", "8"))
WORKLOAD_SEED = int(os.getenv("WORKLOAD_SEED", "7"))


class TraceEntry(BaseModel):
    segment_id: str
    size_bytes: int = Field(..., gt=0)
    frequency: int = Field(..., ge=1)
    recency_seconds: int = Field(..., ge=0)
    query_type: str | None = None
    object_class: str | None = None
    workload_phase: str | None = None
    semantic_tags: list[str] = Field(default_factory=list)


class SegmentSpec(BaseModel):
    segment_id: str
    size_bytes: int = Field(..., gt=0)
    weight: int = Field(..., ge=1)
    query_type: str
    object_class: str
    semantic_tags: list[str] = Field(default_factory=list)


class PhaseSpec(BaseModel):
    name: str
    requests: int = Field(..., ge=1)
    hotset: list[str] = Field(default_factory=list)
    boost: int = Field(3, ge=1)
    query_type_override: str | None = None
    semantic_tags: list[str] = Field(default_factory=list)
    active_workers: int | None = Field(default=None, ge=1)


class BurstSpec(BaseModel):
    every_n_requests: int = Field(10, ge=1)
    burst_length: int = Field(3, ge=1)
    hotset: list[str] = Field(default_factory=list)
    semantic_tags: list[str] = Field(default_factory=list)


class WorkloadConfig(BaseModel):
    mode: Literal["stationary", "bursty", "phase-shifted", "scale-event"]
    seed: int | None = None
    concurrency: int | None = Field(default=None, ge=1)
    requests: int | None = Field(default=None, ge=1)
    repeat: int = Field(1, ge=1)
    segments: list[SegmentSpec] = Field(default_factory=list)
    phases: list[PhaseSpec] = Field(default_factory=list)
    burst: BurstSpec | None = None


def load_trace_entries() -> list[TraceEntry]:
    entries: list[TraceEntry] = []
    for line in TRACE_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(TraceEntry.model_validate(json.loads(line)))
    return entries


def load_workload_config() -> WorkloadConfig:
    payload = json.loads(WORKLOAD_CONFIG_FILE.read_text())
    return WorkloadConfig.model_validate(payload)


def weighted_segments(
    base_segments: list[SegmentSpec],
    hotset: list[str] | None = None,
    hotset_boost: int = 1,
) -> tuple[list[SegmentSpec], list[int]]:
    hotset_ids = set(hotset or [])
    segments = list(base_segments)
    weights = [
        segment.weight * (hotset_boost if segment.segment_id in hotset_ids else 1)
        for segment in segments
    ]
    return segments, weights


def build_entry(
    segment: SegmentSpec,
    phase_name: str,
    rng: random.Random,
    extra_tags: list[str] | None = None,
    query_type_override: str | None = None,
    active_workers: int | None = None,
) -> TraceEntry:
    semantic_tags = list(segment.semantic_tags)
    if extra_tags:
        semantic_tags.extend(extra_tags)
    if active_workers is not None:
        semantic_tags.append(f"active-workers:{active_workers}")
    return TraceEntry(
        segment_id=segment.segment_id,
        size_bytes=segment.size_bytes,
        frequency=rng.randint(1, 8),
        recency_seconds=rng.randint(5, 600),
        query_type=query_type_override or segment.query_type,
        object_class=segment.object_class,
        workload_phase=phase_name,
        semantic_tags=semantic_tags,
    )


def generate_stationary(config: WorkloadConfig, rng: random.Random) -> list[TraceEntry]:
    request_count = config.requests or 24
    segments, weights = weighted_segments(config.segments)
    entries: list[TraceEntry] = []
    for _ in range(request_count):
        segment = rng.choices(segments, weights=weights, k=1)[0]
        entries.append(build_entry(segment, phase_name="stationary", rng=rng, extra_tags=["mode:stationary"]))
    return entries


def generate_bursty(config: WorkloadConfig, rng: random.Random) -> list[TraceEntry]:
    request_count = config.requests or 24
    burst = config.burst or BurstSpec()
    base_segments, base_weights = weighted_segments(config.segments)
    burst_segments, burst_weights = weighted_segments(config.segments, burst.hotset, hotset_boost=6)
    entries: list[TraceEntry] = []

    request_index = 0
    while request_index < request_count:
        if request_index > 0 and request_index % burst.every_n_requests == 0:
            for _ in range(min(burst.burst_length, request_count - request_index)):
                segment = rng.choices(burst_segments, weights=burst_weights, k=1)[0]
                entries.append(
                    build_entry(
                        segment,
                        phase_name="bursty-burst",
                        rng=rng,
                        extra_tags=["mode:bursty", "burst"] + burst.semantic_tags,
                    )
                )
                request_index += 1
        else:
            segment = rng.choices(base_segments, weights=base_weights, k=1)[0]
            entries.append(build_entry(segment, phase_name="bursty-base", rng=rng, extra_tags=["mode:bursty"]))
            request_index += 1
    return entries


def generate_phase_shifted(config: WorkloadConfig, rng: random.Random) -> list[TraceEntry]:
    entries: list[TraceEntry] = []
    for phase in config.phases:
        segments, weights = weighted_segments(config.segments, phase.hotset, hotset_boost=phase.boost)
        for _ in range(phase.requests):
            segment = rng.choices(segments, weights=weights, k=1)[0]
            entries.append(
                build_entry(
                    segment,
                    phase_name=phase.name,
                    rng=rng,
                    extra_tags=["mode:phase-shifted"] + phase.semantic_tags,
                    query_type_override=phase.query_type_override,
                    active_workers=phase.active_workers,
                )
            )
    return entries


def generate_scale_event(config: WorkloadConfig, rng: random.Random) -> list[TraceEntry]:
    entries: list[TraceEntry] = []
    for phase in config.phases:
        phase_tags = ["mode:scale-event"] + phase.semantic_tags
        segments, weights = weighted_segments(config.segments, phase.hotset, hotset_boost=phase.boost)
        for _ in range(phase.requests):
            segment = rng.choices(segments, weights=weights, k=1)[0]
            entries.append(
                build_entry(
                    segment,
                    phase_name=phase.name,
                    rng=rng,
                    extra_tags=phase_tags,
                    query_type_override=phase.query_type_override,
                )
            )
    return entries


def generate_from_mode(config: WorkloadConfig, rng: random.Random) -> list[TraceEntry]:
    generators = {
        "stationary": generate_stationary,
        "bursty": generate_bursty,
        "phase-shifted": generate_phase_shifted,
        "scale-event": generate_scale_event,
    }
    generated = generators[config.mode](config, rng)
    return generated * config.repeat


async def send_request(client: httpx.AsyncClient, semaphore: asyncio.Semaphore, entry: TraceEntry) -> dict[str, object]:
    async with semaphore:
        started_at = time.perf_counter()
        response = await client.post(f"{ROUTER_URL}/read", json=entry.model_dump())
        latency_ms = (time.perf_counter() - started_at) * 1000
        return {
            "status_code": response.status_code,
            "latency_ms": latency_ms,
            "response": response.json(),
            "request": entry.model_dump(),
        }


async def reset_telemetry(client: httpx.AsyncClient) -> None:
    await client.post(f"{TELEMETRY_URL}/reset")


async def scale_router(client: httpx.AsyncClient, active_worker_count: int, reason: str) -> dict[str, object]:
    response = await client.post(
        f"{ROUTER_URL}/admin/scale",
        json={"active_worker_count": active_worker_count, "reason": reason},
    )
    response.raise_for_status()
    return response.json()


async def fetch_telemetry_summary(client: httpx.AsyncClient) -> dict[str, object]:
    response = await client.get(f"{TELEMETRY_URL}/summary")
    response.raise_for_status()
    return response.json()


def summarize_requests(entries: list[TraceEntry]) -> dict[str, object]:
    phase_counts = Counter(entry.workload_phase or "unknown" for entry in entries)
    query_types = Counter(entry.query_type or "unknown" for entry in entries)
    object_classes = Counter(entry.object_class or "unknown" for entry in entries)
    return {
        "total_requests": len(entries),
        "phases": dict(phase_counts),
        "query_types": dict(query_types),
        "object_classes": dict(object_classes),
    }


async def main() -> None:
    config = load_workload_config()
    effective_seed = config.seed if config.seed is not None else WORKLOAD_SEED
    rng = random.Random(effective_seed)
    generated_entries = generate_from_mode(config, rng)
    fallback_trace_entries = load_trace_entries()
    entries = generated_entries if generated_entries else fallback_trace_entries
    concurrency = config.concurrency or WORKLOAD_CONCURRENCY
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=10.0) as client:
        await reset_telemetry(client)

        if config.mode == "scale-event" and config.phases:
            results: list[dict[str, object]] = []
            for phase in config.phases:
                phase_entries = [entry for entry in entries if entry.workload_phase == phase.name]
                target_workers = phase.active_workers or 1
                await scale_router(client, target_workers, reason=f"phase:{phase.name}")
                phase_results = await asyncio.gather(
                    *(send_request(client, semaphore, entry) for entry in phase_entries)
                )
                results.extend(phase_results)
        else:
            results = await asyncio.gather(
                *(send_request(client, semaphore, entry) for entry in entries)
            )

    successes = [result for result in results if result["status_code"] == 200]
    failures = [result for result in results if result["status_code"] != 200]
    average_latency_ms = (
        sum(float(result["latency_ms"]) for result in results) / len(results) if results else 0.0
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        telemetry_summary = await fetch_telemetry_summary(client)

    print(
        json.dumps(
            {
                "mode": config.mode,
                "seed": effective_seed,
                "concurrency": concurrency,
                "requests_sent": len(results),
                "successful_requests": len(successes),
                "failed_requests": len(failures),
                "average_latency_ms": round(average_latency_ms, 3),
                "request_mix": summarize_requests(entries),
                "telemetry_summary": telemetry_summary,
                "sample_request": entries[0].model_dump() if entries else None,
                "sample_response": successes[0]["response"] if successes else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
