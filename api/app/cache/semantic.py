"""Tier 1: embed normalized RAW query, ANN over stored original + variations. No LLM on this path."""

import numpy as np

from app.cache import store
from app.cache.slot_guard import compatible
from app.config import settings
from app.models import CacheEntry, Slots
from app.retrieval import dense

# One row per stored phrasing; _keys[i] says which cache entry row i belongs to.
_keys: list[str] = []
_matrix: np.ndarray | None = None


def index(entry: CacheEntry) -> None:
    """Add a solved plan's phrasings to the vector index.

    Every variation points at the same plan, so a paraphrase of a solved query hits without
    re-running the pipeline. That is the whole of the paraphrase hit rate in block A3.
    """
    global _matrix
    texts = [text for text in entry.query_texts if text.strip()]
    if not texts:
        return
    vectors = np.asarray(dense.embed(texts), dtype=np.float32)
    _matrix = vectors if _matrix is None else np.vstack([_matrix, vectors])
    _keys.extend([entry.key] * len(texts))


def rebuild() -> None:
    """Re-embed everything in the store. Called at startup after the snapshot is loaded."""
    global _keys, _matrix
    _keys, _matrix = [], None
    for entry in store.entries().values():
        index(entry)


def lookup(norm_query: str, slots: Slots, siis_hash: str | None) -> dict | None:
    """The plan of the closest stored phrasing, when it is close enough and does not contradict.

    Three conditions, all required: cosine >= threshold, the lexicon slots agree, and the
    article hash matches. The last one stops the same question with a different article from
    being served a stale plan.
    """
    if _matrix is None or not _keys:
        return None

    query_vector = np.asarray(dense.embed([norm_query])[0], dtype=np.float32)
    similarities = _matrix @ query_vector  # rows are normalized, so this is cosine similarity

    entries = store.entries()
    for position in np.argsort(-similarities):
        score = float(similarities[position])
        if score < settings.cache_sim_threshold:
            return None  # sorted, so nothing further can qualify
        entry = entries.get(_keys[position])
        if entry is None:
            continue
        if entry.siis_hash != siis_hash:
            continue
        if not compatible(slots, entry.slots):
            continue
        store.record_hit(entry.key)
        return entry.plan
    return None


def best_match(norm_query: str) -> tuple[str | None, float]:
    """Closest stored phrasing and its score, ignoring the guards. For /v1/metrics and debugging."""
    if _matrix is None or not _keys:
        return None, 0.0
    query_vector = np.asarray(dense.embed([norm_query])[0], dtype=np.float32)
    similarities = _matrix @ query_vector
    position = int(np.argmax(similarities))
    return _keys[position], float(similarities[position])
