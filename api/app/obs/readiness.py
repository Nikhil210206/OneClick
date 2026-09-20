"""Startup warm-up and readiness state behind /health (ADR-007).

The scorer's first call must not land on a half-loaded service: /health returns 503 until the
catalog, the Screen Graph, the vector index and the cache snapshot are in memory.
"""

import time
from pathlib import Path

from app import cache, retrieval
from app.config import settings
from app.retrieval import dense
from app.screengraph import load as load_graph

DATA = Path(settings.data_dir)
CATALOG = DATA / "kit" / "deeplinks.json"
GRAPH = DATA / "build" / "screengraph.json"
VECTORS = DATA / "build" / "catalog_vectors.npz"

_state: dict = {
    "ready": False,
    "screen_graph": 0,
    "catalog_entries": 0,
    "cache_entries": 0,
    "boot_ms": 0.0,
    "error": None,
}


def warm() -> dict:
    """Load everything the request path needs. Safe to call twice."""
    start = time.perf_counter()
    try:
        nodes = load_graph(CATALOG, GRAPH if GRAPH.exists() else None)
        docs = retrieval.load_catalog_docs(CATALOG)
        retrieval.build(docs, vectors_path=VECTORS if VECTORS.exists() else None)
        # The ONNX model loads lazily; embed once here so the first real query does not pay for it.
        dense.embed(["warm up the embedding model"])
        cached = cache.init()
        _state.update(
            ready=True,
            screen_graph=len(nodes),
            catalog_entries=len(docs),
            cache_entries=cached,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - reported through /health, never raised at boot
        _state.update(ready=False, error=f"{type(exc).__name__}: {exc}")
    _state["boot_ms"] = round((time.perf_counter() - start) * 1000, 1)
    return dict(_state)


def ensure() -> bool:
    """Warm once if startup never did (some ASGI mounts and test clients skip the lifespan)."""
    if not _state["ready"] and not _state["boot_ms"]:
        warm()
    return is_ready()


def is_ready() -> bool:
    return bool(_state["ready"])


def state() -> dict:
    return dict(_state)
