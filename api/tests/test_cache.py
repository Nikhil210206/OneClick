"""Two-tier cache (component 2): repeat hits, paraphrase hits, and the near-miss guard."""

import time

import pytest

from app import cache
from app.config import settings
from app.models import CacheEntry
from app.pipeline.slots import extract_slots

PLAN = {"contexts": [{"goal": "Follow these steps to perform this Screen Troubleshooting"}]}
HASH = "aa71c2c2cb988681"

BLACK_SCREEN = "my galaxy s24 screen is completely black and won't turn on"
BLACK_VARIATIONS = [
    "the display on my galaxy s24 stays completely dark",
    "phone screen won't light up at all",
    "galaxy screen black no display",
    "why is my samsung screen totally blank??",
    "screen goes black and nothing happens when i press the power key",
]


def _store(query: str, variations: list[str], siis_hash: str | None = HASH) -> CacheEntry:
    entry = CacheEntry(
        key=cache.make_key(query, siis_hash),
        siis_hash=siis_hash,
        slots=extract_slots(query),
        plan=PLAN,
        query_texts=[query, *variations],
        created_at=time.time(),
    )
    cache.put(entry)
    return entry


@pytest.fixture(autouse=True)
def empty_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "cache.sqlite"))
    cache.clear()
    yield
    cache.clear()


def test_a_fresh_query_misses():
    assert cache.lookup(BLACK_SCREEN, extract_slots(BLACK_SCREEN), HASH) is None


def test_the_same_query_hits_tier_zero():
    _store(BLACK_SCREEN, BLACK_VARIATIONS)
    hit = cache.lookup(BLACK_SCREEN, extract_slots(BLACK_SCREEN), HASH)
    assert hit is not None
    assert hit.tier == "exact"
    assert hit.plan == PLAN


def test_a_repeat_hit_is_fast():
    """Block A3 wants p95 <= 300 ms on repeats; tier 0 should be well under 5 ms."""
    _store(BLACK_SCREEN, BLACK_VARIATIONS)
    slots = extract_slots(BLACK_SCREEN)
    start = time.perf_counter()
    for _ in range(20):
        cache.lookup(BLACK_SCREEN, slots, HASH)
    assert (time.perf_counter() - start) / 20 * 1000 < 5


@pytest.mark.parametrize("paraphrase", BLACK_VARIATIONS)
def test_a_stored_variation_hits_tier_one(paraphrase):
    _store(BLACK_SCREEN, BLACK_VARIATIONS)
    hit = cache.lookup(paraphrase, extract_slots(paraphrase), HASH)
    assert hit is not None and hit.tier == "semantic"


def test_an_unseen_paraphrase_can_still_hit():
    """Not one of the stored phrasings, but the same problem in other words."""
    _store(BLACK_SCREEN, BLACK_VARIATIONS)
    unseen = "my samsung galaxy s24 display is black and does not switch on"
    hit = cache.lookup(unseen, extract_slots(unseen), HASH)
    assert hit is not None and hit.tier == "semantic"


def test_a_different_symptom_on_the_same_part_is_blocked():
    """The false-hit case: embeddings rate these alike, the slot guard must refuse."""
    _store(BLACK_SCREEN, BLACK_VARIATIONS)
    near_miss = "my galaxy s24 screen is completely cracked and shattered"
    assert cache.lookup(near_miss, extract_slots(near_miss), HASH) is None


def test_a_different_article_is_blocked():
    """Same question, new article: the cached plan came from other source text."""
    _store(BLACK_SCREEN, BLACK_VARIATIONS)
    assert cache.lookup(BLACK_SCREEN, extract_slots(BLACK_SCREEN), "0000000000000000") is None


def test_entries_survive_a_restart():
    _store(BLACK_SCREEN, BLACK_VARIATIONS)
    assert cache.init() == 1
    hit = cache.lookup(BLACK_SCREEN, extract_slots(BLACK_SCREEN), HASH)
    assert hit is not None and hit.tier == "exact"


def test_a_prompt_change_invalidates_old_keys(monkeypatch):
    """Plans built by an older prompt must not be served after it changes."""
    _store(BLACK_SCREEN, BLACK_VARIATIONS)
    monkeypatch.setattr(settings, "prompt_version", "v2")
    assert cache.make_key(BLACK_SCREEN, HASH) not in [e.key for e in cache.store.entries().values()]
