"""The four eval sets, the gold labels, and the validator that guards them.

Two halves: the sets as they are committed must be valid, and the validator must actually reject
the mistakes it claims to catch. A validator nobody has seen fail is not a validator.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from collections import Counter
from pathlib import Path

import pytest

from evalkit.bm25 import Bm25Index, load_entries
from evalkit.checks import check_url_leaks
from evalkit.paths import GOLD_PATH, REPO_ROOT
from evalkit.sets import load_kit, load_set, read_jsonl
from evalkit.stats import jaccard, norm_query


def _load_validator():
    """eval/sets/ holds data, not a package, so the validator is loaded by path."""
    path = REPO_ROOT / "eval" / "sets" / "validate_sets.py"
    spec = importlib.util.spec_from_file_location("validate_sets", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_sets"] = module
    spec.loader.exec_module(module)
    return module


vs = _load_validator()


@pytest.fixture(scope="module")
def kit():
    return load_kit()


@pytest.fixture(scope="module")
def kit_ids(kit):
    return {r.row_id for r in kit}


@pytest.fixture(scope="module")
def kit_norm(kit):
    return {r.row_id: norm_query(r.query) for r in kit}


@pytest.fixture
def rep():
    return vs.Report()


# --- the committed sets -------------------------------------------------------------------


def test_validator_passes_on_the_committed_sets():
    assert vs.main([]) == 0


def test_paraphrases_cover_every_kit_row_ten_times(kit_ids):
    rows = load_set("paraphrases")
    assert len(rows) == 10 * len(kit_ids)
    assert Counter(r["row_id"] for r in rows) == {rid: 10 for rid in kit_ids}


def test_every_paraphrase_register_is_used(kit_ids):
    rows = load_set("paraphrases")
    assert {r["register"] for r in rows} == vs.REGISTERS
    for rid in kit_ids:
        group = {r["register"] for r in rows if r["row_id"] == rid}
        assert group == vs.REGISTERS, f"{rid} is missing {vs.REGISTERS - group}"


def test_paraphrases_are_held_out_from_the_cache(kit_norm):
    """An exact match would be answered by the Tier 0 cache and would not test the semantic path."""
    for row in load_set("paraphrases"):
        assert norm_query(row["query"]) != kit_norm[row["row_id"]]


def test_near_miss_has_three_per_row_across_all_three_axes(kit_ids):
    rows = load_set("near_miss")
    assert len(rows) == 3 * len(kit_ids)
    assert Counter(r["row_id"] for r in rows) == {rid: 3 for rid in kit_ids}
    assert set(Counter(r["differs_in"] for r in rows)) == vs.DIFFERS_IN


def test_near_misses_are_closer_to_the_row_than_paraphrases(kit):
    """If similarity alone separated them, the set would not be testing the slot guard."""
    q = {r.row_id: r.query for r in kit}
    para = load_set("paraphrases")
    near = load_set("near_miss")
    p_mean = sum(jaccard(r["query"], q[r["row_id"]]) for r in para) / len(para)
    n_mean = sum(jaccard(r["query"], q[r["row_id"]]) for r in near) / len(near)
    assert n_mean > p_mean


def test_unseen_is_balanced_across_three_new_domains():
    rows = load_set("unseen")
    assert Counter(r["domain"] for r in rows) == {"Battery": 5, "Camera": 5, "Performance": 5}


def test_unseen_articles_are_segmentable_and_grounded_in_the_kit_house_style():
    for row in load_set("unseen"):
        content = row["siis_response"]["content"]
        assert "# " in content, row["id"]
        assert content.count("## Step") >= 5, row["id"]


def test_unseen_queries_are_not_in_the_kit(kit_norm):
    kit_queries = set(kit_norm.values())
    for row in load_set("unseen"):
        assert norm_query(row["query"]) not in kit_queries


@pytest.mark.parametrize("name", ["paraphrases", "near_miss", "unseen"])
def test_no_set_but_adversarial_contains_a_url(name):
    for row in load_set(name):
        assert check_url_leaks(row) == [], f"{name}/{row['id']}"


def test_adversarial_carries_real_payloads():
    rows = load_set("adversarial")
    assert len({r["kind"] for r in rows}) == len(rows)
    assert sum(1 for r in rows if check_url_leaks(r)) >= 4


def test_every_adversarial_case_still_demands_200_and_zero_leaks():
    """Hard rules 3 and 4 admit no exception, however hostile the input."""
    for row in load_set("adversarial"):
        assert row["expect"]["status"] == 200
        assert row["expect"]["url_leaks"] == 0
        assert row["expect"]["contexts"] in vs.CONTEXTS_EXPECT


def test_adversarial_covers_the_shapes_the_api_must_survive():
    kinds = {r["kind"] for r in load_set("adversarial")}
    assert {"prompt_injection", "siis_as_string", "siis_missing", "empty_query"} <= kinds


# --- the gold labels ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def gold():
    return read_jsonl(GOLD_PATH)


def test_gold_ids_all_exist_in_the_catalog(gold):
    known = {e.id for e in load_entries()}
    for row in gold:
        for field in ("expected_id", "acceptable_ids", "parent_ids"):
            for entry_id in vs._as_list(row.get(field)):
                assert entry_id in known, f"{entry_id} ({field})"


def test_gold_tier_matches_the_recorded_link(gold):
    by_id = {e.id: e for e in load_entries()}
    for row in gold:
        if row["tier"] == "catalog":
            assert row["expected_deeplink"] == by_id[row["expected_id"]].deeplink
        elif row["tier"] == "dummy":
            assert row["expected_id"] is None
            assert row["expected_deeplink"] == vs.DUMMY_URI
        else:
            assert row["expected_id"] is None and row["expected_deeplink"] is None


def test_gold_steps_are_unique(gold):
    assert len({r["step"] for r in gold}) == len(gold)


def test_gold_agrees_with_the_shipped_fixtures(gold):
    """The three screens Vishaal's fixtures already link were labelled independently here."""
    by_screen = {(r["screen"], r["verb"]): r["expected_id"] for r in gold}
    assert by_screen[("Settings > Connections > Wi-Fi", "open")] == "DL-0313"
    assert by_screen[("Settings > Display > Navigation bar", "open")] == "DL-0169"
    assert by_screen[("Settings > Display > Touch sensitivity", "enable")] == "DL-0126"
    assert by_screen[("Settings > Display > Touch sensitivity", "disable")] == "DL-0125"


