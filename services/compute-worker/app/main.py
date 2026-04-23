from __future__ import annotations

import os
import shutil
import time
import json
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from google.cloud import storage
from pydantic import BaseModel, Field
from redis.asyncio import Redis


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
POLICY_ENGINE_URL = os.getenv("POLICY_ENGINE_URL", "http://localhost:8001")
TELEMETRY_COLLECTOR_URL = os.getenv("TELEMETRY_COLLECTOR_URL", "http://localhost:8002")
OBJECT_STORE_ROOT = Path(os.getenv("OBJECT_STORE_ROOT", "./data/object-store"))
OBJECT_STORE_BUCKET = os.getenv("OBJECT_STORE_BUCKET", "").strip()
OBJECT_STORE_PREFIX = os.getenv("OBJECT_STORE_PREFIX", "").strip().strip("/")
CACHE_ROOT = Path(os.getenv("CACHE_ROOT", "./data/cache"))
OBJECT_STORE_STAGING_ROOT = Path(os.getenv("OBJECT_STORE_STAGING_ROOT", "/tmp/object-store-staging"))
CACHE_CAPACITY_BYTES = int(os.getenv("CACHE_CAPACITY_BYTES", str(128 * 1024 * 1024)))
CACHE_EVICTION_POLICY = os.getenv("CACHE_EVICTION_POLICY", "lru").lower()
POLICY_NAME = os.getenv("POLICY_NAME", "baseline-lru")
WORKER_ID = os.getenv("WORKER_ID", "compute-worker")
OBJECT_STORE_BASE_LATENCY_MS = float(os.getenv("OBJECT_STORE_BASE_LATENCY_MS", "25.0"))
OBJECT_STORE_MB_PER_SEC = float(os.getenv("OBJECT_STORE_MB_PER_SEC", "120.0"))
TRANSFER_COST_PER_MB = float(os.getenv("TRANSFER_COST_PER_MB", "0.00002"))
TRAINING_LOG_DIR = Path(os.getenv("TRAINING_LOG_DIR", "/data/training"))

SUPPORTED_CACHE_POLICIES = {"lru", "lfu", "size-aware-lru"}
SUPPORTED_POLICY_NAMES = {"baseline-lru", "baseline-lfu", "baseline-size-aware", "baseline-admit-all", "ml"}


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
    policy_name: Literal["baseline-lru", "baseline-lfu", "baseline-size-aware", "baseline-admit-all", "ml"]
    segment_id: str
    size_bytes: int
    frequency: int
    recency_seconds: int
    inter_arrival_gap_seconds: float
    rolling_hit_count: int
    estimated_object_store_latency_ms: float
    transfer_cost_proxy: float
    query_type: str | None = None
    object_class: str | None = None
    workload_phase: str | None = None
    cache_capacity_bytes: int
    cache_used_bytes: int


class PolicyDecisionResponse(BaseModel):
    policy_name: Literal["baseline-lru", "baseline-lfu", "baseline-size-aware", "baseline-admit-all", "ml"]
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


