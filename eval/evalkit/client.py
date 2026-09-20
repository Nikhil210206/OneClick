"""HTTP client for the scored API, recording what the organisers' scorer would observe."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx


@dataclass
class CallResult:
    status_code: int | None
    client_ms: float
    server_ms: float | None = None
    cache_hit: bool | None = None
    cache_tier: str | None = None
    body: object = None
    pure_json: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and self.pure_json and isinstance(self.body, dict)

    @property
    def contexts(self) -> list:
        ctx = self.body.get("contexts") if isinstance(self.body, dict) else None
        return ctx if isinstance(ctx, list) else []


def _header_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() in {"true", "1", "yes", "hit"}


def _header_float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class ApiClient:
    def __init__(self, base_url: str, timeout_s: float = 30.0, http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        self.http.close()

    def _call(self, method: str, path: str, payload: dict | None = None) -> CallResult:
        start = time.perf_counter()
        try:
            r = self.http.request(method, self.base_url + path, json=payload)
        except httpx.HTTPError as e:
            return CallResult(None, (time.perf_counter() - start) * 1000, error=f"{type(e).__name__}: {e}")
        client_ms = (time.perf_counter() - start) * 1000
        result = CallResult(r.status_code, client_ms, server_ms=_header_float(r.headers.get("x-latency-ms")))
        try:
            result.body = json.loads(r.text)
            result.pure_json = "application/json" in r.headers.get("content-type", "")
        except json.JSONDecodeError:
            result.error = f"body is not JSON: {r.text[:120]!r}"
        meta = result.body.get("meta") if isinstance(result.body, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        result.cache_hit = _header_bool(r.headers.get("x-cache-hit"))
        if result.cache_hit is None and isinstance(meta.get("cache_hit"), bool):
            result.cache_hit = meta["cache_hit"]
        result.cache_tier = r.headers.get("x-cache-tier") or meta.get("cache_tier")
        return result

    def health(self) -> CallResult:
        return self._call("GET", "/health")

    def troubleshoot(self, query: str, siis: dict | str | None) -> CallResult:
        payload: dict = {"query": query}
        if siis is not None:
            payload["siis_response"] = siis
        return self._call("POST", "/v1/troubleshoot", payload)
