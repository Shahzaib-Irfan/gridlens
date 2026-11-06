"""Rule-based summarizer.

Two jobs: it is the fallback when the model fails validation, and it is the
baseline the LLM has to beat in the evals.
"""
from __future__ import annotations

from .events import POWER_LOSS, TAMPER, VOLTAGE
from .models import Cause, IncidentFacts, IncidentSummary, Severity


def _count(f: IncidentFacts, names: set[str]) -> int:
    return sum(v for k, v in f.event_counts.items() if k in names)


def classify(f: IncidentFacts) -> tuple[Cause, Severity]:
    power = _count(f, POWER_LOSS)
    tamper = _count(f, TAMPER)
    volt = _count(f, VOLTAGE)
    comms = f.event_counts.get("communication_lost", 0)

    if tamper and f.meter_count <= 2:
        return Cause.tampering, Severity.high
    if power:
        if f.meter_count >= 20 or len(f.transformers) > 1:
            sev = Severity.critical if not f.restored else Severity.high
            return Cause.feeder_outage, sev
        if f.share_of_transformer_meters is not None and f.share_of_transformer_meters >= 0.8 and f.meter_count > 1:
            return Cause.transformer_fault, Severity.high if not f.restored else Severity.medium
        if f.meter_count == 1:
            return Cause.single_meter_outage, Severity.low if f.restored else Severity.medium
    if volt:
        return Cause.voltage_quality, Severity.medium
    if comms:
        return Cause.communication_loss, Severity.low
    return Cause.unknown, Severity.low


_ACTIONS = {
    Cause.feeder_outage: "Escalate to the grid control room and check the feeder breaker and upstream protection.",
    Cause.transformer_fault: "Dispatch a crew to inspect the transformer and its fuses.",
    Cause.single_meter_outage: "Check the service cable and the meter at the premises.",
    Cause.tampering: "Raise a revenue-protection inspection for the listed meter(s).",
    Cause.voltage_quality: "Log for the power-quality team and check transformer tap settings.",
    Cause.communication_loss: "Check the data concentrator or modem; no field action unless it persists.",
    Cause.unknown: "Review the raw events manually.",
}


def summarize(f: IncidentFacts) -> IncidentSummary:
    cause, sev = classify(f)
    if cause is Cause.feeder_outage and f.feeders:
        where = ", ".join(f.feeders)
    else:
        where = ", ".join(f.transformers or f.feeders) or "unknown location"
    listed = f.meter_ids if f.meter_count <= 5 else []
    state = "restored" if f.restored else "not yet restored"
    return IncidentSummary(
        title=f"{cause.value.replace('_', ' ').title()} at {where}"[:120],
        severity=sev,
        probable_cause=cause,
        summary=(
            f"{f.meter_count} meter(s) at {where} raised {sum(f.event_counts.values())} events "
            f"over {f.duration_minutes} minutes. Supply is {state}."
        ),
        evidence=[f"{k}: {v}" for k, v in sorted(f.event_counts.items())],
        recommended_action=_ACTIONS[cause],
        meters_mentioned=listed,
    )
