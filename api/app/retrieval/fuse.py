"""Reciprocal rank fusion of BM25 and dense results."""

from collections import defaultdict


def rrf(*ranked: list[tuple[str, float]], k: int = 60) -> list[tuple[str, float]]:
    """Merge ranked lists by position, not by score.

    Each list contributes 1 / (k + rank) per document (rank starts at 1), so a document that
    places well in either searcher rises. Raw BM25 and cosine scores are on different scales,
    which is why positions are used instead.
    """
    fused: dict[str, float] = defaultdict(float)
    for ranking in ranked:
        for rank, (doc_id, _score) in enumerate(ranking, start=1):
            fused[doc_id] += 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda pair: pair[1], reverse=True)
