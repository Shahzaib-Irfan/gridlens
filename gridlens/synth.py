"""Synthetic, labeled incident scenarios.

Real head-end logs are confidential, so evals run on generated scenarios whose
ground truth is known by construction. Each scenario mirrors a pattern seen in
field operations, including a few deliberately awkward ones.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import Cause, MeterEvent, Severity

CODES = {"fail": 1, "restore": 2, "cover": 40, "main_cover": 44, "dc_field": 47, "reverse": 91,
         "neutral": 63, "sag": 76, "swell": 77, "comms_lost": 200, "comms_back": 201}


@dataclass
class Scenario:
    name: str
    cause: Cause
    severity: Severity
    events: list[MeterEvent]
    registry: dict[str, set[str]] = field(default_factory=dict)
    note: str = ""


class Fleet:
    """A toy distribution network: feeders -> transformers -> meters."""

    def __init__(self, rng: random.Random, feeders: int = 3, tx_per_feeder: int = 4, meters_per_tx: int = 12):
        self.rng = rng
        self.topology: dict[str, dict[str, list[str]]] = {}
        n = 1
        for fi in range(1, feeders + 1):
            fdr = f"FDR-{fi:02d}"
            self.topology[fdr] = {}
            for ti in range(1, tx_per_feeder + 1):
                tx = f"TX-{fi:02d}{ti:02d}"
                self.topology[fdr][tx] = [f"MTR-{n + k:06d}" for k in range(meters_per_tx)]
                n += meters_per_tx

    @property
    def registry(self) -> dict[str, set[str]]:
        return {tx: set(ms) for txs in self.topology.values() for tx, ms in txs.items()}

    def where(self, meter: str) -> tuple[str, str]:
        for fdr, txs in self.topology.items():
            for tx, ms in txs.items():
                if meter in ms:
                    return fdr, tx
        raise KeyError(meter)

    def ev(self, meter: str, t: datetime, kind: str, value: float | None = None) -> MeterEvent:
        fdr, tx = self.where(meter)
        return MeterEvent(meter_id=meter, timestamp=t, code=CODES[kind], feeder=fdr, transformer=tx, value=value)

    def jitter(self, t: datetime, max_s: int = 90) -> datetime:
        return t + timedelta(seconds=self.rng.randint(0, max_s))


def _outage(fleet: Fleet, meters: list[str], t0: datetime, minutes: int, restored: bool) -> list[MeterEvent]:
    out = [fleet.ev(m, fleet.jitter(t0, 30), "fail") for m in meters]
    if restored:
        out += [fleet.ev(m, fleet.jitter(t0 + timedelta(minutes=minutes), 60), "restore") for m in meters]
    return out


def scenarios(seed: int = 7) -> list[Scenario]:
    rng = random.Random(seed)
    fleet = Fleet(rng, feeders=6)
    reg = fleet.registry
    base = datetime(2026, 10, 3, 2, 0)
    txs = [tx for t in fleet.topology.values() for tx in t]
    # Feeders 1-3 are used for feeder outages; every other scenario gets its own transformer.
    spare = txs[12:]
    out: list[Scenario] = []

    def at(h: float) -> datetime:
        return base + timedelta(hours=h)

    # Feeder outages: whole feeder loses supply.
    for i, restored in enumerate([True, False, True]):
        fdr = list(fleet.topology)[i]
        meters = [m for ms in fleet.topology[fdr].values() for m in ms]
        out.append(Scenario(f"feeder_outage_{i}", Cause.feeder_outage,
                            Severity.high if restored else Severity.critical,
                            _outage(fleet, meters, at(i * 10), rng.randint(20, 90), restored), reg))
    # Transformer faults: every meter on one transformer.
    for i, restored in enumerate([True, False, True, False]):
        tx = spare[i]
        meters = fleet.registry[tx]
        out.append(Scenario(f"transformer_fault_{i}", Cause.transformer_fault,
                            Severity.medium if restored else Severity.high,
                            _outage(fleet, sorted(meters), at(40 + i * 5), rng.randint(15, 60), restored), reg))
    # Single meter outages.
    for i, restored in enumerate([True, True, False]):
        m = sorted(fleet.registry[spare[4]])[i]
        out.append(Scenario(f"single_meter_{i}", Cause.single_meter_outage,
                            Severity.low if restored else Severity.medium,
                            _outage(fleet, [m], at(70 + i * 5), rng.randint(5, 30), restored), reg))
    # Tampering patterns.
    for i, kinds in enumerate([["cover", "dc_field"], ["main_cover", "reverse"], ["dc_field", "dc_field", "reverse"]]):
        m = sorted(fleet.registry[spare[5]])[i]
        t = at(90 + i * 5)
        out.append(Scenario(f"tampering_{i}", Cause.tampering, Severity.high,
                            [fleet.ev(m, t + timedelta(minutes=k * 2), kind) for k, kind in enumerate(kinds)], reg))
    # Voltage quality: sags or swells on a few meters.
    for i, kind in enumerate(["sag", "swell"]):
        ms = sorted(fleet.registry[spare[6]])[:3]
        t = at(110 + i * 5)
        v = (lambda: rng.uniform(170, 195)) if kind == "sag" else (lambda: rng.uniform(255, 270))
        evs = [fleet.ev(m, fleet.jitter(t, 300), kind, round(v(), 1)) for m in ms for _ in range(2)]
        out.append(Scenario(f"voltage_{kind}", Cause.voltage_quality, Severity.medium, evs, reg))
    # Communication loss on one meter.
    m = sorted(fleet.registry[spare[7]])[0]
    out.append(Scenario("comms_loss", Cause.communication_loss, Severity.low,
                        [fleet.ev(m, at(130), "comms_lost"), fleet.ev(m, at(130.2), "comms_back")], reg))

    # Hard cases: patterns a fixed rule set tends to get wrong.
    tx = spare[8]
    ms = sorted(fleet.registry[tx])
    out.append(Scenario("hard_partial_phase_loss", Cause.transformer_fault, Severity.medium,
                        _outage(fleet, ms[:4], at(150), 25, True), reg,
                        note="One phase of a three-phase transformer fails: a third of its meters drop together."))
    sag = [fleet.ev(m, fleet.jitter(at(160), 120), "sag", round(rng.uniform(150, 175), 1)) for m in ms]
    out.append(Scenario("hard_sag_then_outage", Cause.transformer_fault, Severity.high,
                        sag + _outage(fleet, ms, at(160) + timedelta(minutes=4), 40, False), reg,
                        note="Deep sags across a transformer followed by total loss of supply."))
    neutral = [fleet.ev(m, fleet.jitter(at(170), 60), "neutral") for m in ms[:6]]
    out.append(Scenario("hard_lost_neutral", Cause.transformer_fault, Severity.high, neutral, reg,
                        note="Lost neutral on a transformer: dangerous, half the meters report neutral_missing."))
    return out


def stream(seed: int = 7) -> tuple[list[MeterEvent], dict[str, set[str]]]:
    """All scenarios as one interleaved stream, as a head-end would deliver them."""
    sc = scenarios(seed)
    events = sorted((e for s in sc for e in s.events), key=lambda e: e.timestamp)
    return events, sc[0].registry
