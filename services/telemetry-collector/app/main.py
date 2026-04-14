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
    placement_decision: Literal["Admit", "Retain", "Evict", "Hit"]
    bytes_served: int = Field(..., ge=0)
    latency_ms: float = Field(..., ge=0)
    frequency: int = Field(..., ge=1)
    recency_seconds: int = Field(..., ge=0)
    query_type: str | None = None
    object_class: str | None = None
    workload_phase: str | None = None
    semantic_tags: list[str] = Field(default_factory=list)


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


@app.get("/summary")
async def summary() -> dict[str, object]:
    events: list[TelemetryEvent] = app.state.events
    total_requests = len(events)
    cache_hits = sum(1 for event in events if event.metadata_hit)
    source_counts = Counter(event.source for event in events)
    decision_counts = Counter(event.placement_decision for event in events)
    avg_latency_ms = mean(event.latency_ms for event in events) if events else 0.0

    return {
        "total_requests": total_requests,
        "cache_hits": cache_hits,
        "cache_hit_rate": (cache_hits / total_requests) if total_requests else 0.0,
        "average_latency_ms": round(avg_latency_ms, 3),
        "bytes_served_total": sum(event.bytes_served for event in events),
        "sources": dict(source_counts),
        "decisions": dict(decision_counts),
        "recent_events": [event.model_dump() for event in events[-10:]],
    }
