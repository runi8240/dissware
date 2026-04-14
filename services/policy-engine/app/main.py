from __future__ import annotations

import os
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field


ADMIT_SIZE_THRESHOLD_BYTES = int(os.getenv("POLICY_ADMIT_SIZE_THRESHOLD_BYTES", str(64 * 1024 * 1024)))
ADMIT_FREQUENCY_THRESHOLD = int(os.getenv("POLICY_ADMIT_FREQUENCY_THRESHOLD", "3"))


class DecisionRequest(BaseModel):
    segment_id: str
    size_bytes: int = Field(..., gt=0)
    frequency: int = Field(..., ge=1)
    recency_seconds: int = Field(..., ge=0)


class DecisionResponse(BaseModel):
    decision: Literal["Admit", "Retain", "Evict"]
    reason: str


app = FastAPI(title="policy-engine")


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/decide", response_model=DecisionResponse)
async def decide(request: DecisionRequest) -> DecisionResponse:
    if request.frequency >= ADMIT_FREQUENCY_THRESHOLD and request.size_bytes <= ADMIT_SIZE_THRESHOLD_BYTES:
        return DecisionResponse(
            decision="Admit",
            reason="Hot and small enough to promote into the cache tier.",
        )

    return DecisionResponse(
        decision="Retain",
        reason="Keep only in the durable object-store tier for now.",
    )
