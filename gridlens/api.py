"""HTTP API. Run with: uvicorn gridlens.api:app --reload"""
from __future__ import annotations

import os

from fastapi import FastAPI
from pydantic import BaseModel

from .grouping import group_incidents
from .llm import get_provider
from .models import MeterEvent, SummaryResult
from .summarize import summarize_incident

app = FastAPI(title="GridLens", description="Incident reports from smart-meter event streams")


class SummarizeRequest(BaseModel):
    events: list[MeterEvent]
    meters_per_transformer: dict[str, list[str]] | None = None
    provider: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/incidents/summarize", response_model=list[SummaryResult])
def summarize(req: SummarizeRequest) -> list[SummaryResult]:
    provider = get_provider(req.provider or os.getenv("GRIDLENS_PROVIDER", "baseline"))
    registry = {k: set(v) for k, v in req.meters_per_transformer.items()} if req.meters_per_transformer else None
    return [summarize_incident(f, provider) for f in group_incidents(req.events, meters_per_transformer=registry)]
