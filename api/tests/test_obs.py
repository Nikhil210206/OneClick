"""Metrics window, traces and the observability endpoints (component: obs)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Trace
from app.obs import metrics, trace


@pytest.fixture(autouse=True)
def clean():
    metrics.reset()
    trace.clear()
    yield
    metrics.reset()
    trace.clear()


def _trace(tier: str | None = None, cost: float = 0.0) -> Trace:
    record = trace.new_trace()
    record.cache_tier = tier
    record.cost_usd = cost
    return record


def test_snapshot_is_empty_before_any_request():
    assert metrics.snapshot() == {"requests": 0}


def test_percentiles_and_hit_rate():
    metrics.record(_trace("exact"), latency_ms=2.0)
    metrics.record(_trace("semantic"), latency_ms=20.0)
    metrics.record(_trace(None, cost=0.001), latency_ms=3000.0)

    snapshot = metrics.snapshot()
    assert snapshot["requests"] == 3
    assert snapshot["cache_hit_rate"] == pytest.approx(2 / 3, abs=0.01)
    assert snapshot["by_tier"] == {"exact": 1, "semantic": 1, "miss": 1}
    assert snapshot["latency_ms"]["hit_p95"] == 20.0
    assert snapshot["latency_ms"]["cold_p95"] == 3000.0
    assert snapshot["cost_usd"]["total"] == pytest.approx(0.001)


def test_the_window_keeps_only_the_last_thousand():
    for index in range(1100):
        metrics.record(_trace("exact"), latency_ms=float(index))
    assert metrics.snapshot()["requests"] == 1000


def test_a_stored_trace_comes_back_by_id():
    record = _trace("exact")
    trace.put(record, {"query": "screen is black"})
    stored = trace.get(record.trace_id)
    assert stored["query"] == "screen is black"
    assert stored["cache_tier"] == "exact"


def test_traces_fall_out_of_the_window():
    first = _trace()
    trace.put(first)
    for _ in range(200):
        trace.put(_trace())
    assert trace.get(first.trace_id) is None


def test_endpoints_serve_the_window():
    with TestClient(app) as client:
        metrics.record(_trace("exact"), latency_ms=2.0)
        record = _trace("semantic")
        trace.put(record)

        body = client.get("/v1/metrics").json()
        assert body["requests"] == 1 and body["readiness"]["ready"] is True

        assert client.get("/v1/traces").json()["traces"][0]["trace_id"] == record.trace_id
        assert client.get(f"/v1/trace/{record.trace_id}").json()["cache_tier"] == "semantic"
        assert client.get("/v1/trace/t_missing").status_code == 404
