"""BM25 over Screen Graph nodes; message and qna_description weighted above description."""

import re

from rank_bm25 import BM25Okapi

from app.config import settings

# Words that carry no signal in this catalog: they appear in almost every description
# ("Opens the ... settings page in device Settings on the device").
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "to",
        "for",
        "and",
        "or",
        "is",
        "are",
        "be",
        "this",
        "that",
        "it",
        "its",
        "your",
        "you",
        "with",
        "as",
        "at",
        "by",
        "from",
        "page",
        "screen",
        "settings",
        "setting",
        "device",
        "devices",
        "option",
        "options",
        "menu",
        "open",
        "opens",
    ]
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Module-level index, built once at startup (or in tests) via build().
_ids: list[str] = []
_index: BM25Okapi | None = None


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def _doc_tokens(fields: dict[str, str]) -> list[str]:
    """Tokens for one document, with important fields repeated per settings.bm25_field_weights."""
    tokens: list[str] = []
    for field, value in fields.items():
        if not value:
            continue
        weight = settings.bm25_field_weights.get(field, 1)
        tokens.extend(tokenize(value) * weight)
    return tokens


def build(docs: list[dict]) -> None:
    """Build the index from documents shaped {"id": str, "fields": {field: text}}."""
    global _ids, _index
    _ids = [d["id"] for d in docs]
    corpus = [_doc_tokens(d["fields"]) for d in docs]
    _index = BM25Okapi(corpus)


def search(query: str, k: int = 20) -> list[tuple[str, float]]:
    """Top-k (doc id, score) for the query, best first. Zero-score docs are dropped."""
    if _index is None:
        raise RuntimeError("bm25.build() must be called before search()")
    scores = _index.get_scores(tokenize(query))
    ranked = sorted(zip(_ids, scores), key=lambda pair: pair[1], reverse=True)
    return [(doc_id, float(score)) for doc_id, score in ranked[:k] if score > 0]
