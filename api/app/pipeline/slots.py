"""Component 1: lexicon/regex slot extractor (component, symptom). No LLM. Lexicon in data/slot_lexicon.json."""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.models import Slots
from app.pipeline.spell import correct_typos

# settings.data_dir, not a path relative to this file: the Docker image copies api/ alone.
_LEXICON_PATH = Path(settings.data_dir) / "slot_lexicon.json"

# "no physical damage", "doesn't overheat": the phrase is present but denied.
_NEGATIONS = ("no ", "not ", "never ", "without ", "isn't ", "isnt ", "aren't ", "arent ")


@lru_cache(maxsize=1)
def _lexicon() -> dict[str, dict[str, list[str]]]:
    """The word lists, loaded once. Keys: component, symptom."""
    raw = _raw_lexicon()
    return {field: values for field, values in raw.items() if not field.startswith("_")}


@lru_cache(maxsize=1)
def _raw_lexicon() -> dict:
    return json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _weak_components() -> frozenset[str]:
    """Labels that only win when nothing else matches."""
    return frozenset(_raw_lexicon().get("_weak_components", []))


def _negated(text: str, start: int) -> bool:
    """True when the words just before the match deny it."""
    window = text[max(0, start - 14) : start]
    return any(window.endswith(word) for word in _NEGATIONS)


def _best_label(text: str, labels: dict[str, list[str]]) -> str | None:
    """The label mentioned earliest in the text; the longer phrase breaks a tie.

    Earliest wins because a complaint names its subject first ("my SCREEN went black ... I
    cannot use Smart Switch"): a later phrase is usually context, not the problem itself.
    """
    best_label, best_position, best_length = None, len(text) + 1, 0
    for label, phrases in labels.items():
        for phrase in phrases:
            match = re.search(rf"\b{re.escape(phrase)}\b", text)
            if not match or _negated(text, match.start()):
                continue
            position = match.start()
            if position < best_position or (position == best_position and len(phrase) > best_length):
                best_label, best_position, best_length = label, position, len(phrase)
    return best_label


INTENT_FAULT = "fault"
INTENT_CONFIGURE = "configure"


def _intent(text: str, phrases: dict[str, list[str]]) -> str:
    """configure when the query asks for a behaviour, fault otherwise.

    "I want my screen to go black while Smart Switch runs" names the same component and symptom as
    the fault it resembles; only the goal differs. eval/sets/near_miss.jsonl calls these differs_in
    "intent", and they were 16 of the 18 near misses served a kit fault plan. The phrases live in
    data/slot_lexicon.json; "how do I fix..." matches none of them, so it stays a fault.
    """
    return INTENT_CONFIGURE if _best_label(text, phrases) else INTENT_FAULT


def _best_component(text: str, labels: dict[str, list[str]]) -> str | None:
    """Prefer a real part over a weak one: an app name usually says where a fault was noticed.

    "Opening an email in Gmail makes the screen flash" is a screen problem; taking 'app' there
    blocks the cache match against the stored screen answer (7 of 200 paraphrases).
    """
    weak = _weak_components()
    strong = {label: phrases for label, phrases in labels.items() if label not in weak}
    return _best_label(text, strong) or _best_label(text, labels)


def direction(text: str) -> str | None:
    """'add' or 'remove' for a configure request that names exactly one of them, else None.

    "I want to add a floating circle" and "...a floating circle; I want to remove it" share every other
    slot (screen, configure), and the cache served one the other's plan (near miss nm_12_3). A fault
    complaint has no direction ("the screen turns off" is a symptom, not a wish), and a request naming
    both or neither is left open, so a terse paraphrase ("make the bubble go away") still matches.
    """
    text = correct_typos(text).lower()
    lexicon = _lexicon()
    if _intent(text, lexicon.get("intent", {})) != INTENT_CONFIGURE:
        return None
    named = set()
    for label, phrases in lexicon.get("direction", {}).items():
        for phrase in phrases:
            match = re.search(rf"\b{re.escape(phrase)}\b", text)
            if match and not _negated(text, match.start()):
                named.add(label)
                break
    return named.pop() if len(named) == 1 else None


def named_components(text: str) -> set[str]:
    """Every part the text names (not only the first), leaving out weak labels and denied mentions.

    mismatch.py asks whether an article mentions any of them; a weak label ("app") is in nearly every
    article, so it can never say the article is about something else.
    """
    text = text.lower()
    weak = _weak_components()
    found = set()
    for label, phrases in _lexicon().get("component", {}).items():
        if label in weak:
            continue
        for phrase in phrases:
            match = re.search(rf"\b{re.escape(phrase)}\b", text)
            if match and not _negated(text, match.start()):
                found.add(label)
                break
    return found


def mentions_component(text: str, label: str) -> bool:
    """True when the text uses any of the label's lexicon phrases, plurals included ("batteries" too).

    Whole words only: "sim" must not match "similar".
    """
    text = text.lower()
    for phrase in _lexicon().get("component", {}).get(label, []):
        stem = phrase[:-1] + "(?:y|ies)" if phrase.endswith("y") else re.escape(phrase)
        if re.search(rf"\b{stem}(?:s|es)?\b", text):
            return True
    return False


def extract_slots(norm_query: str) -> Slots:
    """Component, symptom and intent for the cache guard. A missing component or symptom is None."""
    text = correct_typos(norm_query).lower()  # "blnak" still names the blank-screen symptom
    lexicon = _lexicon()
    return Slots(
        component=_best_component(text, lexicon.get("component", {})),
        symptom=_best_label(text, lexicon.get("symptom", {})),
        intent=_intent(text, lexicon.get("intent", {})),
    )