class TrainingLogRecord(BaseModel):
    event_time_ns: int
    worker_id: str
    segment_id: str
    policy_name: Literal["baseline-lru", "baseline-lfu", "baseline-size-aware", "baseline-admit-all", "ml"]
    query_type: str | None = None
    object_class: str | None = None
    workload_phase: str | None = None
    semantic_tags: list[str] = Field(default_factory=list)
    metadata_hit: bool
    source: Literal["cache", "object-store"]
    placement_decision: Literal["admit", "retain", "evict", "hit"]
    bytes_served: int
    latency_ms: float
    recency_seconds: int
    frequency: int
    inter_arrival_gap_seconds: float
    rolling_hit_count: int
    estimated_object_store_latency_ms: float
    transfer_cost_proxy: float
    cache_capacity_bytes: int
    cache_used_bytes: int
    policy_inference_ms: float
    redis_lookup_ms: float
    object_fetch_ms: float
    cache_insert_ms: float
    cache_eviction_ms: float
    duplicate_fetch_detected: bool
    duplicate_admit_detected: bool
    duplicate_fetch_count: int
    duplicate_admit_count: int
    bytes_fetched_from_object_store: int
    ssd_occupancy_bytes: int
    eviction_count_total: int
    cache_turnover_bytes_total: int


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

    async def get_segment_metadata(self, segment_id: str) -> dict[str, str]:
        return await self.redis.hgetall(segment_cache_key(self.worker_id, segment_id))

    async def cache_stats(self) -> dict[str, int]:
        stats = await self.redis.hgetall(self.cache_state_key)
        return {
            "used_bytes": int(stats.get("used_bytes", "0")),
            "eviction_count": int(stats.get("eviction_count", "0")),
            "cache_turnover_bytes": int(stats.get("cache_turnover_bytes", "0")),
            "duplicate_fetch_count": int(stats.get("duplicate_fetch_count", "0")),
            "duplicate_admit_count": int(stats.get("duplicate_admit_count", "0")),
        }

    async def increment_duplicate_fetch_count(self) -> int:
        return int(await self.redis.hincrby(self.cache_state_key, "duplicate_fetch_count", 1))

    async def increment_duplicate_admit_count(self) -> int:
        return int(await self.redis.hincrby(self.cache_state_key, "duplicate_admit_count", 1))

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

    async def admit_segment(self, segment_id: str, source_path: Path, size_bytes: int) -> tuple[bool, int, bool]:
        if size_bytes > self.capacity_bytes:
            return False, 0, False

        existing = await self.get_cached_metadata(segment_id)
        if existing:
            cache_file = cache_path(segment_id)
            if not cache_file.exists():
                return False, 0, False
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
            duplicate_admit_count = await self.increment_duplicate_admit_count()
            return True, 0, duplicate_admit_count > 0

        current_usage = await self.current_usage_bytes()
        bytes_needed = max(0, current_usage + size_bytes - self.capacity_bytes)
        evicted_bytes = 0
        if bytes_needed > 0:
            evicted_bytes = await self.evict_until_fit(bytes_needed=bytes_needed, excluding_segment_id=segment_id)

        if size_bytes > self.available_bytes():
            return False, evicted_bytes, False

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
        used_bytes = await self.current_usage_bytes()
        await self.redis.hset(
            self.cache_state_key,
            mapping={
                "used_bytes": used_bytes,
                "cache_turnover_bytes": await self.redis.hincrby(
                    self.cache_state_key, "cache_turnover_bytes", size_bytes + evicted_bytes
                ),
            },
        )
        return True, evicted_bytes, False

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

    async def evict_until_fit(self, bytes_needed: int, excluding_segment_id: str | None = None) -> int:
        if bytes_needed <= 0:
            return 0

        reclaimed = 0
        for candidate in await self.eviction_candidates(excluding_segment_id=excluding_segment_id):
            reclaimed += await self.remove_cached_segment(candidate["segment_id"])
            if reclaimed >= bytes_needed:
                break
        return reclaimed

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
        used_bytes = await self.current_usage_bytes()
        eviction_count = await self.redis.hincrby(self.cache_state_key, "eviction_count", 1)
        cache_turnover_bytes = await self.redis.hincrby(self.cache_state_key, "cache_turnover_bytes", size_bytes)
        await self.redis.hset(
            self.cache_state_key,
            mapping={
                "used_bytes": used_bytes,
                "eviction_count": eviction_count,
                "cache_turnover_bytes": cache_turnover_bytes,
            },
        )
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
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    TRAINING_LOG_DIR.mkdir(parents=True, exist_ok=True)
    OBJECT_STORE_STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    if not OBJECT_STORE_BUCKET:
        OBJECT_STORE_ROOT.mkdir(parents=True, exist_ok=True)
    app.state.redis = Redis.from_url(REDIS_URL, decode_responses=True)
    app.state.http_client = httpx.AsyncClient(timeout=5.0)
    app.state.storage_client = storage.Client() if OBJECT_STORE_BUCKET else None
    app.state.training_log_path = TRAINING_LOG_DIR / f"{WORKER_ID}.jsonl"
    app.state.training_log_lock = asyncio.Lock()
    app.state.inflight_requests: dict[str, asyncio.Future[ReadResponse]] = {}
    app.state.inflight_lock = asyncio.Lock()
    app.state.last_telemetry_emit_ms = 0.0
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
    await app.state.redis.hset(
        f"cache:state:{WORKER_ID}",
        mapping={
            "used_bytes": 0,
            "eviction_count": 0,
            "cache_turnover_bytes": 0,
            "duplicate_fetch_count": 0,
            "duplicate_admit_count": 0,
        },
    )
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


