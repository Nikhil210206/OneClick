"""A complaint that arrives without an article: answered from real articles, never from nothing.

    1. plans      the kit table (the 20 kit plans + their variations from results.jsonl, loaded at
                  startup) and every plan this server has solved, whatever its article
                  -> the closest plan above settings.no_siis_plan_threshold that the slot guard allows
    2. articles   every article the API has received (the kit's are pre-loaded)
                  -> the one clearly closest to the complaint, above settings.article_match_threshold
                  and ahead of the runner-up by settings.article_match_margin; the pipeline then runs
                  on it, so every step still comes from real SIIS text
    3. nothing    the caller returns empty contexts with fallback no_siis_context

No LLM fills the gap: invented steps are penalised in review (FAQ Q14), and free generation is where
"visit samsung.com/support" comes from (G5 allows zero URLs). Both gates are stricter than the
with-article cache because the article hash no longer guards the match (measured values in config).

The kit table is an index of its own, not entries in the SIIS cache: that cache still ships empty, so
the scorer's first call with an article is genuinely cold. Named no_siis.py rather than lookup.py: a
module named `lookup` inside the package would shadow `cache.lookup()`.
"""

import json
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.cache import semantic
from app.cache.exact import make_key
from app.cache.slot_guard import compatible
from app.config import settings
from app.models import Slots
from app.pipeline.spell import correct_typos
from app.retrieval import dense

_lock = threading.Lock()
_loaded = False
# The kit table: one row per phrasing, pointing at its plan.
_table_rows: list[tuple[str, Slots, dict]] = []  # (key, slots, plan)
_table_matrix: np.ndarray | None = None
# Remembered articles: hash -> (title, text, vectors of title + each section), oldest first.
_articles: "OrderedDict[str, tuple[str, str, np.ndarray]]" = OrderedDict()
_background = ThreadPoolExecutor(max_workers=1, thread_name_prefix="article-memory")


@dataclass(frozen=True)
class RetrievedArticle:
    text: str
    siis_hash: str
    title: str
    similarity: float


@dataclass(frozen=True)
class NoSiisHit:
    plan: dict
    key: str
    similarity: float
    source: str  # kit_table | cache
    tier: str = "semantic"

    def as_detail(self) -> dict:
        return {"hit": True, "tier": "semantic", "similarity": round(self.similarity, 3), "key": self.key}


# ---- startup ----------------------------------------------------------------------------------------
def _table_path() -> Path | None:
    if settings.no_siis_table_path:
        return Path(settings.no_siis_table_path)
    for path in (Path(settings.data_dir) / "results.jsonl", Path(settings.data_dir).parent / "results.jsonl"):
        if path.exists():
            return path
    return None


def prewarm() -> dict:
    """Load the kit table and the kit articles. Safe to call twice; missing files only shrink it."""
    global _loaded, _table_matrix
    from app.pipeline.normalize import clean_siis, display_query, normalize_query, siis_title
    from app.pipeline.slots import extract_slots

    with _lock:
        if _loaded:
            return {"table_plans": len({k for k, _, _ in _table_rows}), "articles": len(_articles)}
        _loaded = True
    rows, texts = [], []
    path = _table_path()
    if path is not None and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            contexts = (record.get("response") or {}).get("contexts") or []
            if not contexts:
                continue
            norm = normalize_query(record["query"])
            key, slots = make_key(norm, None), extract_slots(norm)
            for text in dict.fromkeys(
                [display_query(record["query"]), *(record.get("query_variations") or [])]
            ):
                if text.strip():
                    rows.append((key, slots, {"contexts": contexts}))
                    texts.append(text)
    matrix = np.asarray(dense.embed([correct_typos(t) for t in texts]), dtype=np.float32) if texts else None
    with _lock:
        _table_rows[:] = rows
        _table_matrix = matrix
    kit = Path(settings.data_dir) / "kit" / "siis_responses.json"
    if kit.exists():
        for record in json.loads(kit.read_text(encoding="utf-8")).get("responses", []):
            clean, digest = clean_siis(record.get("siis_response"))
            if clean:
                remember_article(clean, digest, siis_title(record.get("siis_response")) or "")
    return {"table_plans": len({k for k, _, _ in rows}), "articles": len(_articles)}


def _ensure() -> None:
    if not _loaded:
        prewarm()


# ---- 1. plans -----------------------------------------------------------------------------------------
def lookup_no_siis(norm_query: str, slots: Slots | None = None) -> NoSiisHit | None:
    """The closest plan for a complaint with no article: kit table or any solved plan, strict + guarded."""
    from app.pipeline.slots import extract_slots

    _ensure()
    slots = slots or extract_slots(norm_query)
    threshold = settings.no_siis_plan_threshold
    best: NoSiisHit | None = None
    with _lock:
        matrix, rows = _table_matrix, list(_table_rows)
    if matrix is not None and rows:
        sims = matrix @ np.asarray(dense.embed([correct_typos(norm_query)])[0], dtype=np.float32)
        for position in np.argsort(-sims):
            score = float(sims[position])
            if score < threshold:
                break
            key, row_slots, plan = rows[position]
            if compatible(slots, row_slots):
                best = NoSiisHit(plan, key, score, "kit_table")
                break
    found = semantic.lookup_any_article(norm_query, slots, threshold)
    if found is not None and (best is None or found[2] > best.similarity):
        best = NoSiisHit(found[0], found[1], found[2], "cache")
    return best


# ---- 2. articles --------------------------------------------------------------------------------------
def remember_article(siis_clean: str, siis_hash: str | None, title: str = "") -> None:
    """Index an article the API received (title + each section), once per hash; bounded, oldest out."""
    from app.pipeline.segment import _section_text, split_sections

    if not siis_clean or not siis_hash:
        return
    with _lock:
        if siis_hash in _articles:
            _articles.move_to_end(siis_hash)
            return
    texts = [t for t in [title] + [_section_text(s) for s in split_sections(siis_clean)] if t.strip()]
    if not texts:
        return
    vectors = np.asarray(dense.embed(texts), dtype=np.float32)  # outside the lock: the slow part
    with _lock:
        _articles[siis_hash] = (title, siis_clean, vectors)
        while len(_articles) > settings.article_memory_max:
            _articles.popitem(last=False)


def remember_article_later(siis_clean: str, siis_hash: str | None, title: str = "") -> None:
    """remember_article() off the request path: a new article costs ~20 ms of embedding."""
    with _lock:
        if not siis_hash or siis_hash in _articles:
            return
    _background.submit(remember_article, siis_clean, siis_hash, title)


def find_article(norm_query: str) -> RetrievedArticle | None:
    """The remembered article clearly closest to the complaint, or None when none is close or two tie."""
    _ensure()
    with _lock:
        articles = list(_articles.items())
    if not articles:
        return None
    query = np.asarray(dense.embed([correct_typos(norm_query)])[0], dtype=np.float32)
    scored = sorted(
        ((float(np.max(vectors @ query)), h, title, text) for h, (title, text, vectors) in articles),
        reverse=True,
    )
    best, digest, title, text = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best < settings.article_match_threshold or best - runner_up < settings.article_match_margin:
        return None
    return RetrievedArticle(text=text, siis_hash=digest, title=title, similarity=best)


def forget_all() -> None:
    """Tests only: drop the table and the remembered articles; the next call re-loads the kit."""
    global _loaded, _table_matrix
    with _lock:
        _loaded = False
        _table_rows.clear()
        _table_matrix = None
        _articles.clear()
