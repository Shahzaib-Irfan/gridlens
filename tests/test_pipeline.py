import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from gridlens.api import app
from gridlens.grouping import group_incidents
from gridlens.llm import ScriptedProvider
from gridlens.models import MeterEvent
from gridlens.summarize import check_grounding, summarize_incident
from gridlens.synth import scenarios, stream

T0 = datetime(2026, 1, 1, 8, 0)


def ev(m, minutes, code, tx="TX-1", value=None):
    return MeterEvent(meter_id=m, timestamp=T0 + timedelta(minutes=minutes), code=code, transformer=tx,
                      feeder="FDR-1", value=value)


def good_json(**over):
    d = {
        "title": "Transformer outage at TX-1",
        "severity": "medium",
        "probable_cause": "transformer_fault",
        "summary": "2 meters on TX-1 lost power and supply came back after 10 minutes.",
        "evidence": ["power_failure: 2", "power_restored: 2"],
        "recommended_action": "Inspect the transformer fuses.",
        "meters_mentioned": ["MTR-001", "MTR-002"],
    }
    d.update(over)
    return json.dumps(d)


def two_meter_incident():
    events = [ev("MTR-001", 0, 1), ev("MTR-002", 1, 1), ev("MTR-001", 10, 2), ev("MTR-002", 10, 2)]
    [f] = group_incidents(events, meters_per_transformer={"TX-1": {"MTR-001", "MTR-002"}})
    return f


def test_grouping_splits_on_quiet_gap_and_by_transformer():
    events = [ev("A-100", 0, 1), ev("A-100", 5, 2), ev("A-100", 120, 1), ev("B-200", 30, 1, tx="TX-2")]
    incidents = group_incidents(events)
    assert len(incidents) == 3


def test_facts_restored_and_share():
    f = two_meter_incident()
    assert f.restored is True
    assert f.meter_count == 2
    assert f.share_of_transformer_meters == 1.0
    assert f.event_counts == {"power_failure": 2, "power_restored": 2}


def test_not_restored_when_any_meter_stays_dark():
    events = [ev("MTR-001", 0, 1), ev("MTR-002", 0, 1), ev("MTR-001", 10, 2)]
    [f] = group_incidents(events)
    assert f.restored is False


def test_grounding_catches_invented_meter_and_wrong_count():
    f = two_meter_incident()
    from gridlens.models import IncidentSummary

    bad = IncidentSummary.model_validate_json(
        good_json(summary="3 meters lost power including MTR-999.", meters_mentioned=["MTR-999"])
    )
    issues = check_grounding(f, bad)
    assert any("MTR-999" in i for i in issues)
    assert any("3 meters" in i for i in issues)


def test_grounding_flags_false_restoration_but_allows_negation():
    events = [ev("MTR-001", 0, 1)]
    [f] = group_incidents(events)
    from gridlens.models import IncidentSummary

    claims = IncidentSummary.model_validate_json(good_json(summary="Supply has been restored.", meters_mentioned=[]))
    honest = IncidentSummary.model_validate_json(good_json(summary="Supply has not been restored yet.", meters_mentioned=[]))
    assert any("restored" in i for i in check_grounding(f, claims))
    assert not any("restored" in i for i in check_grounding(f, honest))


def test_repair_loop_recovers_from_bad_json_then_ungrounded_answer():
    f = two_meter_incident()
    provider = ScriptedProvider([
        "not json at all",
        good_json(meters_mentioned=["MTR-777"]),
        "```json\n" + good_json() + "\n```",
    ])
    r = summarize_incident(f, provider)
    assert r.grounded and not r.fell_back
    assert r.attempts == 3
    # The second and third prompts carry the specific problems back to the model.
    assert "not valid JSON" in provider.calls[1][1]
    assert "MTR-777" in provider.calls[2][1]


def test_falls_back_to_rules_after_max_attempts():
    f = two_meter_incident()
    r = summarize_incident(f, ScriptedProvider(["{}", "{}", "{}"]))
    assert r.fell_back
    assert r.summary.probable_cause.value == "transformer_fault"


def test_every_scenario_forms_one_incident():
    for sc in scenarios():
        assert len(group_incidents(sc.events, meters_per_transformer=sc.registry)) == 1, sc.name


def test_interleaved_stream_separates_scenarios():
    events, registry = stream()
    assert len(group_incidents(events, meters_per_transformer=registry)) == len(scenarios())


def test_api_baseline():
    client = TestClient(app)
    events = [json.loads(e.model_dump_json()) for e in [ev("MTR-001", 0, 40), ev("MTR-001", 2, 47)]]
    r = client.post("/incidents/summarize", json={"events": events, "provider": "baseline"})
    assert r.status_code == 200
    body = r.json()
    assert body[0]["summary"]["probable_cause"] == "tampering"
    assert body[0]["grounded"] is True


def test_long_outage_stays_one_incident():
    events = [ev("MTR-001", 0, 1), ev("MTR-001", 90, 2)]  # 90 silent minutes, well past the 15 min gap
    [f] = group_incidents(events)
    assert f.restored and f.duration_minutes == 90


def test_unrestored_meter_does_not_swallow_later_events_forever():
    events = [ev("MTR-001", 0, 1), ev("MTR-002", 60 * 24, 40)]  # next day, same transformer
    assert len(group_incidents(events)) == 2


def test_simultaneous_transformer_outages_on_a_feeder_merge():
    events = [ev("MTR-001", 0, 1, tx="TX-1"), ev("MTR-101", 1, 1, tx="TX-2"), ev("MTR-201", 30, 1, tx="TX-3")]
    incidents = group_incidents(events)
    assert sorted(i.meter_count for i in incidents) == [1, 2]


def test_provider_outage_falls_back_instead_of_crashing():
    import httpx

    class Down:
        name = "down"

        def complete(self, system, user):
            raise httpx.ConnectError("connection refused")

    r = summarize_incident(two_meter_incident(), Down())
    assert r.fell_back and "Provider error" in r.grounding_issues[0]


def test_large_incidents_do_not_send_meter_ids_to_the_model():
    from gridlens.summarize import render_facts

    sc = next(s for s in scenarios() if s.name == "feeder_outage_0")
    [f] = group_incidents(sc.events, meters_per_transformer=sc.registry)
    assert "MTR-" not in render_facts(f)
