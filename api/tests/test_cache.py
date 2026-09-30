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


def test_a_configuration_request_is_not_served_the_fault_plan():
    """eval/sets/near_miss.jsonl 'intent' cases: same words, but the user wants the behaviour."""
    _store(BLACK_SCREEN, BLACK_VARIATIONS)
    wish = "i want my galaxy s24 screen to stay completely black while the phone is on"
    assert cache.lookup(wish, extract_slots(wish), HASH) is None


def test_a_semantic_hit_reports_the_entry_it_served():
    entry = _store(BLACK_SCREEN, BLACK_VARIATIONS)
    hit = cache.lookup(BLACK_VARIATIONS[0], extract_slots(BLACK_VARIATIONS[0]), HASH)
    assert hit is not None and hit.key == entry.key and 0 < hit.similarity <= 1


def test_indexing_while_looking_up_is_safe():
    """The engine writes variations from a background thread while requests read the index."""
    import threading

    _store(BLACK_SCREEN, BLACK_VARIATIONS)
    errors: list[Exception] = []

    def writer():
        try:
            for index in range(15):
                _store(f"battery drains fast on phone number {index}", [f"battery dies {index}"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def reader():
        try:
            for _ in range(30):
                cache.lookup(BLACK_VARIATIONS[1], extract_slots(BLACK_VARIATIONS[1]), HASH)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors


def test_reputting_an_entry_does_not_index_its_texts_twice():
    """The engine puts an entry with the query, then again when variations land."""
    from app.cache import semantic

    entry = _store(BLACK_SCREEN, [])
    rows_before = len(semantic._keys)
    entry.query_texts = [BLACK_SCREEN, *BLACK_VARIATIONS]
    cache.put(entry)
    assert len(semantic._keys) == rows_before + len(BLACK_VARIATIONS)


def test_intent_survives_a_restart():
    _store(BLACK_SCREEN, BLACK_VARIATIONS)
    cache.init()
    stored = next(iter(cache.store.entries().values()))
    assert stored.slots.intent == "fault"


def test_an_old_snapshot_without_the_intent_column_still_loads(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE cache_entries (key TEXT PRIMARY KEY, siis_hash TEXT, component TEXT,"
            " symptom TEXT, plan TEXT NOT NULL, query_texts TEXT NOT NULL, created_at REAL NOT NULL,"
            " hits INTEGER NOT NULL DEFAULT 0)"
        )
        connection.execute(
            "INSERT INTO cache_entries VALUES ('k1', 'h', 'screen', 'black', '{}', '[\"q\"]', 1.0, 0)"
        )
    monkeypatch.setattr(settings, "sqlite_path", str(path))
    assert cache.init() == 1
    assert cache.store.entries()["k1"].slots.intent is None


def test_a_plan_from_other_prompts_is_not_served_by_meaning(monkeypatch):
    """A cache.sqlite kept across a prompt bump: the exact key changes with the version, and the semantic
    tier must not hand the old plan back at similarity 1.0 either."""
    entry = _store(BLACK_SCREEN, BLACK_VARIATIONS)
    cache.put(entry.model_copy(update={"plan": {**PLAN, "prompt_version": settings.prompt_version}}))
    paraphrase = BLACK_VARIATIONS[0]
    assert cache.lookup(paraphrase, extract_slots(paraphrase), HASH) is not None
    monkeypatch.setattr(settings, "prompt_version", "a-newer-prompt")
    assert cache.lookup(BLACK_SCREEN, extract_slots(BLACK_SCREEN), HASH) is None
    assert cache.lookup(paraphrase, extract_slots(paraphrase), HASH) is None


def test_a_request_for_the_opposite_setting_is_not_served_the_stored_plan():
    """Near miss nm_12_3: "I want to add a floating circle" shares every slot (screen, configure) with the
    stored "...I want to remove it", and was served its plan. The add/remove direction now blocks it; a
    paraphrase that names no direction still hits."""
    remove = (
        "my galaxy s25 has a floating circle that hovers on my screen with shortcuts to recent apps, home "
        "and back; i want to remove it"
    )
    _store(remove, ["i want to turn off the floating shortcut circle on my galaxy s25 screen"])
    add = (
        "i want to add a floating circle to my galaxy s25 screen with shortcuts to recent apps, home and back"
    )
    assert cache.lookup(add, extract_slots(add), HASH) is None
    same = "my galaxy s25 screen has a floating circle with shortcuts to recent apps, home and back, i want it gone"
    assert cache.lookup(same, extract_slots(same), HASH) is not None
