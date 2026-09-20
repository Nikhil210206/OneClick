"""Central settings. Every threshold and budget lives here, never hard-coded in modules."""

from pydantic import BaseModel


class Settings(BaseModel):
    # LLM (design doc: ADR-002)
    primary_model: str = "gemini-flash"
    fallback_model: str = "mistral-small"
    llm_timeout_s: float = 3.0
    prompt_version: str = "v1"

    # Stage budgets in seconds (design doc: Reliability)
    enrich_budget_s: float = 2.5
    extract_budget_s: float = 3.5
    retrieval_budget_s: float = 0.5

    # Cache (ADR-004)
    # 0.80 measured on data/gold/cache_paraphrases.jsonl (scripts/eval_cache.py --sweep):
    # 0.85 hit only 63% of held-out paraphrases (A3 needs >= 80%), and below 0.80 a paraphrase
    # starts matching the wrong cached plan. 0.80 gave 90% with zero wrong plans or false hits.
    cache_sim_threshold: float = 0.80
    sqlite_path: str = "cache.sqlite"

    # Grounding (component 6)
    grounding_cos_threshold: float = 0.75

    # Query variations (component 3)
    variation_min: int = 8
    variation_max: int = 10
    variation_jaccard_max: float = 0.6
    variation_meaning_min_cos: float = 0.6

    # Response (ADR-006)
    include_meta: bool = True

    # Console stream (Vishaal): replays data/fixtures until pipeline.run.run_stream is implemented
    stream_mock: bool = True
    stream_mock_time_scale: float = 1.0  # 1.0 = recorded stage timings; 0 = no delay (tests)
    stream_fixtures_dir: str | None = None  # None = <repo>/data/fixtures (not in the Docker image)
    # Retrieval (Karur, component 7): hybrid search over catalog entries / Screen Graph nodes
    embed_model: str = "BAAI/bge-small-en-v1.5"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    retrieval_top_k: int = 20  # candidates each searcher returns before fusion
    rerank_candidates: int = 10  # candidates sent to the cross-encoder (CPU cost is linear)
    rerank_top_k: int = 3  # candidates kept after the cross-encoder
    leaf_weight: float = 0.6  # weight of the leaf-screen name match ("Dark mode" in the path)
    polarity_bonus: float = 0.25  # entry type matches the step verb (enable -> onURL)
    polarity_penalty: float = 0.35  # entry is the opposite toggle (enable step -> offURL entry)
    offsurface_penalty: float = 0.3  # entry belongs to TV Settings / Members, not device Settings
    # Cross-encoder rerank. Off by default: on the Screen Graph it costs ~250 ms per step and
    # lowered recall@3 on article-worded steps (100% -> 88%), because grouping buttons into
    # screens already removed the near-ties it used to break. Kept for re-measuring later.
    use_rerank: bool = False
    # Adaptive rerank thresholds, used when use_rerank is on
    rerank_confident_score: float = 1.60  # top score at or above this: accept without reranking
    rerank_min_margin: float = 0.10  # top two closer than this: rerank to break the tie
    # Resolver tiers (component 7). Scores come from retrieval.search: fusion (<=1.0) plus the
    # leaf-name match and polarity bonus, so a confident screen sits near the top of the range.
    link_catalog_min_score: float = 1.60  # below this no catalog entry is trusted
    link_max_score: float = 1.85  # full marks, used to normalise confidence to 0-1
    rrf_k: int = 60  # reciprocal rank fusion constant
    # BM25 field weights: a field's tokens are repeated this many times in the indexed document
    bm25_field_weights: dict[str, int] = {
        "message": 2,
        "qna_description": 2,
        "clean_description": 1,
    }


settings = Settings()
