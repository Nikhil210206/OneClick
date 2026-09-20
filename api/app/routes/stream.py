"""POST /v1/troubleshoot/stream — SSE per stage for the console (ADR-008). Not scored.

One frame per pipeline stage, then ``done``:

    event: enrich
    data: {"stage":"enrich","ms":812.4,"summary":"...","detail":{...}}

``done.detail`` is the full response body (contexts + meta); ``error`` ends the stream early. While the
pipeline is a skeleton (settings.stream_mock) frames are replayed from data/fixtures/ and marked as mock:
response header ``X-Mock: true`` and ``detail.mock`` on every frame. Flip settings.stream_mock to False
once app.pipeline.run.run_stream exists and the route serves the real pipeline unchanged.
"""

import json
import re
import time
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.config import settings
from app.models import ResponseMeta, StageEvent, StageName
from app.pipeline import run as pipeline_run
from app.routes.troubleshoot import TroubleshootRequest

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _frame(ev: StageEvent) -> str:
    payload = json.dumps(ev.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    return f"event: {ev.stage.value}\ndata: {payload}\n\n"


# ---- mock replay (fixtures) -------------------------------------------------------------------------
def _fixtures_dir() -> Path:
    if settings.stream_fixtures_dir:
        return Path(settings.stream_fixtures_dir)
    return Path(__file__).resolve().parents[3] / "data" / "fixtures"


@lru_cache(maxsize=32)
def _read(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _norm(text: str) -> str:
    """Mock-only stand-in for pipeline.normalize: drop list numbering and quotes, lowercase."""
    text = re.sub(r"^\d+\.\s*", "", text.strip()).strip('"“” ')
    return re.sub(r"\s+", " ", text).lower()


def _find_scenario(req: TroubleshootRequest, fixtures: Path) -> dict | None:
    scenarios = _read(str(fixtures / "scenarios.json"))["scenarios"]
    nq = _norm(req.query)
    for sc in scenarios:
        if _norm(sc["query"]) == nq:
            return sc
    title = req.siis_response.get("title") if isinstance(req.siis_response, dict) else None
    if title:
        for sc in scenarios:
            if sc["siis_title"] == title:
                return sc
    return None


def _mark_mock(ev: StageEvent) -> StageEvent:
    detail = {**ev.detail, "mock": True}
    if ev.stage is StageName.done and isinstance(detail.get("meta"), dict):
        detail["meta"] = {**detail["meta"], "mock": True}
    return ev.model_copy(update={"detail": detail})


def _no_scenario(req: TroubleshootRequest) -> list[StageEvent]:
    """Honest empty answer: the mock never invents a plan for a query it has no fixture for."""
    fallback = "no_siis_context" if req.siis_response is None else "no_match"
    meta = ResponseMeta(trace_id="t_mock_none", fallback=fallback).model_dump(mode="json")
    return [
        StageEvent(
            stage=StageName.cache,
            summary="Mock: no fixture scenario matches this query",
            detail={"hit": False, "tier": None},
        ),
        StageEvent(
            stage=StageName.done,
            summary="Empty plan (mock has no scenario for this query)",
            detail={"contexts": [], "meta": meta},
        ),
    ]


def _mock_events(req: TroubleshootRequest, mode: str) -> tuple[list[StageEvent], str]:
    """Return (events, mode actually served). Hit modes need the scenario's cache_events.json."""
    fixtures = _fixtures_dir()
    sc = _find_scenario(req, fixtures)
    if sc is None:
        return [_mark_mock(e) for e in _no_scenario(req)], "no_scenario"
    folder = fixtures / sc["dir"]
    cold = [StageEvent.model_validate(e) for e in _read(str(folder / "stream.json"))]
    if mode in ("exact", "semantic"):
        hit = _read(str(folder / "cache_events.json"))[mode]
        contexts = cold[-1].detail["contexts"]
        meta = hit["meta"]
        done = StageEvent(
            stage=StageName.done,
            ms=meta["latency_ms"],
            detail={"contexts": contexts, "meta": meta},
            summary=f"{len(contexts)} goal(s) from the {mode} cache in {meta['latency_ms']} ms",
        )
        events = [StageEvent.model_validate(hit["event"]), done]
    else:
        events = cold
    return [_mark_mock(e) for e in events], mode


def _replay(events: list[StageEvent]) -> Iterator[str]:
    scale = settings.stream_mock_time_scale
    for ev in events:
        if scale > 0 and ev.stage not in (StageName.done, StageName.error):
            time.sleep(ev.ms / 1000 * scale)  # each frame fires when its stage finishes
        yield _frame(ev)


# ---- live pipeline ----------------------------------------------------------------------------------
def _live(req: TroubleshootRequest) -> Iterator[str]:
    try:
        for ev in pipeline_run.run_stream(req.query, req.siis_response):
            yield _frame(ev)
    except Exception as exc:  # noqa: BLE001 - the console has an Error state; never leave the stream hanging
        summary = (
            "pipeline.run_stream is not implemented yet"
            if isinstance(exc, NotImplementedError)
            else f"Pipeline failed: {type(exc).__name__}"
        )
        yield _frame(StageEvent(stage=StageName.error, summary=summary, detail={"error": type(exc).__name__}))


@router.post("/v1/troubleshoot/stream")
def troubleshoot_stream(
    req: TroubleshootRequest,
    mock: str = Query(
        "cold",
        pattern="^(cold|exact|semantic)$",
        description="Mock mode only: replay a cold run or a cache hit",
    ),
) -> StreamingResponse:
    headers = dict(_SSE_HEADERS)
    if settings.stream_mock:
        headers["X-Mock"] = "true"
        try:
            events, served = _mock_events(req, mock)
            headers["X-Mock-Mode"] = served
            body = _replay(events)
        except (OSError, ValueError, KeyError) as exc:
            headers["X-Mock-Mode"] = "unavailable"
            body = iter(
                [
                    _frame(
                        StageEvent(
                            stage=StageName.error,
                            summary="Mock fixtures could not be loaded (data/fixtures is not in the "
                            "Docker image; run the API from the repo or set settings.stream_fixtures_dir)",
                            detail={"error": type(exc).__name__, "fixtures_dir": str(_fixtures_dir())},
                        )
                    )
                ]
            )
    else:
        body = _live(req)
    return StreamingResponse(body, media_type="text/event-stream", headers=headers)
