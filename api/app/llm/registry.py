"""ADR-002: model ids -> providers, and each stage's model ladder.

A bare id keeps the original rule: gemini-* goes to Google, everything else (ministral-, mistral-,
magistral-) to Mistral. "provider:model" names a provider in settings.llm_providers or "gemini"
explicitly, e.g. "mistral:ministral-8b-latest". Mistral and Gemini model names never contain ":", so
any ":" is a provider prefix, known or not; an unknown one is a failed attempt in the router, never an
exception.
"""

import os

from app.config import settings

GEMINI = "gemini"
_GEMINI_KEY_ENV = "GEMINI_API_KEY"  # llm/gemini.py reads the same variable


def parse(model_id: str) -> tuple[str, str]:
    """(provider, the provider's own model name)."""
    prefix, sep, rest = model_id.partition(":")
    if sep:
        return prefix, rest
    return (GEMINI if model_id.startswith("gemini") else "mistral"), model_id


def known(provider: str) -> bool:
    return provider == GEMINI or provider in settings.llm_providers


def key_env(provider: str) -> str | None:
    if provider == GEMINI:
        return _GEMINI_KEY_ENV
    spec = settings.llm_providers.get(provider)
    return spec.get("key_env") if spec else None


def has_key(model_id: str) -> bool:
    env = key_env(parse(model_id)[0])
    return bool(env and os.getenv(env))


def ladder(stage: str) -> list[str]:
    """The stage's models, quality first, without repeats: its list in settings, or the single-model
    keys, then fallback_model (every stage but coverage, whose last resort is its own fast model)."""
    if stage == "coverage":
        models = settings.coverage_models or [settings.coverage_model, settings.coverage_fallback_model]
        return _unique(models)
    if stage == "enrich":
        models = settings.enrich_models or [settings.enrich_model]
    elif stage == "variations":
        models = settings.variations_models or [settings.variations_model]
    else:
        models = settings.extract_models or [settings.extract_model, settings.extract_fast_model]
    return _unique([*models, settings.fallback_model])


def stages() -> list[str]:
    """The stages that call a model on a request (enrich only when it sits on the critical path)."""
    return ["extract", "coverage", "variations"] + (
        ["enrich"] if settings.enrich_llm_on_critical_path else []
    )


def _unique(models: list[str]) -> list[str]:
    return list(dict.fromkeys(m for m in models if m))
