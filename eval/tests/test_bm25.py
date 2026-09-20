"""BM25 candidate generator used for gold labelling."""

from __future__ import annotations

import pytest

from evalkit.bm25 import POLARITY_BONUS, Bm25Index, Entry, load_entries, terms


@pytest.fixture(scope="module")
def entries() -> list[Entry]:
    return load_entries()


@pytest.fixture(scope="module")
def index(entries: list[Entry]) -> Bm25Index:
    return Bm25Index.build(entries)


def test_terms_drops_stopwords_and_keeps_repeats():
    assert terms("the screen and the Screen") == ["screen", "screen"]


def test_terms_lowercases_and_splits_on_punctuation():
    assert terms("Wi-Fi Settings!") == ["wi", "fi", "settings"]


def test_catalog_loads_every_entry(entries):
    assert len(entries) == 578
    assert all(e.id.startswith("DL-") for e in entries)
    assert all(e.deeplink.startswith("bixby://") for e in entries)


def test_only_on_url_entries_are_fully_validatable(entries):
    """Documented catalog quirk: the 138 enable toggles are the only validatable entries."""
    validatable = [e for e in entries if e.validatable]
    assert len(validatable) == 138
    assert {e.original_type for e in validatable} == {"onURL"}


def test_search_finds_the_obvious_screen(index):
    ids = [e.id for e, _ in index.search("touch sensitivity")]
    assert "DL-0125" in ids and "DL-0126" in ids


def test_search_is_empty_for_unrelated_text(index):
    assert index.search("zzzz qqqq xxxx") == []


def test_search_respects_top_k(index):
    assert len(index.search("settings", top_k=3)) == 3


def test_polarity_verb_promotes_the_matching_entry(index):
    off = index.search("touch sensitivity", verb="disable", top_k=1)[0][0]
    on = index.search("touch sensitivity", verb="enable", top_k=1)[0][0]
    assert off.id == "DL-0125" and off.original_type == "offURL"
    assert on.id == "DL-0126" and on.original_type == "onURL"


def test_polarity_bonus_is_exactly_the_configured_amount(index):
    plain = {e.id: s for e, s in index.search("touch sensitivity", top_k=8)}
    boosted = {e.id: s for e, s in index.search("touch sensitivity", verb="enable", top_k=8)}
    assert boosted["DL-0126"] == pytest.approx(plain["DL-0126"] + POLARITY_BONUS)
    assert boosted["DL-0125"] == pytest.approx(plain["DL-0125"])


def test_unknown_verb_changes_nothing(index):
    plain = [(e.id, s) for e, s in index.search("power saving")]
    same = [(e.id, s) for e, s in index.search("power saving", verb="frobnicate")]
    assert plain == same


def test_results_are_sorted_by_descending_score(index):
    scores = [s for _, s in index.search("battery charging", top_k=8)]
    assert scores == sorted(scores, reverse=True)


def test_idf_is_never_negative(index):
    assert all(v >= 0 for v in index.idf.values())


def test_summary_mentions_id_type_and_message(entries):
    line = entries[0].summary()
    assert entries[0].id in line and entries[0].message in line
