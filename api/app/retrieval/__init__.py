"""Hybrid retrieval over the deeplink catalog.

Documents are generic: {"id": str, "fields": {field_name: text}, "meta": {...}}. Today they are
cleaned catalog entries; after the Screen Graph lands (component 7) the same modules index nodes.

Ranking is three layers:
  1. BM25 (keywords) + dense (meaning), merged by reciprocal rank fusion  -> candidates
  2. leaf boost + polarity/surface adjustments                            -> re-scored candidates
  3. optional cross-encoder rerank                                        -> confidence
"""

from pathlib import Path

from app.config import settings
from app.retrieval import bm25, dense, fuse
from app.retrieval import rerank as rerank_mod
from app.screengraph.clean import load_clean_catalog

# Fields BM25 and dense index.
_CATALOG_FIELDS = ("message", "qna_description", "clean_description")
# Fields the cross-encoder reads: the screen name and what it does, without the long QnA text.
_RERANK_FIELDS = ("message", "clean_description")

# Which catalog polarity each step verb wants.
_VERB_POLARITY = {
    "enable": "onURL",
    "disable": "offURL",
    "set": "updateURL",
    "adjust": "updateURL",
    "open": "onClickURL",
    "check": "onClickURL",
}
# The opposite polarity is an active mistake ("Disable Power saving" for an enable step).
_OPPOSITE = {"onURL": "offURL", "offURL": "onURL"}

_docs_by_id: dict[str, dict] = {}


def load_catalog_docs(path: str | Path) -> list[dict]:
    """Read data/kit/deeplinks.json into search documents keyed by catalog id (DL-0001...)."""
    return [
        {
            "id": entry["id"],
            "fields": {field: entry.get(field) or "" for field in _CATALOG_FIELDS},
            "meta": {"polarity": entry.get("polarity"), "surface": entry.get("surface")},
        }
        for entry in load_clean_catalog(path)
    ]


def build(docs: list[dict]) -> None:
    """Build both indexes over the same documents."""
    global _docs_by_id
    _docs_by_id = {d["id"]: d for d in docs}
    bm25.build(docs)
    dense.build(docs)


def _leaf(screen_path: str) -> str:
    """Last segment of 'Settings > Display > Dark mode' — the screen actually being targeted."""
    return screen_path.rsplit(">", 1)[-1].strip()


# Message verbs are the catalog's own prefix, not part of the screen name.
_MESSAGE_VERBS = frozenset(
    ["view", "enable", "disable", "adjust", "check", "switch", "increase", "run", "open"]
)


def _name_tokens(doc_id: str) -> tuple[set[str], set[str]]:
    """The entry's screen name (message minus its verb) and its cleaned description."""
    fields = _docs_by_id[doc_id]["fields"]
    message = {t for t in bm25.tokenize(fields["message"]) if t not in _MESSAGE_VERBS}
    return message, set(bm25.tokenize(fields["clean_description"]))


def _leaf_bonus(doc_id: str, leaf: str) -> float:
    """How closely the entry's own name matches the leaf screen, both ways.

    Overlap alone is not enough: "Storage" sits inside "Storage Share", so a one-sided score
    calls that a perfect hit. Jaccard punishes the entry's extra words, which is what keeps a
    step with no real catalog screen from looking confident.
    """
    leaf_tokens = set(bm25.tokenize(leaf))
    if not leaf_tokens:
        return 0.0
    best = 0.0
    for tokens in _name_tokens(doc_id):
        if tokens:
            best = max(best, len(leaf_tokens & tokens) / len(leaf_tokens | tokens))
    return best


def _polarity_adjust(doc_id: str, verb: str | None) -> float:
    """Reward a document that can serve the verb; punish one that only does the opposite.

    A catalog entry has one polarity; a Screen Graph node has every polarity its buttons offer,
    so a screen with both an on and an off button satisfies either verb.
    """
    wanted = _VERB_POLARITY.get((verb or "").lower())
    if not wanted:
        return 0.0
    meta = _docs_by_id[doc_id]["meta"]
    available = meta.get("polarities") or [meta.get("polarity")]
    if wanted in available:
        return settings.polarity_bonus
    if available == [_OPPOSITE.get(wanted)]:
        return -settings.polarity_penalty
    return 0.0


def _surface_adjust(doc_id: str) -> float:
    """Phone steps should not resolve to TV or Members entries."""
    surface = _docs_by_id[doc_id]["meta"].get("surface")
    return 0.0 if surface == "device Settings" else -settings.offsurface_penalty


def hybrid_search(query: str, k: int | None = None) -> list[tuple[str, float]]:
    """Keyword + meaning search, merged by reciprocal rank fusion."""
    top_k = k or settings.retrieval_top_k
    return fuse.rrf(
        bm25.search(query, top_k),
        dense.search(query, top_k),
        k=settings.rrf_k,
    )[:top_k]


def _needs_rerank(scored: list[tuple[str, float]]) -> bool:
    """True when the fast path looks unsure: a weak winner, or two candidates nearly tied."""
    top = scored[0][1]
    if top < settings.rerank_confident_score:
        return True
    runner_up = scored[1][1] if len(scored) > 1 else 0.0
    return (top - runner_up) < settings.rerank_min_margin


def search(
    screen_path: str,
    verb: str | None = None,
    k: int | None = None,
    use_rerank: bool | None = None,
) -> list[tuple[str, float]]:
    """Candidates for one action, best first, as (catalog id, score).

    The score mixes fusion rank with a leaf-name match and polarity/surface adjustments; it is a
    ranking signal, not the resolver's confidence. use_rerank=None follows settings.use_rerank
    (off by default, and then only on unsure cases); True and False force it on or off.
    """
    fused = hybrid_search(f"{screen_path} {verb or ''}".strip())
    if not fused:
        return []

    leaf = _leaf(screen_path)
    best = fused[0][1] or 1.0
    scored = [
        (
            doc_id,
            score / best
            + settings.leaf_weight * _leaf_bonus(doc_id, leaf)
            + _polarity_adjust(doc_id, verb)
            + _surface_adjust(doc_id),
        )
        for doc_id, score in fused
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    top_k = k or settings.rerank_top_k

    if use_rerank is None and not settings.use_rerank:
        return scored[:top_k]
    if use_rerank is False or (use_rerank is None and not _needs_rerank(scored)):
        return scored[:top_k]

    candidates = [doc_id for doc_id, _ in scored[: settings.rerank_candidates]]
    ranked = rerank_mod.rerank(f"{leaf} {verb or ''}".strip(), [_rerank_text(i) for i in candidates])
    return [(candidates[int(index)], score) for index, score in ranked[:top_k]]


def _rerank_text(doc_id: str) -> str:
    fields = _docs_by_id[doc_id]["fields"]
    return ". ".join(fields.get(name) or "" for name in _RERANK_FIELDS).strip(". ")
