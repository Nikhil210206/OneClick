"""Component 1: lexicon/regex slot extractor (component, symptom). No LLM. Lexicon in data/slot_lexicon.json."""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.models import Slots

_LEXICON_PATH = Path(__file__).resolve().parents[3] / "data" / "slot_lexicon.json"

# "no physical damage", "doesn't overheat": the phrase is present but denied.
_NEGATIONS = ("no ", "not ", "never ", "without ", "isn't ", "isnt ", "aren't ", "arent ")


@lru_cache(maxsize=1)
def _lexicon() -> dict[str, dict[str, list[str]]]:
    """The word lists, loaded once. Keys: component, symptom."""
    raw = json.loads(_LEXICON_PATH.read_text(encoding="utf-8"))
    return {field: values for field, values in raw.items() if not field.startswith("_")}


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


def extract_slots(norm_query: str) -> Slots:
    """Component and symptom for the cache guard. Unknown fields stay None, which acts as a wildcard."""
    text = norm_query.lower()
    lexicon = _lexicon()
    return Slots(
        component=_best_label(text, lexicon.get("component", {})),
        symptom=_best_label(text, lexicon.get("symptom", {})),
    )
