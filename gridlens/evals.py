"""Evaluation harness: run a provider over labeled scenarios and score it.

Metrics
- cause_accuracy / severity_accuracy: against the scenario's ground truth
- grounded_rate: summaries with no grounding issues on the first accepted answer
- fallback_rate: incidents where the model never produced a valid, grounded answer
- mean_attempts: how often the repair loop had to step in
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from .grouping import group_incidents
from .llm import Provider
from .summarize import summarize_incident
from .synth import Scenario, scenarios


@dataclass
class CaseResult:
    name: str
    expected_cause: str
    got_cause: str
    expected_severity: str
    got_severity: str
    grounded: bool
    fell_back: bool
    attempts: int
    latency_ms: float
    incidents: int

    @property
    def cause_ok(self) -> bool:
        return self.expected_cause == self.got_cause

    @property
    def severity_ok(self) -> bool:
        return self.expected_severity == self.got_severity


def run_case(sc: Scenario, provider: Provider | None) -> CaseResult:
    incidents = group_incidents(sc.events, meters_per_transformer=sc.registry)
    # Each scenario is built to form one incident; if grouping splits it, score the largest piece.
    f = max(incidents, key=lambda i: sum(i.event_counts.values()))
    r = summarize_incident(f, provider)
    return CaseResult(sc.name, sc.cause.value, r.summary.probable_cause.value, sc.severity.value,
                      r.summary.severity.value, r.grounded, r.fell_back, r.attempts, r.latency_ms, len(incidents))


def run(provider: Provider | None, seed: int = 7) -> list[CaseResult]:
    return [run_case(sc, provider) for sc in scenarios(seed)]


def report(results: list[CaseResult], label: str) -> str:
    n = len(results)
    hard = [r for r in results if r.name.startswith("hard_")]
    pct = lambda xs: f"{100 * sum(xs) / len(xs):.0f}%" if xs else "n/a"
    lines = [
        f"### {label}",
        "",
        "| metric | value |",
        "|---|---|",
        f"| cases | {n} |",
        f"| cause accuracy | {pct([r.cause_ok for r in results])} |",
        f"| severity accuracy | {pct([r.severity_ok for r in results])} |",
        f"| cause accuracy, hard cases only | {pct([r.cause_ok for r in hard])} |",
        f"| grounded | {pct([r.grounded for r in results])} |",
        f"| fell back to rules | {pct([r.fell_back for r in results])} |",
        f"| mean attempts | {statistics.mean(r.attempts for r in results):.2f} |",
        f"| median latency | {statistics.median(r.latency_ms for r in results):.0f} ms |",
        "",
        "| case | expected | got | severity (exp / got) |",
        "|---|---|---|---|",
    ]
    for r in results:
        mark = "ok" if r.cause_ok else "MISS"
        lines.append(f"| {r.name} | {r.expected_cause} | {r.got_cause} ({mark}) | {r.expected_severity} / {r.got_severity} |")
    return "\n".join(lines)
