from __future__ import annotations

import os
import shutil
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
CACHE_CAPACITY_BYTES = int(os.getenv("CACHE_CAPACITY_BYTES", str(128 * 1024 * 1024)))
CACHE_EVICTION_POLICY = os.getenv("CACHE_EVICTION_POLICY", "lru").lower()
POLICY_NAME = os.getenv("POLICY_NAME", "baseline-lru")
WORKER_ID = os.getenv("WORKER_ID", "compute-worker")

SUPPORTED_CACHE_POLICIES = {"lru", "lfu", "size-aware-lru"}
SUPPORTED_POLICY_NAMES = {"baseline-lru", "baseline-lfu", "baseline-size-aware", "ml"}


class ReadRequest(BaseModel):
    segment_id: str = Field(..., description="Logical segment or block identifier")
    size_bytes: int = Field(..., gt=0)
    frequency: int = Field(1, ge=1)
    recency_seconds: int = Field(0, ge=0)
    query_type: str | None = None
    object_class: str | None = None
    workload_phase: str | None = None
    semantic_tags: list[str] = Field(default_factory=list)


class PolicyDecisionRequest(BaseModel):
    policy_name: Literal["baseline-lru", "baseline-lfu", "baseline-size-aware", "ml"]
    segment_id: str
    size_bytes: int
    frequency: int
    recency_seconds: int
    cache_capacity_bytes: int
    cache_used_bytes: int


class PolicyDecisionResponse(BaseModel):
    policy_name: Literal["baseline-lru", "baseline-lfu", "baseline-size-aware", "ml"]
    action: Literal["admit", "retain", "evict"]
    score: float
    reason: str


class ReadResponse(BaseModel):
    segment_id: str
    worker_id: str
    source: Literal["cache", "object-store"]
    placement_decision: Literal["admit", "retain", "evict", "hit"]
    metadata_hit: bool
    bytes_served: int


class TelemetryEvent(BaseModel):
    segment_id: str
    worker_id: str
    metadata_hit: bool
    source: Literal["cache", "object-store"]
    placement_decision: Literal["admit", "retain", "evict", "hit"]
    bytes_served: int
    latency_ms: float
    frequency: int
    recency_seconds: int
    query_type: str | None = None
    object_class: str | None = None
    workload_phase: str | None = None
    semantic_tags: list[str] = Field(default_factory=list)


