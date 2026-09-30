"""Tier 1: embed normalized RAW query, ANN over stored original + variations. No LLM on this path."""

import threading

import numpy as np

from app.cache import store
from app.cache.slot_guard import compatible, same_direction
from app.config import settings
from app.models import CacheEntry, Slots
from app.pipeline.spell import correct_typos
from app.retrieval import dense

# One row per stored phrasing; _keys[i] says which cache entry row i belongs to.
# The engine writes variations from a background thread, so index() can run while lookup() reads:
# both take _index_lock, and lookup() reads the pair under it before doing the maths.
_keys: list[str] = []
_matrix: np.ndarray | None = None
_index_lock = threading.Lock()
# key -> phrasings already in the matrix. The engine puts an entry once with the query and again
# when its variations land; without this the query was embedded and indexed a second time.
_indexed: dict[str, set[str]] = {}


def index(entry: CacheEntry) -> None:
    """Add a solved plan's phrasings to the vector index.

    Every variation points at the same plan, so a paraphrase of a solved query hits without
    re-running the pipeline. That is the whole of the paraphrase hit rate in block A3.
    """
    global _matrix
    with _index_lock:
        seen = _indexed.get(entry.key, set())
    texts = list(dict.fromkeys(t for t in entry.query_texts if t.strip() and t not in seen))
    if not texts:
        return
    # Misspellings put right on both sides (pipeline/spell.py); _indexed keeps the texts as stored.
    vectors = np.asarray(dense.embed([correct_typos(t) for t in texts]), dtype=np.float32)  # the slow part
    with _index_lock:
        _matrix = vectors if _matrix is None else np.vstack([_matrix, vectors])
        _keys.extend([entry.key] * len(texts))
        _indexed.setdefault(entry.key, set()).update(texts)


def rebuild() -> None:
    """Re-embed everything in the store. Called at startup after the snapshot is loaded."""
    global _keys, _matrix, _indexed
    with _index_lock:
        _keys, _matrix, _indexed = [], None, {}
    for entry in store.entries().values():
        index(entry)


_ANY_ARTICLE = object()


def lookup_with_score(norm_query: str, slots: Slots, siis_hash: str | None) -> tuple[dict, str, float] | None:
    """The plan of the closest stored phrasing, when it is close enough and does not contradict.

    Three conditions, all required: cosine >= threshold, the lexicon slots agree, and the
    article hash matches. The last one stops the same question with a different article from
    being served a stale plan.
    """
    return _search(norm_query, slots, siis_hash, settings.cache_sim_threshold)


def lookup_any_article(norm_query: str, slots: Slots, threshold: float) -> tuple[dict, str, float] | None:
    """The same search for a request that carries no article: any cached plan may answer.

    Without the article hash guarding the match, the caller passes a stricter threshold
    (settings.no_siis_plan_threshold); the slot guard still applies.
    """
    return _search(norm_query, slots, _ANY_ARTICLE, threshold)


def _search(norm_query: str, slots: Slots, siis_hash, threshold: float) -> tuple[dict, str, float] | None:
    with _index_lock:  # one consistent snapshot; a concurrent index() may add rows after this
        matrix, keys = _matrix, list(_keys)
    if matrix is None or not keys:
        return None

    query_vector = np.asarray(dense.embed([correct_typos(norm_query)])[0], dtype=np.float32)
    similarities = matrix @ query_vector  # rows are normalized, so this is cosine similarity

    entries = store.entries()
    for position in np.argsort(-similarities):
        score = float(similarities[position])
        if score < threshold:
            return None  # sorted, so nothing further can qualify
        entry = entries.get(keys[position])
        if entry is None:
            continue
        if siis_hash is not _ANY_ARTICLE and entry.siis_hash != siis_hash:
            continue
        # A plan made by other prompts (a cache.sqlite kept across a prompt bump) is not served: the exact
        # tier's key carries the version, and without this check the same query hit its old plan here at
        # similarity 1.0. Plans stored before plans carried a version (tests, eval scripts) still match.
        version = (entry.plan or {}).get("prompt_version")
        if version is not None and version != settings.prompt_version:
            continue
        if not compatible(slots, entry.slots):  # component, intent, one-sided symptom
            continue
        if entry.query_texts and not same_direction(norm_query, entry.query_texts[0]):  # add vs remove
            continue
        store.record_hit(entry.key)
        return entry.plan, entry.key, score
    return None


def lookup(norm_query: str, slots: Slots, siis_hash: str | None) -> dict | None:
    """The plan alone, for callers that do not report the match."""
    found = lookup_with_score(norm_query, slots, siis_hash)
    return found[0] if found else None


def best_match(norm_query: str) -> tuple[str | None, float]:
    """Closest stored phrasing and its score, ignoring every guard.

    Debugging only. A lookup must use lookup_with_score(), which reports the entry it actually
    served: the nearest phrasing is often one the guards rejected.
    """
    with _index_lock:
        matrix, keys = _matrix, list(_keys)
    if matrix is None or not keys:
        return None, 0.0
    query_vector = np.asarray(dense.embed([correct_typos(norm_query)])[0], dtype=np.float32)
    similarities = matrix @ query_vector
    position = int(np.argmax(similarities))
    return keys[position], float(similarities[position])
