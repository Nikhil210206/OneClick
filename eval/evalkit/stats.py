"""Small numeric helpers: percentiles, token Jaccard, query normalisation."""

from __future__ import annotations

import itertools
import math
import re

_TOKEN = re.compile(r"[a-z0-9]+")
_LEADING_NUMBER = re.compile(r"^\s*\d+\s*[.)]\s*")


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (p in 0-100). None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta and not tb:
        return 1.0
    return len(ta & tb) / len(ta | tb)


def mean_pairwise_jaccard(texts: list[str]) -> float | None:
    """Lower is more diverse. Proxy for the scorer's lexical-diversity check (A5)."""
    pairs = list(itertools.combinations(texts, 2))
    if not pairs:
        return None
    return sum(jaccard(a, b) for a, b in pairs) / len(pairs)


def norm_query(text: str) -> str:
    """Loose key for matching a results line to a kit query: numbering, quotes, case and punctuation."""
    text = _LEADING_NUMBER.sub("", text)
    return " ".join(_TOKEN.findall(text.lower()))


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None
