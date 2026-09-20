"""Cross-encoder rerank of fused candidates.

DISABLED by default (settings.use_rerank = False): it costs ~250 ms per step against ~5 ms for
the fast path, and on the gold sets we have it showed no measurable gain (a 1-2 case swing on
8-38 labelled steps, which is noise). The model is only downloaded when something turns it on.
Re-measure with `python scripts/eval_retrieval.py --rerank` once the gold set is larger.
"""

import math

from fastembed.rerank.cross_encoder import TextCrossEncoder

from app.config import settings

_model: TextCrossEncoder | None = None


def _get_model() -> TextCrossEncoder:
    """Load the ONNX cross-encoder once; downloads to the fastembed cache on first use."""
    global _model
    if _model is None:
        _model = TextCrossEncoder(model_name=settings.rerank_model)
    return _model


def rerank(query: str, candidates: list[str]) -> list[tuple[str, float]]:
    """Score each candidate text against the query together, best first.

    BM25 and dense compare query and document separately; the cross-encoder reads the pair, so
    it separates near-identical entries ("Brightness" vs "Adjust Brightness"). Returns indices
    into `candidates` as strings alongside a 0-1 confidence (sigmoid of the raw logit).
    """
    if not candidates:
        return []
    scores = list(_get_model().rerank(query, candidates))
    ranked = [(str(i), 1.0 / (1.0 + math.exp(-score))) for i, score in enumerate(scores)]
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked
