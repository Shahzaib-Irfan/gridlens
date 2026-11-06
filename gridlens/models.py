"""Core data models shared by the pipeline, the LLM layer and the evals."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MeterEvent(BaseModel):
    """One raw event as a head-end system or Kafka topic would emit it."""

    meter_id: str
    timestamp: datetime
    code: int = Field(description="Standard event code, see gridlens.events.CATALOG")
    feeder: str | None = None
    transformer: str | None = None
    value: float | None = Field(default=None, description="Optional reading, e.g. voltage in volts")


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Cause(str, Enum):
    feeder_outage = "feeder_outage"
    transformer_fault = "transformer_fault"
    single_meter_outage = "single_meter_outage"
    tampering = "tampering"
    voltage_quality = "voltage_quality"
    communication_loss = "communication_loss"
    unknown = "unknown"


class IncidentFacts(BaseModel):
    """Deterministic facts computed from events. This is all the LLM is allowed to see."""

    incident_id: str
    start: datetime
    end: datetime
    duration_minutes: float
    meter_ids: list[str]
    meter_count: int
    feeders: list[str]
    transformers: list[str]
    event_counts: dict[str, int] = Field(description="Event name -> count")
    restored: bool = Field(description="Every meter that lost power reported power restored")
    min_voltage: float | None = None
    max_voltage: float | None = None
    share_of_transformer_meters: float | None = Field(
        default=None, description="Fraction of known meters on the affected transformer(s) that are in this incident"
    )


class IncidentSummary(BaseModel):
    """Structured output the model must return."""

    title: str = Field(max_length=120)
    severity: Severity
    probable_cause: Cause
    summary: str = Field(description="2-4 plain-English sentences for a field operations team")
    evidence: list[str] = Field(description="Facts from the input that support the cause")
    recommended_action: str
    meters_mentioned: list[str] = Field(
        default_factory=list, description="Every meter ID referenced in the text above"
    )


class SummaryResult(BaseModel):
    """What the pipeline returns for one incident, including how it was produced."""

    facts: IncidentFacts
    summary: IncidentSummary
    provider: str
    grounded: bool
    grounding_issues: list[str] = Field(default_factory=list)
    attempts: int = 1
    fell_back: bool = False
    latency_ms: float = 0.0
