"""Resolver against data/fixtures/resolver_cases.json, the contract with the engine lane."""

import json
from pathlib import Path

import pytest

from app import retrieval
from app.models import DraftAction
from app.screengraph import load
from app.screengraph.resolver import offscreen_tier, resolve, validation_for

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "data" / "kit" / "deeplinks.json"
GRAPH = ROOT / "data" / "build" / "screengraph.json"
CASES = ROOT / "data" / "fixtures" / "resolver_cases.json"


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    if not CATALOG.exists() or not CASES.exists():
        pytest.skip("kit catalog or resolver fixtures not present")
    load(CATALOG, GRAPH if GRAPH.exists() else None)
    retrieval.build(retrieval.load_catalog_docs(CATALOG))
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


def test_every_case_resolves_to_the_expected_tier(cases):
    wrong = []
    for case in cases:
        expected = case["expected"]
        decision = resolve(DraftAction(**case["action"]))
        if decision.tier.value != expected["tier"]:
            wrong.append(f"{case['id']}: tier {decision.tier.value} != {expected['tier']}")
        elif expected["tier"] == "catalog" and decision.entry_id != expected["entry_id"]:
            wrong.append(f"{case['id']}: entry {decision.entry_id} != {expected['entry_id']}")
    assert not wrong, "\n".join(wrong)


def test_no_case_picks_a_lookalike_entry(cases):
    """must_not_match lists catalog entries that look right but are the wrong screen."""
    for case in cases:
        decision = resolve(DraftAction(**case["action"]))
        assert decision.entry_id not in (case["expected"].get("must_not_match") or []), case["id"]


def test_physical_steps_never_reach_the_search(cases):
    for case in cases:
        if case["expected"]["tier"] != "manual":
            continue
        action = DraftAction(**case["action"])
        assert offscreen_tier(action) is not None or not action.screen_path, case["id"]


def test_validation_is_copied_verbatim_from_the_catalog(cases):
    entries = {e["id"]: e for e in json.loads(CATALOG.read_text(encoding="utf-8"))["deeplinks"]}
    for case in cases:
        decision = resolve(DraftAction(**case["action"]))
        if decision.tier.value != "catalog":
            continue
        assert validation_for(decision.entry_id) == entries[decision.entry_id]["validation"], case["id"]
