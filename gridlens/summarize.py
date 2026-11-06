"""The LLM step: facts in, validated and grounded summary out.

Loop: ask the model -> parse JSON -> validate against the schema -> check the
text against the facts (grounding). If anything fails, send the specific
problems back to the model and retry. If it still fails, fall back to the
rule-based summary so operators never get nothing.
"""
from __future__ import annotations

import json
import re
import time

import httpx
from pydantic import ValidationError

from . import baseline
from .llm import Hybrid, Provider
from .models import Cause, IncidentFacts, IncidentSummary, SummaryResult

SYSTEM = """You are an operations analyst for an electricity distribution utility.
You turn smart-meter incident facts into a short incident report for a field operations team.

Rules:
- Use ONLY the facts provided. Never invent meter IDs, counts, times, places or readings.
- Choose probable_cause from: feeder_outage, transformer_fault, single_meter_outage, tampering,
  voltage_quality, communication_loss, unknown.
- Choose severity from: low, medium, high, critical.
  critical = many customers still without supply; high = outage affecting a transformer or confirmed tampering;
  medium = restored transformer outage, unrestored single meter, or voltage quality; low = minor or self-resolved.
- How to tell causes apart: 20+ meters or more than one transformer losing power points to the feeder.
  Most meters on one transformer losing power together points to the transformer. One meter alone is a
  single-meter problem. Cover-open, magnetic field or reverse-energy events on 1-2 meters point to tampering.
- Write the summary in plain English, 2-4 sentences, no jargon an engineer on a phone would struggle with.
- List in meters_mentioned every meter ID that appears anywhere in your text. Only mention meter IDs
  when 5 or fewer meters are affected.
- Return only a JSON object that matches the schema."""

METER_ID = re.compile(r"\b[A-Z]{2,5}-\d{3,}\b")
COUNT_CLAIM = re.compile(r"\b(\d+)\s+(?:smart\s+)?meters?\b", re.IGNORECASE)
RESTORED_CLAIM = re.compile(r"\b(?:has|have)\s+been\s+restored\b|\b(?:was|were|is|are)\s+(?:fully\s+)?restored\b", re.I)


def render_facts(f: IncidentFacts) -> str:
    # Large incidents: send the count, not the ID list. It saves tokens and removes the
    # temptation to paste dozens of IDs into a report nobody can act on.
    exclude = {"meter_ids"} if f.meter_count > 5 else set()
    return "Incident facts:\n" + f.model_dump_json(indent=2, exclude=exclude)


def check_grounding(f: IncidentFacts, s: IncidentSummary) -> list[str]:
    """Return a list of specific problems; empty means the summary is consistent with the facts."""
    issues: list[str] = []
    meters = set(f.meter_ids)
    known_ids = meters | set(f.transformers) | set(f.feeders)
    text = " ".join([s.title, s.summary, s.recommended_action, *s.evidence])

    for m in sorted(set(s.meters_mentioned)):
        if m not in meters:
            issues.append(f"Meter {m} is not part of this incident.")
    for ident in sorted(set(METER_ID.findall(text)) - known_ids - set(s.meters_mentioned)):
        issues.append(f"{ident} does not appear in the incident facts.")
    for n in COUNT_CLAIM.findall(text):
        if int(n) != f.meter_count:
            issues.append(f"Text says {n} meters but the incident has {f.meter_count}.")
    if not f.restored and RESTORED_CLAIM.search(text):  # negations ("has not been restored") don't match
        issues.append("Text says supply was restored, but at least one meter has not reported power_restored.")
    if f.meter_count > 5 and s.meters_mentioned:
        issues.append("More than 5 meters are affected, so meter IDs should not be listed.")
    return issues


def _parse(raw: str) -> IncidentSummary:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    start, end = raw.find("{"), raw.rfind("}")
    return IncidentSummary.model_validate(json.loads(raw[start : end + 1]))


def summarize_incident(f: IncidentFacts, provider: Provider | None, max_attempts: int = 3) -> SummaryResult:
    t0 = time.perf_counter()
    if provider is None:
        s = baseline.summarize(f)
        issues = check_grounding(f, s)
        return SummaryResult(facts=f, summary=s, provider="baseline", grounded=not issues,
                             grounding_issues=issues, latency_ms=(time.perf_counter() - t0) * 1000)

    if isinstance(provider, Hybrid):
        cause, _ = baseline.classify(f)
        if cause is not Cause.unknown:
            s = baseline.summarize(f)
            issues = check_grounding(f, s)
            return SummaryResult(facts=f, summary=s, provider=f"{provider.name}:rules", grounded=not issues,
                                 grounding_issues=issues, latency_ms=(time.perf_counter() - t0) * 1000)

    user = render_facts(f)
    feedback: list[str] = []
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        prompt = user if not feedback else (
            user + "\n\nYour previous answer had these problems. Fix them and return the full JSON again:\n- "
            + "\n- ".join(feedback)
        )
        try:
            s = _parse(provider.complete(SYSTEM, prompt))
        except (ValueError, ValidationError) as e:
            feedback = [f"The output was not valid JSON for the schema: {str(e)[:300]}"]
            continue
        except httpx.HTTPError as e:  # provider down or rejecting the request: retry, then fall back
            feedback = []
            last_error = f"Provider error: {type(e).__name__}: {str(e)[:200]}"
            continue
        issues = check_grounding(f, s)
        if not issues:
            return SummaryResult(facts=f, summary=s, provider=provider.name, grounded=True,
                                 attempts=attempt, latency_ms=(time.perf_counter() - t0) * 1000)
        feedback = issues

    s = baseline.summarize(f)
    return SummaryResult(facts=f, summary=s, provider=provider.name, grounded=not check_grounding(f, s),
                         grounding_issues=feedback or ([last_error] if last_error else []),
                         attempts=max_attempts, fell_back=True,
                         latency_ms=(time.perf_counter() - t0) * 1000)
