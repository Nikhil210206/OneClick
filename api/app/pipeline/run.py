"""Orchestrator: normalize -> cache -> enrich -> segment -> extract -> ground -> resolve -> order -> compile."""

from collections.abc import Iterator

from app.models import StageEvent


def run(query: str, siis: dict | str | None) -> dict:
    raise NotImplementedError


def run_stream(query: str, siis: dict | str | None) -> Iterator[StageEvent]:
    """Same pipeline as run(), yielding one StageEvent as each stage finishes.

    Order: cache -> enrich -> segment -> extract -> ground -> resolve -> compile -> done. On a cache hit
    yield cache then done. `done.detail` is the full response body (contexts + meta). Detail shapes per
    stage are in data/fixtures/README.md. Set settings.stream_mock = False once this exists.
    """
    raise NotImplementedError
