from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field


class TelemetryEvent(BaseModel):
    segment_id: str
    worker_id: str
    metadata_hit: bool
    source: Literal["cache", "object-store"]
    placement_decision: Literal["admit", "retain", "evict", "hit"]
    bytes_served: int = Field(..., ge=0)
    latency_ms: float = Field(..., ge=0)
    frequency: int = Field(..., ge=1)
    recency_seconds: int = Field(..., ge=0)
    query_type: str | None = None
    object_class: str | None = None
    workload_phase: str | None = None
    semantic_tags: list[str] = Field(default_factory=list)
    policy_inference_ms: float = Field(..., ge=0)
    redis_lookup_ms: float = Field(..., ge=0)
    object_fetch_ms: float = Field(..., ge=0)
    cache_insert_ms: float = Field(..., ge=0)
    cache_eviction_ms: float = Field(..., ge=0)
    telemetry_emit_ms: float = Field(..., ge=0)
    duplicate_fetch_detected: bool = False
    duplicate_admit_detected: bool = False
    duplicate_fetch_count: int = Field(..., ge=0)
    duplicate_admit_count: int = Field(..., ge=0)
    bytes_fetched_from_object_store: int = Field(..., ge=0)
    ssd_occupancy_bytes: int = Field(..., ge=0)
    eviction_count_total: int = Field(..., ge=0)
    cache_turnover_bytes_total: int = Field(..., ge=0)


app = FastAPI(title="telemetry-collector")


@app.on_event("startup")
async def startup() -> None:
    app.state.events: list[TelemetryEvent] = []


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/events")
async def ingest_event(event: TelemetryEvent) -> dict[str, str]:
    app.state.events.append(event)
    return {"status": "recorded"}


@app.post("/reset")
async def reset() -> dict[str, str]:
    app.state.events = []
    return {"status": "reset"}


def summarize_phase(events: list[TelemetryEvent]) -> dict[str, object]:
    total_requests = len(events)
    cache_hits = sum(1 for event in events if event.metadata_hit)
    avg_latency_ms = mean(event.latency_ms for event in events) if events else 0.0
    workers = Counter(event.worker_id for event in events)
    return {
        "requests": total_requests,
        "cache_hits": cache_hits,
        "cache_hit_rate": (cache_hits / total_requests) if total_requests else 0.0,
        "average_latency_ms": round(avg_latency_ms, 3),
        "workers": dict(workers),
        "policy_inference_ms_avg": round(mean(event.policy_inference_ms for event in events), 3) if events else 0.0,
        "redis_lookup_ms_avg": round(mean(event.redis_lookup_ms for event in events), 3) if events else 0.0,
        "object_fetch_ms_avg": round(mean(event.object_fetch_ms for event in events), 3) if events else 0.0,
    }


@app.get("/summary")
async def summary() -> dict[str, object]:
    events: list[TelemetryEvent] = app.state.events
    total_requests = len(events)
    cache_hits = sum(1 for event in events if event.metadata_hit)
    source_counts = Counter(event.source for event in events)
    decision_counts = Counter(event.placement_decision for event in events)
    avg_latency_ms = mean(event.latency_ms for event in events) if events else 0.0
    phase_counts = Counter(event.workload_phase or "unknown" for event in events)
    phase_breakdown = {
        phase: summarize_phase([event for event in events if (event.workload_phase or "unknown") == phase])
        for phase in phase_counts
    }

    recovery = {}
    if "scale-out" in phase_breakdown and "post-scale" in phase_breakdown:
        recovery["scale_out_to_post_scale_hit_rate_delta"] = round(
            phase_breakdown["post-scale"]["cache_hit_rate"] - phase_breakdown["scale-out"]["cache_hit_rate"],
            4,
        )
        recovery["scale_out_to_post_scale_latency_delta_ms"] = round(
            phase_breakdown["post-scale"]["average_latency_ms"] - phase_breakdown["scale-out"]["average_latency_ms"],
            4,
        )

    return {
        "total_requests": total_requests,
        "cache_hits": cache_hits,
        "cache_hit_rate": (cache_hits / total_requests) if total_requests else 0.0,
        "average_latency_ms": round(avg_latency_ms, 3),
        "bytes_served_total": sum(event.bytes_served for event in events),
        "bytes_fetched_from_object_store_total": sum(event.bytes_fetched_from_object_store for event in events),
        "sources": dict(source_counts),
        "decisions": dict(decision_counts),
        "overheads": {
            "policy_inference_ms_avg": round(mean(event.policy_inference_ms for event in events), 3) if events else 0.0,
            "redis_lookup_ms_avg": round(mean(event.redis_lookup_ms for event in events), 3) if events else 0.0,
            "object_fetch_ms_avg": round(mean(event.object_fetch_ms for event in events), 3) if events else 0.0,
            "cache_insert_ms_avg": round(mean(event.cache_insert_ms for event in events), 3) if events else 0.0,
            "cache_eviction_ms_avg": round(mean(event.cache_eviction_ms for event in events), 3) if events else 0.0,
            "telemetry_emit_ms_avg": round(mean(event.telemetry_emit_ms for event in events), 3) if events else 0.0,
        },
        "cache_stats": {
            "ssd_occupancy_bytes_latest": events[-1].ssd_occupancy_bytes if events else 0,
            "eviction_count_total_latest": events[-1].eviction_count_total if events else 0,
            "cache_turnover_bytes_total_latest": events[-1].cache_turnover_bytes_total if events else 0,
        },
        "duplicates": {
            "duplicate_fetch_events": sum(1 for event in events if event.duplicate_fetch_detected),
            "duplicate_admit_events": sum(1 for event in events if event.duplicate_admit_detected),
            "duplicate_fetch_count_latest": events[-1].duplicate_fetch_count if events else 0,
            "duplicate_admit_count_latest": events[-1].duplicate_admit_count if events else 0,
        },
        "phases": phase_breakdown,
        "recovery": recovery,
        "recent_events": [event.model_dump() for event in events[-10:]],
    }