class CacheManager:
    def __init__(self, redis: Redis, cache_root: Path, capacity_bytes: int, policy: str, worker_id: str):
        self.redis = redis
        self.cache_root = cache_root
        self.capacity_bytes = capacity_bytes
        self.policy = policy if policy in SUPPORTED_CACHE_POLICIES else "lru"
        self.worker_id = worker_id

    @property
    def cache_index_key(self) -> str:
        return f"cache:index:{self.worker_id}"

    @property
    def cache_state_key(self) -> str:
        return f"cache:state:{self.worker_id}"

    async def get_cached_metadata(self, segment_id: str) -> dict[str, str]:
        metadata = await self.redis.hgetall(segment_cache_key(self.worker_id, segment_id))
        if metadata.get("location") != "cache":
            return {}

        cache_file = cache_path(segment_id)
        if not cache_file.exists():
            await self.remove_cached_segment(segment_id, preserve_directory_state=True)
            return {}
        return metadata

    async def record_cache_hit(self, segment_id: str, metadata: dict[str, str]) -> None:
        now = time.time()
        await self.redis.hset(
            segment_cache_key(self.worker_id, segment_id),
            mapping={
                "location": "cache",
                "size_bytes": metadata.get("size_bytes", "0"),
                "last_access_ts": now,
                "access_count": int(metadata.get("access_count", "0")) + 1,
                "admission_ts": metadata.get("admission_ts", now),
                "last_decision": "hit",
            },
        )

    async def admit_segment(self, segment_id: str, source_path: Path, size_bytes: int) -> bool:
        if size_bytes > self.capacity_bytes:
            return False

        existing = await self.get_cached_metadata(segment_id)
        if existing:
            cache_file = cache_path(segment_id)
            if not cache_file.exists():
                return False
            now = time.time()
            await self.redis.hset(
                segment_cache_key(self.worker_id, segment_id),
                mapping={
                    "location": "cache",
                    "size_bytes": size_bytes,
                    "last_access_ts": now,
                    "access_count": int(existing.get("access_count", "0")) + 1,
                    "admission_ts": existing.get("admission_ts", now),
                    "last_decision": "admit",
                },
            )
            return True

        current_usage = await self.current_usage_bytes()
        bytes_needed = max(0, current_usage + size_bytes - self.capacity_bytes)
        if bytes_needed > 0:
            await self.evict_until_fit(bytes_needed=bytes_needed, excluding_segment_id=segment_id)

        if size_bytes > self.available_bytes():
            return False

        cache_file = cache_path(segment_id)
        shutil.copyfile(source_path, cache_file)
        now = time.time()
        await self.redis.hset(
            segment_cache_key(self.worker_id, segment_id),
            mapping={
                "location": "cache",
                "size_bytes": size_bytes,
                "last_access_ts": now,
                "access_count": 1,
                "admission_ts": now,
                "last_decision": "admit",
            },
        )
        await self.redis.sadd(self.cache_index_key, segment_id)
        await self.redis.hset(self.cache_state_key, mapping={"used_bytes": await self.current_usage_bytes()})
        return True

    async def retain_in_object_store(self, segment_id: str, size_bytes: int, last_decision: str) -> None:
        now = time.time()
        existing = await self.redis.hgetall(segment_cache_key(self.worker_id, segment_id))
        await self.redis.hset(
            segment_cache_key(self.worker_id, segment_id),
            mapping={
                "location": "object-store",
                "size_bytes": size_bytes,
                "last_access_ts": now,
                "access_count": int(existing.get("access_count", "0")) + 1,
                "admission_ts": existing.get("admission_ts", ""),
                "last_decision": last_decision,
            },
        )

    async def evict_until_fit(self, bytes_needed: int, excluding_segment_id: str | None = None) -> None:
        if bytes_needed <= 0:
            return

        reclaimed = 0
        for candidate in await self.eviction_candidates(excluding_segment_id=excluding_segment_id):
            reclaimed += await self.remove_cached_segment(candidate["segment_id"])
            if reclaimed >= bytes_needed:
                break

    async def eviction_candidates(self, excluding_segment_id: str | None = None) -> list[dict[str, str]]:
        candidates: list[dict[str, str]] = []
        for segment_id in await self.redis.smembers(self.cache_index_key):
            if segment_id == excluding_segment_id:
                continue
            metadata = await self.redis.hgetall(segment_cache_key(self.worker_id, segment_id))
            if metadata.get("location") != "cache":
                continue
            metadata["segment_id"] = segment_id
            candidates.append(metadata)

        def lru_key(item: dict[str, str]) -> tuple[float, int]:
            return (float(item.get("last_access_ts", "0")),)

        def lfu_key(item: dict[str, str]) -> tuple[int, float, int]:
            return (
                int(item.get("access_count", "0")),
                float(item.get("last_access_ts", "0")),
                -int(item.get("size_bytes", "0")),
            )

        def size_aware_lru_key(item: dict[str, str]) -> tuple[float, int]:
            return (float(item.get("last_access_ts", "0")), -int(item.get("size_bytes", "0")))

        sort_key = {
            "lru": lru_key,
            "lfu": lfu_key,
            "size-aware-lru": size_aware_lru_key,
        }[self.policy]
        return sorted(candidates, key=sort_key)

    async def remove_cached_segment(self, segment_id: str, preserve_directory_state: bool = False) -> int:
        metadata = await self.redis.hgetall(segment_cache_key(self.worker_id, segment_id))
        size_bytes = int(metadata.get("size_bytes", "0"))
        cache_file = cache_path(segment_id)
        if cache_file.exists():
            cache_file.unlink()

        await self.redis.hset(
            segment_cache_key(self.worker_id, segment_id),
            mapping={
                "location": "object-store",
                "size_bytes": size_bytes,
                "last_access_ts": metadata.get("last_access_ts", time.time()),
                "access_count": metadata.get("access_count", 0),
                "admission_ts": metadata.get("admission_ts", ""),
                "last_decision": "evict",
            },
        )

        await self.redis.srem(self.cache_index_key, segment_id)
        await self.redis.hset(self.cache_state_key, mapping={"used_bytes": await self.current_usage_bytes()})
        return size_bytes

    async def current_usage_bytes(self) -> int:
        total = 0
        for segment_id in await self.redis.smembers(self.cache_index_key):
            metadata = await self.redis.hgetall(segment_cache_key(self.worker_id, segment_id))
            if metadata.get("location") == "cache":
                total += int(metadata.get("size_bytes", "0"))
        return total

    def available_bytes(self) -> int:
        total = sum(file.stat().st_size for file in self.cache_root.glob("*.bin") if file.is_file())
        return max(0, self.capacity_bytes - total)


