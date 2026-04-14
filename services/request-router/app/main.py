from __future__ import annotations

import hashlib
import os
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


ROUTER_WORKERS = os.getenv(
    "ROUTER_WORKERS",
    "worker-1=http://compute-worker-1:8000,worker-2=http://compute-worker-2:8000,worker-3=http://compute-worker-3:8000",
)


class WorkerTarget(BaseModel):
    worker_id: str
    base_url: str


class ReadRequest(BaseModel):
    segment_id: str = Field(..., min_length=1)
    size_bytes: int = Field(..., gt=0)
    frequency: int = Field(1, ge=1)
    recency_seconds: int = Field(0, ge=0)
    query_type: str | None = None
    object_class: str | None = None
    workload_phase: str | None = None
    semantic_tags: list[str] = Field(default_factory=list)


class ScaleRequest(BaseModel):
    active_worker_count: int = Field(..., ge=1)
    reason: str | None = None


def parse_workers() -> list[WorkerTarget]:
    workers: list[WorkerTarget] = []
    for token in ROUTER_WORKERS.split(","):
        token = token.strip()
        if not token:
            continue
        worker_id, base_url = token.split("=", maxsplit=1)
        workers.append(WorkerTarget(worker_id=worker_id.strip(), base_url=base_url.strip()))
    if not workers:
        raise ValueError("ROUTER_WORKERS must define at least one worker")
    return workers


def active_worker_count(tags: list[str], max_workers: int) -> int | None:
    for tag in tags:
        if tag.startswith("active-workers:"):
            _, raw_count = tag.split(":", maxsplit=1)
            try:
                requested = int(raw_count)
            except ValueError:
                continue
            return max(1, min(max_workers, requested))
    return None


def choose_worker(segment_id: str, workers: list[WorkerTarget], replicas: int = 64) -> WorkerTarget:
    best_score = None
    best_worker = workers[0]
    for worker in workers:
        for replica in range(replicas):
            digest = hashlib.sha256(f"{worker.worker_id}:{replica}:{segment_id}".encode()).hexdigest()
            score = int(digest, 16)
            if best_score is None or score > best_score:
                best_score = score
                best_worker = worker
    return best_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.workers = parse_workers()
    app.state.active_worker_count = len(app.state.workers)
    app.state.scale_events: list[dict[str, object]] = []
    app.state.http_client = httpx.AsyncClient(timeout=10.0)
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(title="request-router", lifespan=lifespan)


@app.get("/health")
async def healthcheck() -> dict[str, object]:
    workers: list[WorkerTarget] = app.state.workers
    return {
        "status": "ok",
        "workers": [worker.worker_id for worker in workers],
        "active_worker_count": app.state.active_worker_count,
    }


@app.get("/ring")
async def ring() -> dict[str, object]:
    workers: list[WorkerTarget] = app.state.workers
    return {
        "workers": [worker.model_dump() for worker in workers],
        "replicas_per_worker": 64,
        "active_worker_count": app.state.active_worker_count,
        "scale_events": app.state.scale_events,
    }


@app.post("/admin/scale")
async def scale_workers(request: ScaleRequest) -> dict[str, object]:
    workers: list[WorkerTarget] = app.state.workers
    app.state.active_worker_count = max(1, min(len(workers), request.active_worker_count))
    app.state.scale_events.append(
        {
            "active_worker_count": app.state.active_worker_count,
            "reason": request.reason or "manual",
        }
    )
    return {
        "status": "ok",
        "active_worker_count": app.state.active_worker_count,
        "active_workers": [worker.worker_id for worker in workers[: app.state.active_worker_count]],
    }


@app.post("/read")
async def route_read(request: ReadRequest) -> dict[str, object]:
    workers: list[WorkerTarget] = app.state.workers
    http_client: httpx.AsyncClient = app.state.http_client
    requested_active_count = active_worker_count(request.semantic_tags, len(workers))
    active_count = requested_active_count or app.state.active_worker_count
    active_workers = workers[:active_count]
    worker = choose_worker(request.segment_id, active_workers)

    try:
        response = await http_client.post(f"{worker.base_url}/read", json=request.model_dump())
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Assigned worker {worker.worker_id} unavailable: {exc}") from exc

    payload = response.json()
    payload["assigned_worker"] = worker.worker_id
    payload["active_worker_count"] = active_count
    return payload
