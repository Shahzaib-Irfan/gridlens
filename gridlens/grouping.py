"""Turn a raw event stream into incidents with deterministic, checkable facts.

The LLM never sees raw events. It sees IncidentFacts, which are computed here,
so every number in a summary can be traced back to code that can be tested.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Iterable

from .events import POWER_BACK, POWER_LOSS, name_of
from .models import IncidentFacts, MeterEvent

DEFAULT_GAP = timedelta(minutes=15)


def _bucket_key(e: MeterEvent) -> str:
    # Meters on the same transformer share a fate; meters without topology stand alone.
    return f"tx:{e.transformer}" if e.transformer else f"meter:{e.meter_id}"


def _split_by_gap(events: list[MeterEvent], gap: timedelta, max_open: timedelta) -> list[list[MeterEvent]]:
    """Start a new incident after a quiet gap, but not while a meter is still without power.

    A two-hour outage is silent between power_failure and power_restored; splitting on the
    gap alone would report the failure and the restoration as two unrelated incidents.
    `max_open` stops a meter that never reports back from swallowing every later event.
    """
    chunks: list[list[MeterEvent]] = []
    dark: set[str] = set()
    for e in sorted(events, key=lambda x: x.timestamp):
        quiet = not chunks or e.timestamp - chunks[-1][-1].timestamp > gap
        stale = bool(chunks) and e.timestamp - chunks[-1][0].timestamp > max_open
        if quiet and (not dark or stale):
            chunks.append([e])
            dark.clear()
        else:
            chunks[-1].append(e)
        n = name_of(e.code)
        if n in POWER_LOSS:
            dark.add(e.meter_id)
        elif n in POWER_BACK:
            dark.discard(e.meter_id)
    return chunks


def _starts_with_power_loss(chunk: list[MeterEvent]) -> bool:
    return name_of(chunk[0].code) in POWER_LOSS


def _merge_feeder_outages(chunks: list[list[MeterEvent]], window: timedelta) -> list[list[MeterEvent]]:
    """Merge transformer-level outages on the same feeder that begin within `window` of each other.

    When a feeder breaker trips, every transformer on it goes dark at once. Reporting that as
    one incident per transformer would bury the real cause.
    """
    outages = sorted((c for c in chunks if c[0].feeder and _starts_with_power_loss(c)), key=lambda c: c[0].timestamp)
    others = [c for c in chunks if not (c[0].feeder and _starts_with_power_loss(c))]
    merged: list[list[MeterEvent]] = []
    for c in outages:
        target = next((m for m in merged if m[0].feeder == c[0].feeder
                       and abs(c[0].timestamp - m[0].timestamp) <= window), None)
        if target is None:
            merged.append(list(c))
        else:
            target.extend(c)
            target.sort(key=lambda x: x.timestamp)
    return merged + others


def _restored(events: list[MeterEvent]) -> bool:
    lost: set[str] = set()
    back: set[str] = set()
    for e in sorted(events, key=lambda x: x.timestamp):
        n = name_of(e.code)
        if n in POWER_LOSS:
            lost.add(e.meter_id)
            back.discard(e.meter_id)
        elif n in POWER_BACK:
            back.add(e.meter_id)
    return lost <= back


def _facts(chunk: list[MeterEvent], meters_per_tx: dict[str, set[str]]) -> IncidentFacts:
    meters = sorted({e.meter_id for e in chunk})
    txs = sorted({e.transformer for e in chunk if e.transformer})
    feeders = sorted({e.feeder for e in chunk if e.feeder})
    volts = [e.value for e in chunk if e.value is not None]
    start, end = chunk[0].timestamp, chunk[-1].timestamp
    known = set().union(*(meters_per_tx.get(t, set()) for t in txs)) if txs else set()
    share = round(len(meters) / len(known), 3) if known else None
    digest = hashlib.sha1(f"{start.isoformat()}|{','.join(meters)}".encode()).hexdigest()[:8]
    return IncidentFacts(
        incident_id=f"INC-{start:%Y%m%d}-{digest}",
        start=start,
        end=end,
        duration_minutes=round((end - start).total_seconds() / 60, 1),
        meter_ids=meters,
        meter_count=len(meters),
        feeders=feeders,
        transformers=txs,
        event_counts=dict(Counter(name_of(e.code) for e in chunk)),
        restored=_restored(chunk),
        min_voltage=min(volts) if volts else None,
        max_voltage=max(volts) if volts else None,
        share_of_transformer_meters=share,
    )


def group_incidents(
    events: Iterable[MeterEvent],
    gap: timedelta = DEFAULT_GAP,
    meters_per_transformer: dict[str, set[str]] | None = None,
    feeder_window: timedelta = timedelta(minutes=2),
    max_open: timedelta = timedelta(hours=6),
) -> list[IncidentFacts]:
    """Group events by transformer (or meter) and split them wherever there is a quiet gap.

    `meters_per_transformer` is the fleet registry. Without it, the registry is
    inferred from the events themselves, which undercounts healthy meters.
    """
    events = list(events)
    registry: dict[str, set[str]] = defaultdict(set)
    if meters_per_transformer:
        for tx, ms in meters_per_transformer.items():
            registry[tx] |= set(ms)
    else:
        for e in events:
            if e.transformer:
                registry[e.transformer].add(e.meter_id)

    buckets: dict[str, list[MeterEvent]] = defaultdict(list)
    for e in events:
        buckets[_bucket_key(e)].append(e)

    chunks = [chunk for b in buckets.values() for chunk in _split_by_gap(b, gap, max_open)]
    chunks = _merge_feeder_outages(chunks, feeder_window)
    incidents = [_facts(chunk, registry) for chunk in chunks]
    return sorted(incidents, key=lambda i: i.start)
