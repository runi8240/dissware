from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import joblib
import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel, Field
from sklearn.linear_model import LogisticRegression


ADMIT_SIZE_THRESHOLD_BYTES = int(os.getenv("POLICY_ADMIT_SIZE_THRESHOLD_BYTES", str(64 * 1024 * 1024)))
ADMIT_FREQUENCY_THRESHOLD = int(os.getenv("POLICY_ADMIT_FREQUENCY_THRESHOLD", "3"))
LRU_RECENCY_THRESHOLD_SECONDS = int(os.getenv("POLICY_LRU_RECENCY_THRESHOLD_SECONDS", "60"))
ML_SCORE_THRESHOLD = float(os.getenv("POLICY_ML_SCORE_THRESHOLD", "0.55"))
ML_MODEL_PATH = Path(os.getenv("POLICY_ML_MODEL_PATH", "/app/models/logreg_reuse_model.joblib"))
ML_REUSE_HORIZON_REQUESTS = int(os.getenv("POLICY_ML_REUSE_HORIZON_REQUESTS", "5"))
ML_FEATURE_SET = os.getenv("POLICY_ML_FEATURE_SET", "set-b").lower()
SUPPORTED_POLICIES = {"baseline-lru", "baseline-lfu", "baseline-size-aware", "baseline-admit-all", "ml"}
FEATURE_SETS = {
    "set-a": [
        "recency_seconds",
        "frequency",
        "inter_arrival_gap_seconds",
        "rolling_hit_count",
    ],
    "set-b": [
        "recency_seconds",
        "frequency",
        "inter_arrival_gap_seconds",
        "rolling_hit_count",
        "size_bytes",
        "estimated_object_store_latency_ms",
        "transfer_cost_proxy",
    ],
    "set-c": [
        "recency_seconds",
        "frequency",
        "inter_arrival_gap_seconds",
        "rolling_hit_count",
        "size_bytes",
        "estimated_object_store_latency_ms",
        "transfer_cost_proxy",
        "query_type_code",
        "object_class_code",
        "workload_phase_code",
    ],
}
QUERY_TYPE_CODES = {"scan": 0.0, "lookup": 1.0, "join": 2.0, "aggregate": 3.0, "unknown": -1.0}
OBJECT_CLASS_CODES = {"fact": 0.0, "dimension": 1.0, "aggregate": 2.0, "unknown": -1.0}
WORKLOAD_PHASE_CODES = {
    "stationary": 0.0,
    "bursty-base": 1.0,
    "bursty-burst": 2.0,
    "phase-1": 3.0,
    "phase-2": 4.0,
    "phase-3": 5.0,
    "pre-scale": 6.0,
    "scale-out": 7.0,
    "post-scale": 8.0,
    "scale-in": 9.0,
    "unknown": -1.0,
}


class DecisionRequest(BaseModel):
    policy_name: Literal["baseline-lru", "baseline-lfu", "baseline-size-aware", "baseline-admit-all", "ml"]
    segment_id: str
    size_bytes: int = Field(..., gt=0)
    frequency: int = Field(..., ge=1)
    recency_seconds: int = Field(..., ge=0)
    inter_arrival_gap_seconds: float = Field(..., ge=0)
    rolling_hit_count: int = Field(..., ge=0)
    estimated_object_store_latency_ms: float = Field(..., ge=0)
    transfer_cost_proxy: float = Field(..., ge=0)
    query_type: str | None = None
    object_class: str | None = None
    workload_phase: str | None = None
    cache_capacity_bytes: int = Field(..., gt=0)
    cache_used_bytes: int = Field(..., ge=0)


class DecisionResponse(BaseModel):
    policy_name: Literal["baseline-lru", "baseline-lfu", "baseline-size-aware", "baseline-admit-all", "ml"]
    action: Literal["admit", "retain", "evict"]
    score: float
    reason: str


class MLModelBundle(BaseModel):
    model_path: str
    loaded: bool
    model_type: Literal["sklearn-logistic-regression"]
    prediction_target: str
    reuse_horizon_requests: int
    active_feature_set: Literal["set-a", "set-b", "set-c"]
    feature_names: list[str]


class FeatureSetCatalog(BaseModel):
    active_feature_set: Literal["set-a", "set-b", "set-c"]
    feature_sets: dict[str, list[str]]


@asynccontextmanager
async def lifespan(app: FastAPI):
    ml_bundle, ml_model = load_ml_bundle()
    app.state.ml_bundle = ml_bundle
    app.state.ml_model = ml_model
    yield


app = FastAPI(title="policy-engine", lifespan=lifespan)


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


def baseline_admit_all_policy(request: DecisionRequest) -> DecisionResponse:
    fits_eventually = request.size_bytes <= request.cache_capacity_bytes
    return DecisionResponse(
        policy_name=request.policy_name,
        action="admit" if fits_eventually else "retain",
        score=1.0 if fits_eventually else 0.0,
        reason=(
            "Admit-all baseline admits every segment that can fit in cache, "
            "forcing the worker cache manager to exercise replacement."
        ),
    )


def default_feature_names() -> list[str]:
    return FEATURE_SETS[ML_FEATURE_SET]


