"""POST /v1/troubleshoot/stream: SSE framing, mock replay from fixtures, and the live-pipeline fallback."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schema import ContextDeeplinkResponse

client = TestClient(app)
FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"
SCENARIOS = json.loads((FIXTURES / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]
STAGES = ["cache", "enrich", "segment", "extract", "ground", "resolve", "compile", "done"]


@pytest.fixture(autouse=True)
def fast_mock(monkeypatch):
    monkeypatch.setattr(settings, "stream_mock", True)
    monkeypatch.setattr(settings, "stream_mock_time_scale", 0.0)
    monkeypatch.setattr(settings, "stream_fixtures_dir", None)


def _request(name: str) -> dict:
    return json.loads((FIXTURES / name / "request.json").read_text(encoding="utf-8"))


def _frames(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        head, data = block.split("\n")
        assert head.startswith("event: ") and data.startswith("data: ")
        out.append((head.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
def test_cold_run_streams_every_stage_and_ends_with_the_plan(sc):
    r = client.post("/v1/troubleshoot/stream", json=_request(sc["name"]))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-mock"] == "true" and r.headers["x-mock-mode"] == "cold"
    frames = _frames(r.text)
    assert [name for name, _ in frames] == STAGES
    for name, ev in frames:
        assert ev["stage"] == name and {"stage", "ms", "summary", "detail"} == set(ev)
        assert ev["detail"]["mock"] is True
    done = frames[-1][1]["detail"]
    ContextDeeplinkResponse.model_validate(done)
    assert done["meta"]["mock"] is True and len(done["contexts"]) == sc["goals"]


def test_scenario_matches_kit_numbering_and_quotes():
    req = _request("touch_lag")
    req["query"] = f'1. "{req["query"]}"'
    r = client.post("/v1/troubleshoot/stream", json=req)
    assert _frames(r.text)[-1][1]["detail"]["meta"]["trace_id"] == "t_mock_touch_lag"


def test_unknown_query_with_a_known_article_falls_back_to_the_article_scenario():
    req = _request("email_not_responding")
    req["query"] = "something the fixtures have never seen"
    r = client.post("/v1/troubleshoot/stream", json=req)
    assert _frames(r.text)[-1][1]["detail"]["meta"]["trace_id"] == "t_mock_email_not_responding"


@pytest.mark.parametrize("mode", ["exact", "semantic"])
def test_cache_hit_modes_jump_from_cache_to_done(mode):
    cold = _frames(client.post("/v1/troubleshoot/stream", json=_request("touch_lag")).text)[-1][1]["detail"]
    r = client.post(f"/v1/troubleshoot/stream?mock={mode}", json=_request("touch_lag"))
    frames = _frames(r.text)
    assert [n for n, _ in frames] == ["cache", "done"] and r.headers["x-mock-mode"] == mode
    assert frames[0][1]["detail"]["tier"] == mode
    hit = frames[1][1]["detail"]
    assert hit["contexts"] == cold["contexts"]
    assert (
        hit["meta"]["cache_hit"] is True
        and hit["meta"]["cache_tier"] == mode
        and hit["meta"]["cost_usd"] == 0.0
    )


def test_invalid_mock_mode_is_rejected():
    assert client.post("/v1/troubleshoot/stream?mock=bogus", json=_request("touch_lag")).status_code == 422


@pytest.mark.parametrize(
    "siis,fallback", [({"title": "x", "content": "y"}, "no_match"), (None, "no_siis_context")]
)
def test_query_without_a_fixture_gets_an_honest_empty_plan(siis, fallback):
    r = client.post(
        "/v1/troubleshoot/stream", json={"query": "battery drains overnight", "siis_response": siis}
    )
    frames = _frames(r.text)
    assert [n for n, _ in frames] == ["cache", "done"] and r.headers["x-mock-mode"] == "no_scenario"
    body = frames[-1][1]["detail"]
    assert body["contexts"] == [] and body["meta"]["fallback"] == fallback
    ContextDeeplinkResponse.model_validate(body)


def test_frames_never_break_sse_framing():
    text = client.post("/v1/troubleshoot/stream", json=_request("touch_lag")).text
    for block in text.strip().split("\n\n"):
        assert block.count("\n") == 1, "a frame is exactly one event line and one data line"


def test_missing_fixtures_directory_yields_an_error_frame(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "stream_fixtures_dir", str(tmp_path))
    r = client.post("/v1/troubleshoot/stream", json=_request("touch_lag"))
    assert r.status_code == 200 and r.headers["x-mock-mode"] == "unavailable"
    ((name, ev),) = _frames(r.text)
    assert name == "error" and ev["stage"] == "error"


def test_live_mode_reports_the_unimplemented_pipeline_as_an_error_frame(monkeypatch):
    monkeypatch.setattr(settings, "stream_mock", False)
    r = client.post("/v1/troubleshoot/stream", json=_request("touch_lag"))
    assert r.status_code == 200 and "x-mock" not in r.headers
    ((name, ev),) = _frames(r.text)
    assert name == "error" and "not implemented" in ev["summary"]
