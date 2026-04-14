from __future__ import annotations

import os
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field


ADMIT_SIZE_THRESHOLD_BYTES = int(os.getenv("POLICY_ADMIT_SIZE_THRESHOLD_BYTES", str(64 * 1024 * 1024)))
ADMIT_FREQUENCY_THRESHOLD = int(os.getenv("POLICY_ADMIT_FREQUENCY_THRESHOLD", "3"))
LRU_RECENCY_THRESHOLD_SECONDS = int(os.getenv("POLICY_LRU_RECENCY_THRESHOLD_SECONDS", "60"))
ML_SCORE_THRESHOLD = float(os.getenv("POLICY_ML_SCORE_THRESHOLD", "0.55"))
SUPPORTED_POLICIES = {"baseline-lru", "baseline-lfu", "baseline-size-aware", "ml"}


class DecisionRequest(BaseModel):
    policy_name: Literal["baseline-lru", "baseline-lfu", "baseline-size-aware", "ml"]
    segment_id: str
    size_bytes: int = Field(..., gt=0)
    frequency: int = Field(..., ge=1)
    recency_seconds: int = Field(..., ge=0)
    cache_capacity_bytes: int = Field(..., gt=0)
    cache_used_bytes: int = Field(..., ge=0)


class DecisionResponse(BaseModel):
    policy_name: Literal["baseline-lru", "baseline-lfu", "baseline-size-aware", "ml"]
    action: Literal["admit", "retain", "evict"]
    score: float
    reason: str


app = FastAPI(title="policy-engine")


def clamp_score(value: float) -> float:
    return max(0.0, min(1.0, value))


def remaining_cache_bytes(request: DecisionRequest) -> int:
    return max(0, request.cache_capacity_bytes - request.cache_used_bytes)


def baseline_lru_policy(request: DecisionRequest) -> DecisionResponse:
    recency_score = 1.0 / (1.0 + (request.recency_seconds / max(1, LRU_RECENCY_THRESHOLD_SECONDS)))
    fits_now = request.size_bytes <= remaining_cache_bytes(request)
    action = "admit" if request.recency_seconds <= LRU_RECENCY_THRESHOLD_SECONDS and fits_now else "retain"
    return DecisionResponse(
        policy_name=request.policy_name,
        action=action,
        score=clamp_score(recency_score),
        reason="LRU-style admission prefers recently accessed segments that fit the cache budget.",
    )


def baseline_lfu_policy(request: DecisionRequest) -> DecisionResponse:
    frequency_score = clamp_score(request.frequency / max(1, ADMIT_FREQUENCY_THRESHOLD * 2))
    fits_eventually = request.size_bytes <= request.cache_capacity_bytes
    action = "admit" if request.frequency >= ADMIT_FREQUENCY_THRESHOLD and fits_eventually else "retain"
    return DecisionResponse(
        policy_name=request.policy_name,
        action=action,
        score=frequency_score,
        reason="LFU-style admission prefers frequently reused segments that can fit in cache.",
    )


def baseline_size_aware_policy(request: DecisionRequest) -> DecisionResponse:
    recency_component = 1.0 / (1.0 + request.recency_seconds / max(1, LRU_RECENCY_THRESHOLD_SECONDS))
    size_penalty = min(1.0, request.size_bytes / request.cache_capacity_bytes)
    score = clamp_score((0.7 * recency_component) + (0.3 * (1.0 - size_penalty)))
    action = "admit" if score >= 0.5 and request.size_bytes <= request.cache_capacity_bytes else "retain"
    return DecisionResponse(
        policy_name=request.policy_name,
        action=action,
        score=score,
        reason="Size-aware LRU balances recency against space amplification from larger segments.",
    )


def ml_policy(request: DecisionRequest) -> DecisionResponse:
    recency_component = 1.0 / (1.0 + request.recency_seconds / 60.0)
    frequency_component = clamp_score(request.frequency / 10.0)
    size_component = 1.0 - min(1.0, request.size_bytes / request.cache_capacity_bytes)
    score = clamp_score((0.45 * frequency_component) + (0.35 * recency_component) + (0.20 * size_component))
    action = "admit" if score >= ML_SCORE_THRESHOLD and request.size_bytes <= request.cache_capacity_bytes else "retain"
    return DecisionResponse(
        policy_name=request.policy_name,
        action=action,
        score=score,
        reason="Current ML mode is a placeholder scored model over frequency, recency, and size.",
    )


POLICY_HANDLERS = {
    "baseline-lru": baseline_lru_policy,
    "baseline-lfu": baseline_lfu_policy,
    "baseline-size-aware": baseline_size_aware_policy,
    "ml": ml_policy,
}


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/policies")
async def list_policies() -> dict[str, list[str]]:
    return {"policies": sorted(SUPPORTED_POLICIES)}


@app.post("/decide", response_model=DecisionResponse)
async def decide(request: DecisionRequest) -> DecisionResponse:
    return POLICY_HANDLERS[request.policy_name](request)