def test_gold_is_not_just_what_bm25_said(gold):
    """If the labels only echoed the labelling tool, precision@1 would measure nothing."""
    index = Bm25Index.build(load_entries())
    catalog_rows = [r for r in gold if r["tier"] == "catalog"]
    agree = 0
    for row in catalog_rows:
        hits = index.search(f"{row['step']} {row['screen']}", verb=row["verb"], top_k=1)
        if hits and hits[0][0].id in {row["expected_id"], *row.get("acceptable_ids", [])}:
            agree += 1
    assert agree < len(catalog_rows), "every label equals the BM25 top hit, so the gold is circular"


def test_gold_records_the_catalog_gaps_it_found(gold):
    """Dummy and manual tiers are measurements, not omissions; losing them would hide the gaps."""
    tiers = Counter(r["tier"] for r in gold)
    assert tiers["dummy"] > 0 and tiers["manual"] > 0


# --- the validator itself -----------------------------------------------------------------


@pytest.fixture
def good_paraphrase(kit_ids):
    rid = min(kit_ids)
    return [
        {
            "id": f"p{i}",
            "row_id": rid,
            "query": f"unique paraphrase number {i} about the screen",
            "register": r,
            "source": "authored",
            "expect": "hit",
        }
        for i, r in enumerate(sorted(vs.REGISTERS) * 2)
    ]


def test_validator_accepts_a_well_formed_paraphrase_group(good_paraphrase, kit_ids, kit_norm, rep):
    one_row = {min(kit_ids)}
    vs.check_paraphrases(good_paraphrase, one_row, kit_norm, rep)
    assert rep.failures == []


