"""Command line entry point.

    gridlens generate data/events.jsonl            # write a synthetic event stream
    gridlens summarize data/events.jsonl -p ollama  # incidents -> reports
    gridlens eval -p ollama -p baseline             # score providers on labeled scenarios
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evals import report, run
from .grouping import group_incidents
from .llm import get_provider
from .models import MeterEvent
from .summarize import summarize_incident
from .synth import stream


def _generate(a: argparse.Namespace) -> None:
    events, registry = stream(a.seed)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(e.model_dump_json() for e in events) + "\n")
    reg_path = out.with_name("registry.json")
    reg_path.write_text(json.dumps({k: sorted(v) for k, v in registry.items()}, indent=2))
    print(f"Wrote {len(events)} events to {out} and the meter registry to {reg_path}")


def _load(path: str) -> list[MeterEvent]:
    return [MeterEvent.model_validate_json(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _summarize(a: argparse.Namespace) -> None:
    events = _load(a.events)
    registry = None
    if a.registry:
        registry = {k: set(v) for k, v in json.loads(Path(a.registry).read_text()).items()}
    provider = get_provider(a.provider)
    for f in group_incidents(events, meters_per_transformer=registry):
        r = summarize_incident(f, provider)
        s = r.summary
        if a.json:
            print(r.model_dump_json())
            continue
        flag = " (rule-based fallback)" if r.fell_back else ""
        print(f"\n[{s.severity.value.upper()}] {s.title}  ({f.incident_id}){flag}")
        print(f"  {s.summary}")
        print(f"  Action: {s.recommended_action}")


def _eval(a: argparse.Namespace) -> None:
    blocks = [report(run(get_provider(p), a.seed), p) for p in a.provider]
    text = "\n\n".join(blocks)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="gridlens", description="LLM incident reports from smart-meter event streams")
    sub = p.add_subparsers(required=True)

    g = sub.add_parser("generate", help="write a synthetic event stream")
    g.add_argument("out")
    g.add_argument("--seed", type=int, default=7)
    g.set_defaults(fn=_generate)

    s = sub.add_parser("summarize", help="group events into incidents and summarize them")
    s.add_argument("events")
    s.add_argument("--registry", help="JSON map of transformer -> meter IDs")
    s.add_argument("-p", "--provider", default="ollama", help="ollama[:model] | anthropic | openai | baseline")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=_summarize)

    e = sub.add_parser("eval", help="score providers on labeled scenarios")
    e.add_argument("-p", "--provider", action="append", default=None)
    e.add_argument("--seed", type=int, default=7)
    e.add_argument("--out")
    e.set_defaults(fn=_eval)

    a = p.parse_args(argv)
    if getattr(a, "provider", None) is None and a.fn is _eval:
        a.provider = ["baseline"]
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
