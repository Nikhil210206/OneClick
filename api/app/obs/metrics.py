"""Rolling window (last 1,000 requests): p50/p95, hit rate, cost."""

import threading
import time
from collections import deque

from app.models import Trace

_WINDOW = 1000

# One record per served request. A deque so the window trims itself and nothing grows unbounded
# during the judging session.
_records: deque[dict] = deque(maxlen=_WINDOW)
_lock = threading.Lock()


def record(trace: Trace, latency_ms: float, fallback: str | None = None) -> None:
    """Called once per served request, on the cache path as well as the cold path."""
    with _lock:
        _records.append(
            {
                "at": time.time(),
                "latency_ms": latency_ms,
                "cache_tier": trace.cache_tier,
                "cost_usd": trace.cost_usd,
                "model": trace.model,
                "fallback": fallback or trace.fallback,
            }
        )


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; the scorer reports p50 and p95 the same way."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 1)


def _latencies(records: list[dict], tier: str | None) -> list[float]:
    return [r["latency_ms"] for r in records if r["cache_tier"] == tier]


def snapshot() -> dict:
    """What /v1/metrics serves: the numbers block A3 is scored on, plus cost."""
    with _lock:
        records = list(_records)
    if not records:
        return {"requests": 0}

    hits = [r for r in records if r["cache_tier"]]
    everything = [r["latency_ms"] for r in records]
    return {
        "requests": len(records),
        "cache_hit_rate": round(len(hits) / len(records), 3),
        "by_tier": {
            "exact": len(_latencies(records, "exact")),
            "semantic": len(_latencies(records, "semantic")),
            "miss": len(_latencies(records, None)),
        },
        "latency_ms": {
            "p50": _percentile(everything, 0.50),
            "p95": _percentile(everything, 0.95),
            "hit_p95": _percentile([r["latency_ms"] for r in hits], 0.95),
            "cold_p95": _percentile(_latencies(records, None), 0.95),
        },
        "cost_usd": {
            "total": round(sum(r["cost_usd"] for r in records), 4),
            "per_request": round(sum(r["cost_usd"] for r in records) / len(records), 6),
        },
        "fallbacks": {
            name: sum(1 for r in records if r["fallback"] == name) for name in ("no_match", "no_siis_context")
        },
    }


def reset() -> None:
    """Empty the window. Used by tests and before a measured run."""
    with _lock:
        _records.clear()