def test_validator_rejects_an_unknown_row_id(good_paraphrase, kit_ids, kit_norm, rep):
    rows = copy.deepcopy(good_paraphrase)
    rows[0]["row_id"] = "row_999"
    vs.check_paraphrases(rows, kit_ids, kit_norm, rep)
    assert any("not a kit row" in f for f in rep.failures)


def test_validator_rejects_a_paraphrase_identical_to_its_kit_query(kit, kit_ids, kit_norm, rep):
    row = kit[0]
    rows = [{"id": "p1", "row_id": row.row_id, "query": row.query, "register": "formal"}]
    vs.check_paraphrases(rows, kit_ids, kit_norm, rep)
    assert any("not held out" in f for f in rep.failures)


def test_validator_rejects_a_bad_register(good_paraphrase, kit_ids, kit_norm, rep):
    rows = copy.deepcopy(good_paraphrase)
    rows[0]["register"] = "sarcastic"
    vs.check_paraphrases(rows, kit_ids, kit_norm, rep)
    assert any("register" in f for f in rep.failures)


def test_validator_rejects_a_duplicate_query(good_paraphrase, kit_ids, kit_norm, rep):
    rows = copy.deepcopy(good_paraphrase)
    rows[1]["query"] = rows[0]["query"]
    vs.check_paraphrases(rows, kit_ids, kit_norm, rep)
    assert any("duplicate query" in f for f in rep.failures)


def test_validator_rejects_a_url_in_a_paraphrase(good_paraphrase, kit_ids, kit_norm, rep):
    rows = copy.deepcopy(good_paraphrase)
    rows[0]["query"] = "see https://samsung.com/support for my blank screen"
    vs.check_paraphrases(rows, kit_ids, kit_norm, rep)
    assert any("URL" in f or "scheme" in f for f in rep.failures)


def test_validator_rejects_a_wrong_paraphrase_count(good_paraphrase, kit_ids, kit_norm, rep):
    one_row = {min(kit_ids)}
    vs.check_paraphrases(good_paraphrase[:4], one_row, kit_norm, rep)
    assert any("want 10" in f for f in rep.failures)


def test_validator_rejects_a_near_miss_on_a_bad_axis(kit_ids, kit_norm, rep):
    rid = min(kit_ids)
    rows = [
        {
            "id": "n1",
            "row_id": rid,
            "query": "a different screen complaint entirely",
            "expected_slots": {"component": "screen"},
            "differs_in": "vibes",
            "expect": "miss",
        }
    ]
    vs.check_near_miss(rows, kit_ids, kit_norm, rep)
    assert any("differs_in" in f for f in rep.failures)


def test_validator_rejects_a_near_miss_that_expects_a_hit(kit_ids, kit_norm, rep):
    rid = min(kit_ids)
    rows = [
        {
            "id": "n1",
            "row_id": rid,
            "query": "a different screen complaint entirely",
            "expected_slots": {"component": "screen"},
            "differs_in": "symptom",
            "expect": "hit",
        }
    ]
    vs.check_near_miss(rows, kit_ids, kit_norm, rep)
    assert any("expect should be 'miss'" in f for f in rep.failures)


def test_validator_rejects_an_unknown_slot_key(kit_ids, kit_norm, rep):
    rid = min(kit_ids)
    rows = [
        {
            "id": "n1",
            "row_id": rid,
            "query": "a different screen complaint entirely",
            "expected_slots": {"colour": "blue"},
            "differs_in": "symptom",
            "expect": "miss",
        }
    ]
    vs.check_near_miss(rows, kit_ids, kit_norm, rep)
    assert any("unexpected slot keys" in f for f in rep.failures)


def test_validator_rejects_an_unseen_article_with_no_steps(kit_norm, rep):
    rows = [
        {
            "id": "u1",
            "domain": "Battery",
            "query": "my battery is flat",
            "siis_response": {"title": "Battery", "content": "# Battery\nSome prose with no sections."},
        }
    ]
    vs.check_unseen(rows, kit_norm, rep)
    assert any("no '## Step' section" in f for f in rep.failures)


