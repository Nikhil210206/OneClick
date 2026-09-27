"""Resolver against data/fixtures/resolver_cases.json, the contract with the engine lane."""

import json
from pathlib import Path

import pytest

from app import retrieval
from app.models import DraftAction, DraftStep
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


def _change(screen_path: str, verb: str, step: str) -> DraftAction:
    return DraftAction(
        name="Change A Setting",
        description="It will change the setting",
        screen_path=screen_path,
        intent_verb=verb,
        steps=[DraftStep(text=step, src_ids=["S1"])],
    )


def test_a_change_with_no_toggle_opens_its_own_screen(cases):
    """No entry turns full screen gestures off; the screen that holds the choice has a page link.
    Kept out of resolver_cases.json: its contract ties the entry type to the verb (disable -> offURL).
    `cases` is only here to load the catalog."""
    action = _change(
        "Settings > Display > Navigation bar", "disable", "Select Buttons to turn off full screen gestures."
    )
    decision = resolve(action)
    assert (decision.tier.value, decision.entry_id) == ("catalog", "DL-0169")


def test_the_page_fallback_never_leaves_the_steps_path(cases):
    """Alarm volume has no entry above the floor. The best page for "Alarm" anywhere in the catalog is
    alarms in Do Not Disturb (DL-0319), a different screen: the placeholder is the safe answer."""
    action = _change(
        "Settings > Sounds and vibration > Volume > Alarm", "set", "Drag the Alarm slider to the right."
    )
    decision = resolve(action)
    assert decision.entry_id != "DL-0319"
    assert decision.tier.value == "dummy"
