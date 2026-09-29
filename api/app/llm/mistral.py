"""Mistral client (JSON output): the "mistral" provider of llm/openai_compat.py.

On the free tier this serves call B (extract, Ministral 14B raced against 8B), call C (coverage) and
the background variations call; the Ministral and Mistral Small models are open-weight. Kept as its own
module so callers and tests can name Mistral directly.
"""

import httpx

from app.config import settings
from app.llm import openai_compat

PROVIDER = "mistral"
API = settings.llm_providers[PROVIDER]["url"]
KEY_ENV = settings.llm_providers[PROVIDER]["key_env"]


def available() -> bool:
    return openai_compat.available(PROVIDER)


def call(
    prompt: str,
    schema: dict,
    *,
    model: str | None = None,
    timeout: float | None = None,
    max_tokens: int = 4096,
    client: httpx.Client | None = None,
) -> dict:
    """{"json": parsed answer, "model", "tokens_in", "tokens_out", "ratelimit"}. Raises LLMCallError."""
    model = model or settings.extract_model  # a Ministral model; the router always passes one
    return openai_compat.call(model, prompt, schema, timeout=timeout, max_tokens=max_tokens, client=client)