def test_validator_rejects_an_unseen_domain_that_is_already_in_the_kit(kit_norm, rep):
    rows = [
        {
            "id": "u1",
            "domain": "Display",
            "query": "my screen is black",
            "siis_response": {"title": "t", "content": "# t\n## Step 1: Do it\nDo the thing."},
        }
    ]
    vs.check_unseen(rows, kit_norm, rep)
    assert any("domain" in f for f in rep.failures)


def test_validator_rejects_an_adversarial_case_that_tolerates_a_leak(rep):
    rows = [
        {
            "id": "a1",
            "kind": "k",
            "query": "q",
            "note": "n",
            "expect": {"status": 200, "url_leaks": 1, "contexts": "any"},
        }
    ]
    vs.check_adversarial(rows, rep)
    assert any("hard rule 3" in f for f in rep.failures)


def test_validator_rejects_an_adversarial_case_that_tolerates_an_error(rep):
    rows = [
        {
            "id": "a1",
            "kind": "k",
            "query": "q",
            "note": "n",
            "expect": {"status": 500, "url_leaks": 0, "contexts": "any"},
        }
    ]
    vs.check_adversarial(rows, rep)
    assert any("hard rule 4" in f for f in rep.failures)


def test_validator_rejects_a_gold_id_that_is_not_in_the_catalog(rep):
    rows = [
        {
            "step": "s",
            "screen": "x",
            "verb": "open",
            "tier": "catalog",
            "expected_id": "DL-9999",
            "expected_deeplink": "bixby://masked/act/zz",
        }
    ]
    vs.check_gold(rows, rep)
    assert any("not in the catalog" in f for f in rep.failures)


def test_validator_rejects_a_gold_link_that_does_not_match_its_id(rep):
    rows = [
        {
            "step": "s",
            "screen": "x",
            "verb": "open",
            "tier": "catalog",
            "expected_id": "DL-0313",
            "expected_deeplink": "bixby://masked/act/wrong",
        }
    ]
    vs.check_gold(rows, rep)
    assert any("does not match the catalog URI" in f for f in rep.failures)


def test_validator_rejects_a_dummy_tier_that_names_an_id(rep):
    rows = [
        {
            "step": "s",
            "screen": "x",
            "verb": "open",
            "tier": "dummy",
            "expected_id": "DL-0313",
            "expected_deeplink": vs.DUMMY_URI,
        }
    ]
    vs.check_gold(rows, rep)
    assert any("must not name a catalog id" in f for f in rep.failures)


def test_validator_rejects_a_manual_tier_that_carries_a_link(rep):
    rows = [
        {
            "step": "s",
            "screen": "x",
            "verb": "open",
            "tier": "manual",
            "expected_id": None,
            "expected_deeplink": vs.DUMMY_URI,
        }
    ]
    vs.check_gold(rows, rep)
    assert any("no link at all" in f for f in rep.failures)


def test_cross_check_fails_when_near_misses_are_further_away_than_paraphrases(kit, rep):
    row = kit[0]
    kit_q = {row.row_id: row.query}
    para = [{"row_id": row.row_id, "query": row.query + " please help"}]
    near = [{"row_id": row.row_id, "query": "totally unrelated words about nothing whatsoever"}]
    vs.cross_check(para, near, kit_q, rep)
    assert any("does not test the slot guard" in f for f in rep.failures)


def test_report_require_records_a_failure_and_returns_false(rep):
    assert rep.require(False, "where", "boom") is False
    assert rep.failures == ["where: boom"]
    assert rep.require(True, "where", "fine") is True
    assert len(rep.failures) == 1


def test_all_set_files_exist_and_are_non_empty():
    for name in vs.SET_NAMES:
        path = Path(REPO_ROOT) / "eval" / "sets" / f"{name}.jsonl"
        assert path.exists() and path.stat().st_size > 0, name
