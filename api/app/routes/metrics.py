"""GET /v1/metrics and GET /v1/trace/{id} — observability for the console and eval."""

from fastapi import APIRouter, HTTPException

from app.obs import metrics, readiness, trace

router = APIRouter()


@router.get("/v1/metrics")
def get_metrics() -> dict:
    """Rolling window over the last 1,000 requests, plus what the service loaded at boot."""
    return {"readiness": readiness.state(), **metrics.snapshot()}


@router.get("/v1/traces")
def list_traces(limit: int = 20) -> dict:
    """Newest traces first, for the console's list view."""
    return {"traces": trace.recent(limit)}


@router.get("/v1/trace/{trace_id}")
def get_trace(trace_id: str) -> dict:
    """One full trace: stage timings, tokens, cost, cache tier."""
    found = trace.get(trace_id)
    if found is None:
        raise HTTPException(status_code=404, detail="trace not found or evicted from the window")
    return found
