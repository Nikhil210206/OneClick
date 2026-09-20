"""Two-tier semantic cache (component 2).

    Tier 0  exact key    norm_query + siis_hash + prompt version   ~1 ms
    Tier 1  semantic     cosine over every stored phrasing         ~20 ms
            guarded by   slots agree  AND  same article hash

The SIIS cache ships empty: the scorer's first call has to be genuinely cold. Only the no-SIIS
lookup table (cache/lookup.py) is pre-warmed from the kit.
"""

from app.cache import exact, semantic, store
from app.models import CacheEntry, Slots

__all__ = ["CacheHit", "clear", "init", "lookup", "make_key", "put"]

make_key = exact.make_key


class CacheHit:
    """What the lookup found: the plan plus the detail the stream event and metrics want."""

    def __init__(self, plan: dict, tier: str, key: str, similarity: float = 1.0):
        self.plan = plan
        self.tier = tier  # exact | semantic
        self.key = key
        self.similarity = similarity

    def as_detail(self) -> dict:
        return {"hit": True, "tier": self.tier, "similarity": self.similarity, "key": self.key}


def init() -> int:
    """Load the snapshot and rebuild the vector index. Returns the number of entries."""
    count = store.load()
    semantic.rebuild()
    return count


def lookup(norm_query: str, slots: Slots, siis_hash: str | None) -> CacheHit | None:
    """Tier 0, then tier 1. None means the pipeline has to run."""
    key = exact.make_key(norm_query, siis_hash)
    plan = exact.get(key)
    if plan is not None:
        return CacheHit(plan, tier="exact", key=key, similarity=1.0)

    plan = semantic.lookup(norm_query, slots, siis_hash)
    if plan is not None:
        matched_key, similarity = semantic.best_match(norm_query)
        return CacheHit(plan, tier="semantic", key=matched_key or key, similarity=round(similarity, 3))
    return None


def put(entry: CacheEntry) -> None:
    """Store a solved plan and index all of its phrasings."""
    store.put(entry)
    semantic.index(entry)


def clear() -> None:
    """Empty both tiers, in memory and on disk."""
    store.clear()
    semantic.rebuild()
