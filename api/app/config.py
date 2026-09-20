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
    cache_sim_threshold: float = 0.85
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


settings = Settings()