@asynccontextmanager
async def lifespan(app: FastAPI):
    OBJECT_STORE_ROOT.mkdir(parents=True, exist_ok=True)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    app.state.redis = Redis.from_url(REDIS_URL, decode_responses=True)
    app.state.http_client = httpx.AsyncClient(timeout=5.0)
    if CACHE_EVICTION_POLICY not in SUPPORTED_CACHE_POLICIES:
        raise ValueError(
            f"Unsupported CACHE_EVICTION_POLICY={CACHE_EVICTION_POLICY!r}. "
            f"Expected one of {sorted(SUPPORTED_CACHE_POLICIES)}."
        )
    if POLICY_NAME not in SUPPORTED_POLICY_NAMES:
        raise ValueError(
            f"Unsupported POLICY_NAME={POLICY_NAME!r}. "
            f"Expected one of {sorted(SUPPORTED_POLICY_NAMES)}."
        )
    for cached_file in CACHE_ROOT.glob("*.bin"):
        cached_file.unlink()
    worker_keys: list[str] = []
    async for key in app.state.redis.scan_iter(match=f"segment:{WORKER_ID}:*"):
        worker_keys.append(key)
    if worker_keys:
        await app.state.redis.delete(*worker_keys)
    await app.state.redis.delete(f"cache:index:{WORKER_ID}", f"cache:state:{WORKER_ID}")
    app.state.cache_manager = CacheManager(
        redis=app.state.redis,
        cache_root=CACHE_ROOT,
        capacity_bytes=CACHE_CAPACITY_BYTES,
        policy=CACHE_EVICTION_POLICY,
        worker_id=WORKER_ID,
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await app.state.redis.aclose()


app = FastAPI(title="compute-worker", lifespan=lifespan)


def segment_cache_key(worker_id: str, segment_id: str) -> str:
    return f"segment:{worker_id}:{segment_id}"


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
    return {"status": "ok", "worker_id": WORKER_ID}


@app.get("/cache/state")
async def cache_state() -> dict[str, int | str]:
    cache_manager: CacheManager = app.state.cache_manager
    return {
        "worker_id": WORKER_ID,
        "policy": cache_manager.policy,
        "admission_policy": POLICY_NAME,
        "capacity_bytes": cache_manager.capacity_bytes,
        "used_bytes": await cache_manager.current_usage_bytes(),
        "available_bytes": cache_manager.available_bytes(),
    }


@app.post("/read", response_model=ReadResponse)
async def read_segment(request: ReadRequest) -> ReadResponse:
    cache_manager: CacheManager = app.state.cache_manager
    http_client: httpx.AsyncClient = app.state.http_client
    started_at = time.perf_counter()

    metadata = await cache_manager.get_cached_metadata(request.segment_id)
    if metadata:
        await cache_manager.record_cache_hit(request.segment_id, metadata)
        response = ReadResponse(
            segment_id=request.segment_id,
            worker_id=WORKER_ID,
            source="cache",
            placement_decision="hit",
            metadata_hit=True,
            bytes_served=int(metadata.get("size_bytes", request.size_bytes)),
        )
        await emit_telemetry(
            TelemetryEvent(
                segment_id=request.segment_id,
                worker_id=WORKER_ID,
                metadata_hit=True,
                source=response.source,
                placement_decision=response.placement_decision,
                bytes_served=response.bytes_served,
                latency_ms=(time.perf_counter() - started_at) * 1000,
                frequency=request.frequency,
                recency_seconds=request.recency_seconds,
                query_type=request.query_type,
                object_class=request.object_class,
                workload_phase=request.workload_phase,
                semantic_tags=request.semantic_tags,
            )
        )
        return response

    policy_payload = PolicyDecisionRequest(
        policy_name=POLICY_NAME,
        segment_id=request.segment_id,
        size_bytes=request.size_bytes,
        frequency=request.frequency,
        recency_seconds=request.recency_seconds,
        cache_capacity_bytes=cache_manager.capacity_bytes,
        cache_used_bytes=await cache_manager.current_usage_bytes(),
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

    if decision.action == "admit":
        admitted = await cache_manager.admit_segment(
            segment_id=request.segment_id,
            source_path=object_path,
            size_bytes=bytes_served,
        )
        if not admitted:
            await cache_manager.retain_in_object_store(
                segment_id=request.segment_id,
                size_bytes=bytes_served,
                last_decision="retain",
            )
            decision = PolicyDecisionResponse(
                policy_name=POLICY_NAME,
                action="retain",
                score=decision.score,
                reason="Segment did not fit within cache capacity after eviction.",
            )
    else:
        await cache_manager.retain_in_object_store(
            segment_id=request.segment_id,
            size_bytes=bytes_served,
            last_decision=decision.action,
        )

    response = ReadResponse(
        segment_id=request.segment_id,
        worker_id=WORKER_ID,
        source="object-store",
        placement_decision=decision.action,
        metadata_hit=False,
        bytes_served=bytes_served,
    )
    await emit_telemetry(
        TelemetryEvent(
            segment_id=request.segment_id,
            worker_id=WORKER_ID,
            metadata_hit=False,
            source=response.source,
            placement_decision=response.placement_decision,
            bytes_served=response.bytes_served,
            latency_ms=(time.perf_counter() - started_at) * 1000,
            frequency=request.frequency,
            recency_seconds=request.recency_seconds,
            query_type=request.query_type,
            object_class=request.object_class,
            workload_phase=request.workload_phase,
            semantic_tags=request.semantic_tags,
        )
    )
    return response
