"""POST /v1/troubleshoot — the scored endpoint. Always 200 with a schema-valid body."""

import time

from fastapi import APIRouter, Response
from pydantic import BaseModel, field_validator

from app.config import settings
from app.pipeline import run as pipeline_run
from app.pipeline.normalize import coerce_query, coerce_siis

router = APIRouter()


class TroubleshootRequest(BaseModel):
    """Lenient on purpose (hard rule 4): a missing or null query, a number, a list of articles are all
    read as best they can be instead of drawing a 422. A body that is not a JSON object at all is
    answered by the handler in main.py."""

    query: str = ""
    siis_response: dict | str | None = None

    @field_validator("query", mode="before")
    @classmethod
    def _query(cls, value: object) -> str:
        return coerce_query(value, settings.query_max_chars)

    @field_validator("siis_response", mode="before")
    @classmethod
    def _siis(cls, value: object) -> dict | str | None:
        return coerce_siis(value)


@router.post("/v1/troubleshoot")
def troubleshoot(req: TroubleshootRequest, response: Response) -> dict:
    start = time.perf_counter()
    try:
        body = pipeline_run.run(req.query, req.siis_response)
    except Exception:  # noqa: BLE001 - run() never raises; this is the last line of hard rule 4
        body = pipeline_run.empty_body(req.siis_response)
    meta = body.get("meta") or {}
    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    response.headers["X-Latency-Ms"] = str(latency_ms)
    response.headers["X-Cache-Hit"] = "true" if meta.get("cache_hit") else "false"
    response.headers["X-Cache-Tier"] = meta.get("cache_tier") or "none"
    response.headers["X-Cost-Usd"] = f"{float(meta.get('cost_usd') or 0.0):.6f}"
    out = {"contexts": body.get("contexts", [])}
    if settings.include_meta:
        out["meta"] = {**meta, "latency_ms": latency_ms}
    return out
