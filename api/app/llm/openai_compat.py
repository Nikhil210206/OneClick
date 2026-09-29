"""The client for OpenAI-compatible providers (today Mistral): chat completions with a strict
JSON-schema response format.

Plain REST over httpx. The provider (URL, key variable, temperature, rate-limit header names) comes
from settings.llm_providers, per-model options (reasoning_effort, extra max_tokens) from
settings.llm_model_options. Raises LLMCallError on anything that is not a usable JSON answer, carrying
what the provider's headers said was left of its limits.
"""

import os

import httpx

from app.config import settings
from app.llm.errors import LLMCallError, parse_json
from app.llm.quota import parse_reset
from app.llm.registry import parse


def available(provider: str) -> bool:
    spec = settings.llm_providers.get(provider) or {}
    return bool(spec.get("key_env") and os.getenv(spec["key_env"]))


def _options(model_id: str) -> dict:
    return settings.llm_model_options.get(model_id, {})


def _body(model_id: str, prompt: str, schema: dict, max_tokens: int, optional: bool) -> dict:
    provider, name = parse(model_id)
    spec = settings.llm_providers[provider]
    temperature = spec.get("temperature")
    options = _options(model_id)
    body: dict = {
        "model": name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": settings.llm_temperature_mistral if temperature is None else temperature,
        "max_tokens": max_tokens + int(options.get("max_tokens_extra", 0)),
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": schema, "strict": True},
        },
    }
    reasoning = options.get("reasoning_effort", settings.fallback_reasoning if provider == "mistral" else "")
    if optional and reasoning:
        body["reasoning_effort"] = reasoning
    return body


def _text(content) -> str:
    """Message content is a string, or a list of chunks when the model reasons first."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except ValueError:
        return None


def ratelimit(provider: str, headers: httpx.Headers) -> dict:
    """What the provider's headers say is left: remaining requests / tokens and seconds to each reset."""
    spec = settings.llm_providers.get(provider) or {}

    def header(name: str) -> str | None:
        return headers.get(spec[name]) if spec.get(name) else None

    return {
        "remaining_requests": _int(header("remaining_requests_header")),
        "reset_requests_s": parse_reset(header("reset_requests_header")),
        "remaining_tokens": _int(header("remaining_tokens_header")),
        "reset_tokens_s": parse_reset(header("reset_tokens_header")),
    }


def call(
    model_id: str,
    prompt: str,
    schema: dict,
    *,
    timeout: float | None = None,
    max_tokens: int = 4096,
    client: httpx.Client | None = None,
) -> dict:
    """{"json", "model", "tokens_in", "tokens_out", "ratelimit"}. Raises LLMCallError."""
    provider, _ = parse(model_id)
    spec = settings.llm_providers.get(provider)
    if spec is None:
        raise LLMCallError("unknown_provider", f"{provider!r} is not in settings.llm_providers")
    key = os.getenv(spec["key_env"])
    if not key:
        raise LLMCallError("no_key", f"{spec['key_env']} is not set")
    http = client or httpx.Client()
    try:
        response = None
        # A 400 on the optional knob (reasoning_effort) must not cost the answer: retry once without.
        # Without the knob the retry would resend the same body, so a 400 is final.
        for optional in (True, False):
            body = _body(model_id, prompt, schema, max_tokens, optional)
            response = http.post(
                spec["url"],
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
                timeout=timeout or settings.llm_timeout_default_s,
            )
            if response.status_code != 400 or "reasoning_effort" not in body:
                break
    except httpx.TimeoutException as exc:
        raise LLMCallError("timeout", str(exc)) from exc
    except httpx.HTTPError as exc:
        raise LLMCallError("transport", str(exc)) from exc
    finally:
        if client is None:
            http.close()
    limits = ratelimit(provider, response.headers)
    if response.status_code != 200:
        raise LLMCallError(
            f"http_{response.status_code}",
            response.text[:300],
            response.headers.get("retry-after"),
            ratelimit=limits,
        )
    data = response.json()
    choices = data.get("choices") or []
    text = _text((choices[0].get("message") or {}).get("content")) if choices else ""
    if not text:
        raise LLMCallError("empty", "no text in the answer", ratelimit=limits)
    usage = data.get("usage") or {}
    return {
        "json": parse_json(text),
        "model": model_id,
        "tokens_in": int(usage.get("prompt_tokens") or 0),
        "tokens_out": int(usage.get("completion_tokens") or 0),
        "ratelimit": limits,
    }
