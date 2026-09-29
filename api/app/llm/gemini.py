"""Gemini client (JSON-schema output).

Plain REST over httpx (models.generateContent): exact timeouts, no SDK. Returns the parsed JSON plus
token usage; raises LLMCallError on anything that is not a usable JSON answer, so the router can fall
back. Thinking is set per call (Gemini 3: thinkingConfig.thinkingLevel).
"""

import os

import httpx

from app.config import settings
from app.llm.errors import LLMCallError, parse_json

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
KEY_ENV = "GEMINI_API_KEY"


def available() -> bool:
    return bool(os.getenv(KEY_ENV))


def _body(prompt: str, schema: dict, thinking: str | None, max_tokens: int, optional: bool) -> dict:
    config: dict = {
        "responseMimeType": "application/json",
        "responseJsonSchema": schema,
        "temperature": settings.llm_temperature_gemini,
        "maxOutputTokens": max_tokens,
    }
    if optional and thinking:
        config["thinkingConfig"] = {"thinkingLevel": thinking}
    return {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}


def call(
    prompt: str,
    schema: dict,
    *,
    model: str | None = None,
    thinking: str | None = None,
    timeout: float | None = None,
    max_tokens: int = 4096,
    client: httpx.Client | None = None,
) -> dict:
    """{"json": parsed answer, "model", "tokens_in", "tokens_out"}. Raises LLMCallError."""
    key = os.getenv(KEY_ENV)
    if not key:
        raise LLMCallError("no_key", f"{KEY_ENV} is not set")
    if not model:
        raise LLMCallError("no_model", "a Gemini model id is required")  # the router always passes one
    name = model.removeprefix("gemini:")  # an explicit "gemini:" prefix (llm/registry.py) is not sent
    http = client or httpx.Client()
    try:
        response = None
        # A 400 on the optional knobs (thinking level) must not cost the answer: retry once without.
        for optional in (True, False):
            response = http.post(
                API.format(model=name),
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=_body(prompt, schema, thinking, max_tokens, optional),
                timeout=timeout or settings.llm_timeout_default_s,
            )
            if response.status_code != 400 or not thinking:
                break
    except httpx.TimeoutException as exc:
        raise LLMCallError("timeout", str(exc)) from exc
    except httpx.HTTPError as exc:
        raise LLMCallError("transport", str(exc)) from exc
    finally:
        if client is None:
            http.close()
    if response.status_code != 200:
        raise LLMCallError(
            f"http_{response.status_code}", response.text[:300], response.headers.get("retry-after")
        )
    data = response.json()
    candidates = data.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") if candidates else None
    text = "".join(p.get("text", "") for p in parts or [] if not p.get("thought"))
    if not text:
        reason = candidates[0].get("finishReason") if candidates else "no_candidates"
        raise LLMCallError("empty", f"no text in the answer ({reason})")
    usage = data.get("usageMetadata") or {}
    return {
        "json": parse_json(text),
        "model": model,
        "tokens_in": int(usage.get("promptTokenCount") or 0),
        # thinking tokens are billed as output
        "tokens_out": int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0),
    }