def object_store_object_name(segment_id: str) -> str:
    filename = f"{segment_id}.bin"
    return f"{OBJECT_STORE_PREFIX}/{filename}" if OBJECT_STORE_PREFIX else filename


def object_store_staging_path(segment_id: str) -> Path:
    return OBJECT_STORE_STAGING_ROOT / f"{segment_id}.bin"


def cache_path(segment_id: str) -> Path:
    return CACHE_ROOT / f"{segment_id}.bin"


def build_mock_payload(segment_id: str, size_bytes: int) -> bytes:
    payload = (f"mock-segment:{segment_id}|".encode()) * max(1, size_bytes // max(1, len(segment_id) + 14))
    return payload[:size_bytes] or b"x"


def ensure_mock_segment(segment_id: str, size_bytes: int) -> Path:
    path = object_store_path(segment_id)
    if not path.exists():
        path.write_bytes(build_mock_payload(segment_id, size_bytes))
    return path


def _materialize_segment_from_gcs(segment_id: str, size_bytes: int) -> tuple[Path, int]:
    storage_client: storage.Client = app.state.storage_client
    if storage_client is None:
        raise RuntimeError("GCS client requested but OBJECT_STORE_BUCKET is not configured")

    bucket = storage_client.bucket(OBJECT_STORE_BUCKET)
    blob = bucket.blob(object_store_object_name(segment_id))
    if not blob.exists():
        blob.upload_from_string(build_mock_payload(segment_id, size_bytes), content_type="application/octet-stream")

    destination = object_store_staging_path(segment_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(destination)
    return destination, int(destination.stat().st_size)


async def materialize_object_store_segment(segment_id: str, size_bytes: int) -> tuple[Path, int]:
    if OBJECT_STORE_BUCKET:
        return await asyncio.to_thread(_materialize_segment_from_gcs, segment_id, size_bytes)
    path = ensure_mock_segment(segment_id, size_bytes)
    return path, int(path.stat().st_size)


def estimate_object_store_latency_ms(size_bytes: int) -> float:
    size_mb = size_bytes / (1024 * 1024)
    transfer_ms = (size_mb / OBJECT_STORE_MB_PER_SEC) * 1000
    return OBJECT_STORE_BASE_LATENCY_MS + transfer_ms


def transfer_cost_proxy(size_bytes: int) -> float:
    size_mb = size_bytes / (1024 * 1024)
    return round(size_mb * TRANSFER_COST_PER_MB, 8)


async def emit_telemetry(event: TelemetryEvent) -> float:
    http_client: httpx.AsyncClient = app.state.http_client
    started_at = time.perf_counter()
    try:
        await http_client.post(
            f"{TELEMETRY_COLLECTOR_URL}/events",
            json=event.model_dump(),
        )
    except httpx.HTTPError:
        # Telemetry is best-effort in the local simulation.
        return 0.0
    return (time.perf_counter() - started_at) * 1000


async def append_training_log(record: TrainingLogRecord) -> None:
    log_path: Path = app.state.training_log_path
    lock: asyncio.Lock = app.state.training_log_lock
    async with lock:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump()) + "\n")