def build_ml_feature_vector(request: DecisionRequest, feature_names: list[str]) -> np.ndarray:
    feature_map = {
        "frequency": float(request.frequency),
        "recency_seconds": float(request.recency_seconds),
        "inter_arrival_gap_seconds": float(request.inter_arrival_gap_seconds),
        "rolling_hit_count": float(request.rolling_hit_count),
        "size_bytes": float(request.size_bytes),
        "estimated_object_store_latency_ms": float(request.estimated_object_store_latency_ms),
        "transfer_cost_proxy": float(request.transfer_cost_proxy),
        "query_type_code": QUERY_TYPE_CODES.get((request.query_type or "unknown").lower(), -1.0),
        "object_class_code": OBJECT_CLASS_CODES.get((request.object_class or "unknown").lower(), -1.0),
        "workload_phase_code": WORKLOAD_PHASE_CODES.get((request.workload_phase or "unknown").lower(), -1.0),
    }
    return np.array([[feature_map[name] for name in feature_names]], dtype=float)


def load_ml_bundle() -> tuple[MLModelBundle, LogisticRegression | None]:
    prediction_target = (
        "binary label: whether the segment will be reused within the next "
        f"{ML_REUSE_HORIZON_REQUESTS} requests"
    )
    if not ML_MODEL_PATH.exists():
        return (
            MLModelBundle(
                model_path=str(ML_MODEL_PATH),
                loaded=False,
                model_type="sklearn-logistic-regression",
                prediction_target=prediction_target,
                reuse_horizon_requests=ML_REUSE_HORIZON_REQUESTS,
                active_feature_set=ML_FEATURE_SET,
                feature_names=default_feature_names(),
            ),
            None,
        )

    artifact = joblib.load(ML_MODEL_PATH)
    model = artifact["model"]
    feature_names = artifact.get("feature_names", default_feature_names())
    if not isinstance(model, LogisticRegression):
        raise TypeError("Loaded ML artifact is not a sklearn LogisticRegression model")

    return (
        MLModelBundle(
            model_path=str(ML_MODEL_PATH),
            loaded=True,
            model_type="sklearn-logistic-regression",
            prediction_target=prediction_target,
            reuse_horizon_requests=ML_REUSE_HORIZON_REQUESTS,
            active_feature_set=ML_FEATURE_SET,
            feature_names=feature_names,
        ),
        model,
    )


def ml_policy(request: DecisionRequest) -> DecisionResponse:
    ml_bundle: MLModelBundle = app.state.ml_bundle
    model: LogisticRegression | None = app.state.ml_model

    if not ml_bundle.loaded or model is None:
        recency_component = 1.0 / (1.0 + request.recency_seconds / 60.0)
        frequency_component = clamp_score(request.frequency / 10.0)
        inter_arrival_component = 1.0 / (1.0 + request.inter_arrival_gap_seconds / 60.0)
        rolling_hit_component = clamp_score(request.rolling_hit_count / 10.0)
        size_component = 1.0 - min(1.0, request.size_bytes / request.cache_capacity_bytes)
        score = clamp_score(
            (0.30 * frequency_component)
            + (0.25 * recency_component)
            + (0.20 * inter_arrival_component)
            + (0.15 * rolling_hit_component)
            + (0.10 * size_component)
        )
        action = "admit" if score >= ML_SCORE_THRESHOLD and request.size_bytes <= request.cache_capacity_bytes else "retain"
        return DecisionResponse(
            policy_name=request.policy_name,
            action=action,
            score=score,
            reason=(
                "ML policy is formulated as sklearn logistic regression for reuse-within-horizon, "
                "but no trained model artifact is loaded yet; using fallback score."
            ),
        )

    feature_vector = build_ml_feature_vector(request, ml_bundle.feature_names)
    probability = float(model.predict_proba(feature_vector)[0][1])
    score = clamp_score(probability)
    action = "admit" if score >= ML_SCORE_THRESHOLD and request.size_bytes <= request.cache_capacity_bytes else "retain"
    return DecisionResponse(
        policy_name=request.policy_name,
        action=action,
        score=score,
        reason=(
            "Logistic-regression probability for the label "
            f"'reused within next {ml_bundle.reuse_horizon_requests} requests'."
        ),
    )


POLICY_HANDLERS = {
    "baseline-lru": baseline_lru_policy,
    "baseline-lfu": baseline_lfu_policy,
    "baseline-size-aware": baseline_size_aware_policy,
    "baseline-admit-all": baseline_admit_all_policy,
    "ml": ml_policy,
}


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/policies")
async def list_policies() -> dict[str, list[str]]:
    return {"policies": sorted(SUPPORTED_POLICIES)}


@app.get("/ml/status", response_model=MLModelBundle)
async def ml_status() -> MLModelBundle:
    return app.state.ml_bundle


@app.get("/ml/feature-sets", response_model=FeatureSetCatalog)
async def ml_feature_sets() -> FeatureSetCatalog:
    return FeatureSetCatalog(active_feature_set=ML_FEATURE_SET, feature_sets=FEATURE_SETS)


@app.post("/decide", response_model=DecisionResponse)
async def decide(request: DecisionRequest) -> DecisionResponse:
    return POLICY_HANDLERS[request.policy_name](request)
