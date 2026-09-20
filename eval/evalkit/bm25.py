"""Pure-Python BM25 over the deeplink catalog, for gold labelling only.

Deliberately independent of `app/retrieval/`: the gold set must not be labelled with the same
retriever it is used to grade, or precision@1 only measures self-agreement. This is a plain
Okapi BM25 over the catalog's own text fields, with a small polarity prior so "turn off X" ranks
`offURL` entries above `onURL` ones.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from evalkit.paths import CATALOG_PATH

K1 = 1.5
B = 0.75

_TOKEN = re.compile(r"[a-z0-9]+")

# Catalog `originalType` -> the verbs that entry is the right polarity for.
POLARITY_VERBS = {
    "onURL": {"enable", "turn on", "switch on", "activate", "allow"},
    "offURL": {"disable", "turn off", "switch off", "deactivate", "remove", "stop"},
    "updateURL": {"update", "upgrade", "install"},
    "onClickURL": {"open", "view", "go to", "check", "navigate", "launch"},
}
POLARITY_BONUS = 1.5

_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "then",
    "this",
    "to",
    "your",
    "you",
}


def terms(text: str) -> list[str]:
    """Lowercase word tokens, minus stopwords. Duplicates are kept: BM25 uses term frequency."""
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


@dataclass(frozen=True)
class Entry:
    """One catalog row, flattened to what a labeller needs to see."""

    id: str
    deeplink: str
    description: str
    message: str
    original_type: str | None
    qna_description: str
    validation: dict | None

    @property
    def text(self) -> str:
        return f"{self.message} {self.description} {self.qna_description}"

    @property
    def validatable(self) -> bool:
        """Only the 138 `onURL` enable toggles carry a full key/condition/value validation object."""
        v = self.validation or {}
        return bool(v.get("key") and v.get("condition") is not None and v.get("value") is not None)

    def summary(self) -> str:
        return f"{self.id}  [{self.original_type or 'none'}]  {self.message} — {self.description}"


def load_entries(path: Path = CATALOG_PATH) -> list[Entry]:
    data = json.loads(path.read_text())
    return [
        Entry(
            id=e["id"],
            deeplink=e["deeplink"],
            description=e.get("description") or "",
            message=e.get("message") or "",
            original_type=e.get("originalType"),
            qna_description=e.get("qna_description") or "",
            validation=e.get("validation"),
        )
        for e in data["deeplinks"]
    ]


@dataclass
class Bm25Index:
    """Okapi BM25. Built once over the catalog (578 entries), so speed does not matter."""

    entries: list[Entry]
    docs: list[Counter] = field(default_factory=list)
    lengths: list[int] = field(default_factory=list)
    idf: dict[str, float] = field(default_factory=dict)
    avg_len: float = 0.0

    @classmethod
    def build(cls, entries: list[Entry]) -> Bm25Index:
        docs = [Counter(terms(e.text)) for e in entries]
        lengths = [sum(d.values()) for d in docs]
        n = len(docs)
        df: Counter = Counter()
        for d in docs:
            df.update(d.keys())
        # BM25+ style idf: never negative, so a term in every document simply stops discriminating.
        idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        avg_len = sum(lengths) / n if n else 0.0
        return cls(entries=entries, docs=docs, lengths=lengths, idf=idf, avg_len=avg_len)

    def _score(self, query_terms: list[str], i: int) -> float:
        doc, length = self.docs[i], self.lengths[i]
        score = 0.0
        for t in query_terms:
            f = doc.get(t, 0)
            if not f:
                continue
            denom = f + K1 * (1 - B + B * length / self.avg_len) if self.avg_len else f
            score += self.idf.get(t, 0.0) * f * (K1 + 1) / denom
        return score

    def search(self, query: str, verb: str = "", top_k: int = 8) -> list[tuple[Entry, float]]:
        """Highest scoring entries first. `verb` adds a polarity bonus, it is not matched as text."""
        qt = terms(query)
        verb_l = verb.lower().strip()
        scored = []
        for i, entry in enumerate(self.entries):
            score = self._score(qt, i)
            if score <= 0:
                continue
            if verb_l and verb_l in POLARITY_VERBS.get(entry.original_type or "", set()):
                score += POLARITY_BONUS
            scored.append((entry, score))
        scored.sort(key=lambda p: (-p[1], p[0].id))
        return scored[:top_k]
