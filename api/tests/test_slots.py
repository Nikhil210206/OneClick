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


def test_guard_treats_a_missing_slot_as_a_wildcard():
    black = Slots(component="screen", symptom="black")
    vague = Slots(component="screen", symptom=None)
    assert compatible(black, vague)
    assert compatible(Slots(), black)


def test_guard_blocks_a_different_component():
    assert not compatible(Slots(component="screen"), Slots(component="battery"))
