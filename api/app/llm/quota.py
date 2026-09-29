"""ADR-002: what each model has left of its free-plan limits, so the router skips a rung instead of
spending a call on a 429.

Two sources:
  ledger   this process's requests and tokens per model over the last minute. A call reserves an
           estimate when it starts, so calls B and C see each other, and settles it with the real
           usage when it ends.
  headers  what the provider said was left in its last answer (settings.llm_providers names the
           headers). They also count requests from other processes on the same key, e.g. a bake-off
           running next to the API.
"""

import re
import threading
import time

from app.config import settings

_WINDOW_S = 60.0  # the free plans' limits are per minute; a daily limit reaches us through the headers
_ledger: dict[str, list[list[float]]] = {}  # model -> [[started, tokens], ...]
# model -> (when the headers were read, requests left, tokens left, requests reset at, tokens reset at)
_snapshots: dict[str, tuple[float, int | None, int | None, float, float]] = {}
_lock = threading.Lock()
_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def reserve(model: str, tokens: float) -> list[float]:
    """Count one request for `model` now, holding `tokens` until settle() replaces the estimate."""
    entry = [time.monotonic(), float(tokens)]
    with _lock:
        _prune(model, entry[0])
        _ledger.setdefault(model, []).append(entry)
    return entry


def settle(entry: list[float], tokens: float) -> None:
    with _lock:
        entry[1] = float(tokens)


def calls_last_minute(model: str) -> int:
    now = time.monotonic()
    with _lock:
        return sum(1 for started, _ in _ledger.get(model, []) if now - started < _WINDOW_S)


def tokens_last_minute(model: str) -> float:
    now = time.monotonic()
    with _lock:
        return sum(tokens for started, tokens in _ledger.get(model, []) if now - started < _WINDOW_S)


def note_headers(model: str, ratelimit: dict | None) -> None:
    """Remember what the provider said was left: remaining requests / tokens and when each resets."""
    if not ratelimit:
        return
    requests, tokens = ratelimit.get("remaining_requests"), ratelimit.get("remaining_tokens")
    if requests is None and tokens is None:
        return
    now = time.monotonic()
    # Without a reset header (Mistral) the answer holds for llm_cooldown_s: its limit refills like a
    # bucket, and a full minute kept 14B away long after it had capacity again (measured 2026-09-27).
    requests_reset = now + (ratelimit.get("reset_requests_s") or settings.llm_cooldown_s)
    tokens_reset = now + (ratelimit.get("reset_tokens_s") or settings.llm_cooldown_s)
    with _lock:
        _snapshots[model] = (now, requests, tokens, requests_reset, tokens_reset)


def over(model: str, estimate: float, *, keep_requests: int = 0) -> bool:
    """True when a call to `model` needing about `estimate` tokens would pass one of its limits.

    `keep_requests` leaves that many of the model's per-minute requests to other stages (coverage
    leaves the primary to call B, settings.coverage_leave_primary).
    """
    now = time.monotonic()
    with _lock:
        entries = [e for e in _ledger.get(model, []) if now - e[0] < _WINDOW_S]
        snapshot = _snapshots.get(model)
    rpm = settings.llm_requests_per_minute.get(model)
    if rpm and len(entries) >= rpm - keep_requests:
        return True
    tpm = settings.llm_tokens_per_minute.get(model)
    if tpm and sum(tokens for _, tokens in entries) + estimate > tpm:
        return True
    if snapshot is not None:
        read_at, requests, tokens, requests_reset, tokens_reset = snapshot
        since = [tokens_ for started, tokens_ in entries if started > read_at]
        if requests is not None and now < requests_reset and requests - len(since) <= 0:
            return True
        if tokens is not None and now < tokens_reset and tokens - sum(since) < estimate:
            return True
    return False


def parse_reset(value: str | None) -> float | None:
    """Seconds from a reset header: "7.66s", "2m59.56s", "1h2m3s", "250ms" or a bare number."""
    if not value:
        return None
    text = value.strip().lower()
    try:
        return float(text)
    except ValueError:
        pass
    units = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    parts = _DURATION.findall(text)
    return sum(float(n) * units[u] for n, u in parts) if parts else None


def reset() -> None:
    with _lock:
        _ledger.clear()
        _snapshots.clear()


def _prune(model: str, now: float) -> None:
    kept = [e for e in _ledger.get(model, []) if now - e[0] < _WINDOW_S]
    _ledger[model] = kept
