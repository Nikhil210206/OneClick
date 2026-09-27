"""Lexicon slots (component 1) and the cache slot guard (component 2)."""

import json
from pathlib import Path

import pytest

from app.cache.slot_guard import compatible
from app.models import Slots
from app.pipeline.slots import extract_slots

KIT = Path(__file__).resolve().parents[2] / "data" / "kit" / "siis_responses.json"


@pytest.mark.parametrize(
    ("query", "component", "symptom"),
    [
        ("my screen is black", "screen", "black"),
        ("my screen is cracked", "screen", "cracked"),
        ("display wont turn on", "screen", "black"),
        ("battery drains fast", "battery", "drain"),
        ("camera photos blurry", "camera", "blurry"),
        ("wifi keeps disconnecting", "network", "no_connection"),
    ],
)
def test_reads_component_and_symptom(query, component, symptom):
    slots = extract_slots(query)
    assert (slots.component, slots.symptom) == (component, symptom)


def test_ignores_a_denied_symptom():
    """ "no physical damage" must not read as a cracked screen."""
    assert extract_slots("there is no physical damage but the screen is black").symptom == "black"


def test_earliest_mention_wins_over_later_context():
    """The complaint names its subject first; Smart Switch later is context, not the problem."""
    query = "my screen went completely black so i cannot use smart switch to transfer data"
    assert extract_slots(query).component == "screen"


def test_extraction_is_deterministic():
    query = "my galaxy screen flickers and goes blank"
    assert extract_slots(query) == extract_slots(query)


def test_every_kit_query_gets_a_component():
    rows = json.loads(KIT.read_text(encoding="utf-8"))["responses"]
    missing = [r["id"] for r in rows if extract_slots(r["original_query"]).component is None]
    assert not missing


def test_guard_blocks_a_different_symptom_on_the_same_part():
    black = Slots(component="screen", symptom="black")
    cracked = Slots(component="screen", symptom="cracked")
    assert not compatible(black, cracked)


def test_guard_treats_a_missing_query_slot_as_a_wildcard():
    """A missing slot on the QUERY side matches anything; see the one-sided symptom test below."""
    black = Slots(component="screen", symptom="black")
    vague = Slots(component="screen", symptom=None)
    assert compatible(vague, black)
    assert compatible(Slots(), black)


def test_guard_blocks_a_different_component():
    assert not compatible(Slots(component="screen"), Slots(component="battery"))


def test_an_app_name_does_not_outrank_the_faulty_part():
    """Gmail is where the fault was noticed; the screen is what is faulty (7 of 200 paraphrases)."""
    query = "opening an email in gmail makes the screen flash and go blank"
    assert extract_slots(query).component == "screen"


def test_a_genuine_app_problem_still_reads_as_app():
    assert extract_slots("the gmail app crashes when i open it").component == "app"


@pytest.mark.parametrize(
    ("query", "intent"),
    [
        ("i want my screen to go black while smart switch runs", "configure"),
        ("how do i set the screen timeout", "configure"),
        ("is there a way to keep the screen on", "configure"),
        ("my screen goes black while smart switch runs", "fault"),
        # asking how to FIX is a fault report, not a configuration request
        ("how do i fix my black screen", "fault"),
        # "is fine" is how people describe the half that still works
        ("one side is dark and the other side is fine", "fault"),
        ("i dont want my screen to go black", "fault"),
    ],
)
def test_intent_tells_a_wish_from_a_fault(query, intent):
    assert extract_slots(query).intent == intent


def test_a_query_symptom_the_entry_never_named_misses():
    """One-sided: the query names a symptom and the cached entry has none."""
    assert not compatible(Slots(component="screen", symptom="cracked"), Slots(component="screen"))
    assert not compatible(Slots(component="screen", symptom="black"), Slots(component="screen"))


def test_a_query_without_a_symptom_still_matches():
    """The reverse stays a wildcard: terse paraphrases name no symptom (54% vs 82% hit rate)."""
    assert compatible(Slots(component="screen"), Slots(component="screen", symptom="black"))


def test_fault_and_configure_never_match():
    fault = Slots(component="screen", symptom="black", intent="fault")
    wish = Slots(component="screen", symptom="black", intent="configure")
    assert not compatible(wish, fault)
    assert not compatible(fault, wish)


def test_an_entry_cached_before_intent_existed_is_a_wildcard():
    assert compatible(
        Slots(component="screen", symptom="black", intent="fault"), Slots(component="screen", symptom="black")
    )


@pytest.mark.parametrize(
    "query",
    [
        "touch lags, and i also want buttons back",
        "the screen flickers and i'd also like the old navigation bar",
        "my phone is slow, i would also like bigger icons",
    ],
)
def test_a_request_added_to_a_fault_reads_as_configure(query):
    """A fault plus a request is not the single-fault plan, so the cache must not serve it one."""
    assert extract_slots(query).intent == "configure"


def test_also_inside_a_word_is_not_a_request():
    """Phrases match whole words: "wifi also wants" is not "i also want"."""
    assert extract_slots("my wifi also wants a password every time").intent == "fault"
