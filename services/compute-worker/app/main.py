from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from redis.asyncio import Redis


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
POLICY_ENGINE_URL = os.getenv("POLICY_ENGINE_URL", "http://localhost:8001")
TELEMETRY_COLLECTOR_URL = os.getenv("TELEMETRY_COLLECTOR_URL", "http://localhost:8002")
OBJECT_STORE_ROOT = Path(os.getenv("OBJECT_STORE_ROOT", "./data/object-store"))
CACHE_ROOT = Path(os.getenv("CACHE_ROOT", "./data/cache"))


class ReadRequest(BaseModel):
    segment_id: str = Field(..., description="Logical segment or block identifier")
    size_bytes: int = Field(..., gt=0)
    frequency: int = Field(1, ge=1)
    recency_seconds: int = Field(0, ge=0)


class PolicyDecisionRequest(BaseModel):
    segment_id: str
    size_bytes: int
    frequency: int
    recency_seconds: int


class PolicyDecisionResponse(BaseModel):
    decision: Literal["Admit", "Retain", "Evict"]
    reason: str


class ReadResponse(BaseModel):
    segment_id: str
    source: Literal["cache", "object-store"]
    placement_decision: Literal["Admit", "Retain", "Evict", "Hit"]
    metadata_hit: bool
    bytes_served: int


class TelemetryEvent(BaseModel):
    segment_id: str
    metadata_hit: bool
    source: Literal["cache", "object-store"]
    placement_decision: Literal["Admit", "Retain", "Evict", "Hit"]
    bytes_served: int
    latency_ms: float
    frequency: int
    recency_seconds: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    OBJECT_STORE_ROOT.mkdir(parents=True, exist_ok=True)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    app.state.redis = Redis.from_url(REDIS_URL, decode_responses=True)
    app.state.http_client = httpx.AsyncClient(timeout=5.0)
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await app.state.redis.aclose()


app = FastAPI(title="compute-worker", lifespan=lifespan)


def segment_cache_key(segment_id: str) -> str:
    return f"segment:{segment_id}"


def object_store_path(segment_id: str) -> Path:
    return OBJECT_STORE_ROOT / f"{segment_id}.bin"


def cache_path(segment_id: str) -> Path:
    return CACHE_ROOT / f"{segment_id}.bin"


def ensure_mock_segment(segment_id: str, size_bytes: int) -> Path:
    path = object_store_path(segment_id)
    if not path.exists():
        payload = (f"mock-segment:{segment_id}|".encode()) * max(1, size_bytes // max(1, len(segment_id) + 14))
        path.write_bytes(payload[:size_bytes] or b"x")
    return path


async def emit_telemetry(event: TelemetryEvent) -> None:
    http_client: httpx.AsyncClient = app.state.http_client
    try:
        await http_client.post(
            f"{TELEMETRY_COLLECTOR_URL}/events",
            json=event.model_dump(),
        )
    except httpx.HTTPError:
        # Telemetry is best-effort in the local simulation.
        return


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/read", response_model=ReadResponse)
async def read_segment(request: ReadRequest) -> ReadResponse:
    redis: Redis = app.state.redis
    http_client: httpx.AsyncClient = app.state.http_client
    started_at = time.perf_counter()
    redis_key = segment_cache_key(request.segment_id)

    metadata = await redis.hgetall(redis_key)
    if metadata.get("location") == "cache" and cache_path(request.segment_id).exists():
        await redis.hincrby(redis_key, "hits", 1)
        response = ReadResponse(
            segment_id=request.segment_id,
            source="cache",
            placement_decision="Hit",
            metadata_hit=True,
            bytes_served=int(metadata.get("size_bytes", request.size_bytes)),
        )
        await emit_telemetry(
            TelemetryEvent(
                segment_id=request.segment_id,
                metadata_hit=True,
                source=response.source,
                placement_decision=response.placement_decision,
                bytes_served=response.bytes_served,
                latency_ms=(time.perf_counter() - started_at) * 1000,
                frequency=request.frequency,
                recency_seconds=request.recency_seconds,
            )
        )
        return response

    policy_payload = PolicyDecisionRequest(
        segment_id=request.segment_id,
        size_bytes=request.size_bytes,
        frequency=request.frequency,
        recency_seconds=request.recency_seconds,
    )

    try:
        policy_response = await http_client.post(
            f"{POLICY_ENGINE_URL}/decide",
            json=policy_payload.model_dump(),
        )
        policy_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Policy engine unavailable: {exc}") from exc

    decision = PolicyDecisionResponse.model_validate(policy_response.json())
    object_path = ensure_mock_segment(request.segment_id, request.size_bytes)
    bytes_served = object_path.stat().st_size

    if decision.decision == "Admit":
        cache_file = cache_path(request.segment_id)
        cache_file.write_bytes(object_path.read_bytes())
        await redis.hset(
            redis_key,
            mapping={
                "location": "cache",
                "size_bytes": bytes_served,
                "hits": 0,
                "last_decision": decision.decision,
            },
        )
    else:
        await redis.hset(
            redis_key,
            mapping={
                "location": "object-store",
                "size_bytes": bytes_served,
                "hits": 0,
                "last_decision": decision.decision,
            },
        )

    response = ReadResponse(
        segment_id=request.segment_id,
        source="object-store",
        placement_decision=decision.decision,
        metadata_hit=False,
        bytes_served=bytes_served,
    )
    await emit_telemetry(
        TelemetryEvent(
            segment_id=request.segment_id,
            metadata_hit=False,
            source=response.source,
            placement_decision=response.placement_decision,
            bytes_served=response.bytes_served,
            latency_ms=(time.perf_counter() - started_at) * 1000,
            frequency=request.frequency,
            recency_seconds=request.recency_seconds,
        )
    )
    return response
