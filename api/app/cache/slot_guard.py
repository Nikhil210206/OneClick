"""Rejects semantic hits whose lexicon slots contradict the query (black vs cracked)."""

from app.models import Slots


def compatible(a: Slots, b: Slots) -> bool:
    """True when nothing in the two slot sets contradicts.

    A missing slot is a wildcard: "display won't turn on" finds no symptom word, so it may still
    match a cached "screen is black". Two *filled* slots that differ block the hit, which is what
    keeps "screen is black" from answering "screen is cracked" - embeddings rate those 0.9 alike.
    """
    for left, right in ((a.component, b.component), (a.symptom, b.symptom)):
        if left and right and left != right:
            return False
    return True