async def record_request_observation(
    *,
    request: ReadRequest,
    response: ReadResponse,
    started_at: float,
    cache_manager: CacheManager,
    cache_used_bytes: int,
    inter_arrival_gap_seconds: float,
    rolling_hit_count: int,
    estimated_latency_ms: float,
    transfer_cost: float,
    policy_inference_ms: float,
    redis_lookup_ms: float,
    object_fetch_ms: float,
    cache_insert_ms: float,
    cache_eviction_ms: float,
    duplicate_fetch_detected: bool,
    duplicate_admit_detected: bool,
    duplicate_fetch_count: int,
    duplicate_admit_count: int,
    bytes_fetched_from_object_store: int,
    last_telemetry_emit_ms: float,
) -> None:
    stats = await cache_manager.cache_stats()
    duplicate_fetch_count = max(duplicate_fetch_count, stats["duplicate_fetch_count"])
    duplicate_admit_count = max(duplicate_admit_count, stats["duplicate_admit_count"])

    await append_training_log(
        TrainingLogRecord(
            event_time_ns=time.time_ns(),
            worker_id=WORKER_ID,
            segment_id=request.segment_id,
            policy_name=POLICY_NAME,
            query_type=request.query_type,
            object_class=request.object_class,
            workload_phase=request.workload_phase,
            semantic_tags=request.semantic_tags,
            metadata_hit=response.metadata_hit,
            source=response.source,
            placement_decision=response.placement_decision,
            bytes_served=response.bytes_served,
            latency_ms=(time.perf_counter() - started_at) * 1000,
            recency_seconds=request.recency_seconds,
            frequency=request.frequency,
            inter_arrival_gap_seconds=inter_arrival_gap_seconds,
            rolling_hit_count=rolling_hit_count,
            estimated_object_store_latency_ms=estimated_latency_ms,
            transfer_cost_proxy=transfer_cost,
            cache_capacity_bytes=cache_manager.capacity_bytes,
            cache_used_bytes=cache_used_bytes,
            policy_inference_ms=policy_inference_ms,
            redis_lookup_ms=redis_lookup_ms,
            object_fetch_ms=object_fetch_ms,
            cache_insert_ms=cache_insert_ms,
            cache_eviction_ms=cache_eviction_ms,
            duplicate_fetch_detected=duplicate_fetch_detected,
            duplicate_admit_detected=duplicate_admit_detected,
            duplicate_fetch_count=duplicate_fetch_count,
            duplicate_admit_count=duplicate_admit_count,
            bytes_fetched_from_object_store=bytes_fetched_from_object_store,
            ssd_occupancy_bytes=stats["used_bytes"],
            eviction_count_total=stats["eviction_count"],
            cache_turnover_bytes_total=stats["cache_turnover_bytes"],
        )
    )

    telemetry_event = TelemetryEvent(
        segment_id=request.segment_id,
        worker_id=WORKER_ID,
        metadata_hit=response.metadata_hit,
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
        policy_inference_ms=policy_inference_ms,
        redis_lookup_ms=redis_lookup_ms,
        object_fetch_ms=object_fetch_ms,
        cache_insert_ms=cache_insert_ms,
        cache_eviction_ms=cache_eviction_ms,
        telemetry_emit_ms=last_telemetry_emit_ms,
        duplicate_fetch_detected=duplicate_fetch_detected,
        duplicate_admit_detected=duplicate_admit_detected,
        duplicate_fetch_count=duplicate_fetch_count,
        duplicate_admit_count=duplicate_admit_count,
        bytes_fetched_from_object_store=bytes_fetched_from_object_store,
        ssd_occupancy_bytes=stats["used_bytes"],
        eviction_count_total=stats["eviction_count"],
        cache_turnover_bytes_total=stats["cache_turnover_bytes"],
    )
    app.state.last_telemetry_emit_ms = await emit_telemetry(telemetry_event)


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
    inflight_lock: asyncio.Lock = app.state.inflight_lock
    inflight_requests: dict[str, asyncio.Future[ReadResponse]] = app.state.inflight_requests
    redis_lookup_started = time.perf_counter()
    previous_metadata = await cache_manager.get_segment_metadata(request.segment_id)
    redis_lookup_ms = (time.perf_counter() - redis_lookup_started) * 1000
    inter_arrival_gap_seconds = max(
        0.0,
        time.time() - float(previous_metadata.get("last_access_ts", time.time() - request.recency_seconds)),
    )
    rolling_hit_count = int(previous_metadata.get("access_count", "0"))
    estimated_latency_ms = estimate_object_store_latency_ms(request.size_bytes)
    transfer_cost = transfer_cost_proxy(request.size_bytes)
    cache_used_bytes = await cache_manager.current_usage_bytes()
    duplicate_fetch_detected = False
    duplicate_admit_detected = False
    duplicate_fetch_count = 0
    duplicate_admit_count = 0
    policy_inference_ms = 0.0
    object_fetch_ms = 0.0
    cache_insert_ms = 0.0
    cache_eviction_ms = 0.0
    bytes_fetched_from_object_store = 0
    last_telemetry_emit_ms = float(app.state.last_telemetry_emit_ms)

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
        await record_request_observation(
            request=request,
            response=response,
            started_at=started_at,
            cache_manager=cache_manager,
            cache_used_bytes=cache_used_bytes,
            inter_arrival_gap_seconds=inter_arrival_gap_seconds,
            rolling_hit_count=rolling_hit_count,
            estimated_latency_ms=estimated_latency_ms,
            transfer_cost=transfer_cost,
            policy_inference_ms=policy_inference_ms,
            redis_lookup_ms=redis_lookup_ms,
            object_fetch_ms=object_fetch_ms,
            cache_insert_ms=cache_insert_ms,
            cache_eviction_ms=cache_eviction_ms,
            duplicate_fetch_detected=duplicate_fetch_detected,
            duplicate_admit_detected=duplicate_admit_detected,
            duplicate_fetch_count=duplicate_fetch_count,
            duplicate_admit_count=duplicate_admit_count,
            bytes_fetched_from_object_store=bytes_fetched_from_object_store,
            last_telemetry_emit_ms=last_telemetry_emit_ms,
        )
        return response

    leader = False
    future: asyncio.Future[ReadResponse] | None = None
    async with inflight_lock:
        future = inflight_requests.get(request.segment_id)
        if future is not None:
            duplicate_fetch_detected = True
            duplicate_fetch_count = await cache_manager.increment_duplicate_fetch_count()
        else:
            leader = True
            future = asyncio.get_running_loop().create_future()
            inflight_requests[request.segment_id] = future

    if not leader and future is not None:
        leader_response = await future
        wait_lookup_started = time.perf_counter()
        post_wait_metadata = await cache_manager.get_cached_metadata(request.segment_id)
        redis_lookup_ms += (time.perf_counter() - wait_lookup_started) * 1000
        if post_wait_metadata:
            response = ReadResponse(
                segment_id=request.segment_id,
                worker_id=WORKER_ID,
                source="cache",
                placement_decision="hit",
                metadata_hit=True,
                bytes_served=int(post_wait_metadata.get("size_bytes", request.size_bytes)),
            )
        else:
            response = ReadResponse(
                segment_id=request.segment_id,
                worker_id=WORKER_ID,
                source=leader_response.source,
                placement_decision=leader_response.placement_decision,
                metadata_hit=False,
                bytes_served=leader_response.bytes_served,
            )
        await record_request_observation(
            request=request,
            response=response,
            started_at=started_at,
            cache_manager=cache_manager,
            cache_used_bytes=cache_used_bytes,
            inter_arrival_gap_seconds=inter_arrival_gap_seconds,
            rolling_hit_count=rolling_hit_count,
            estimated_latency_ms=estimated_latency_ms,
            transfer_cost=transfer_cost,
            policy_inference_ms=0.0,
            redis_lookup_ms=redis_lookup_ms,
            object_fetch_ms=0.0,
            cache_insert_ms=0.0,
            cache_eviction_ms=0.0,
            duplicate_fetch_detected=duplicate_fetch_detected,
            duplicate_admit_detected=False,
            duplicate_fetch_count=duplicate_fetch_count,
            duplicate_admit_count=0,
            bytes_fetched_from_object_store=0,
            last_telemetry_emit_ms=last_telemetry_emit_ms,
        )
        return response

    policy_payload = PolicyDecisionRequest(
        policy_name=POLICY_NAME,
        segment_id=request.segment_id,
        size_bytes=request.size_bytes,
        frequency=request.frequency,
        recency_seconds=request.recency_seconds,
        inter_arrival_gap_seconds=inter_arrival_gap_seconds,
        rolling_hit_count=rolling_hit_count,
        estimated_object_store_latency_ms=estimated_latency_ms,
        transfer_cost_proxy=transfer_cost,
        query_type=request.query_type,
        object_class=request.object_class,
        workload_phase=request.workload_phase,
        cache_capacity_bytes=cache_manager.capacity_bytes,
        cache_used_bytes=cache_used_bytes,
    )

    try:
        policy_started = time.perf_counter()
        policy_response = await http_client.post(
            f"{POLICY_ENGINE_URL}/decide",
            json=policy_payload.model_dump(),
        )
        policy_response.raise_for_status()
        policy_inference_ms = (time.perf_counter() - policy_started) * 1000
    except httpx.HTTPError as exc:
        async with inflight_lock:
            inflight_requests.pop(request.segment_id, None)
        if future is not None and not future.done():
            future.set_exception(exc)
        raise HTTPException(status_code=503, detail=f"Policy engine unavailable: {exc}") from exc

    decision = PolicyDecisionResponse.model_validate(policy_response.json())
    object_fetch_started = time.perf_counter()
    object_path, bytes_served = await materialize_object_store_segment(request.segment_id, request.size_bytes)
    bytes_fetched_from_object_store = bytes_served
    object_fetch_ms = (time.perf_counter() - object_fetch_started) * 1000

    if decision.action == "admit":
        cache_insert_started = time.perf_counter()
        admitted, evicted_bytes, duplicate_admit_detected = await cache_manager.admit_segment(
            segment_id=request.segment_id,
            source_path=object_path,
            size_bytes=bytes_served,
        )
        cache_insert_ms = (time.perf_counter() - cache_insert_started) * 1000
        cache_eviction_ms = cache_insert_ms if evicted_bytes > 0 else 0.0
        if duplicate_admit_detected:
            stats_after_duplicate = await cache_manager.cache_stats()
            duplicate_admit_count = stats_after_duplicate["duplicate_admit_count"]
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
    if future is not None and not future.done():
        future.set_result(response)
    async with inflight_lock:
        inflight_requests.pop(request.segment_id, None)
    await record_request_observation(
        request=request,
        response=response,
        started_at=started_at,
        cache_manager=cache_manager,
        cache_used_bytes=cache_used_bytes,
        inter_arrival_gap_seconds=inter_arrival_gap_seconds,
        rolling_hit_count=rolling_hit_count,
        estimated_latency_ms=estimated_latency_ms,
        transfer_cost=transfer_cost,
        policy_inference_ms=policy_inference_ms,
        redis_lookup_ms=redis_lookup_ms,
        object_fetch_ms=object_fetch_ms,
        cache_insert_ms=cache_insert_ms,
        cache_eviction_ms=cache_eviction_ms,
        duplicate_fetch_detected=duplicate_fetch_detected,
        duplicate_admit_detected=duplicate_admit_detected,
        duplicate_fetch_count=duplicate_fetch_count,
        duplicate_admit_count=duplicate_admit_count,
        bytes_fetched_from_object_store=bytes_fetched_from_object_store,
        last_telemetry_emit_ms=last_telemetry_emit_ms,
    )
    return response
