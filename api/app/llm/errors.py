"""The one error type the LLM clients raise, and a tolerant JSON parser for their answers."""

import json
import re

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class LLMCallError(Exception):
    """A provider call that produced no usable JSON. `kind` drives the router's fallback decision."""

    def __init__(
        self,
        kind: str,
        message: str = "",
        retry_after: str | None = None,
        ratelimit: dict | None = None,
    ):
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind  # timeout | transport | http_429 | http_5xx | no_key | empty | bad_json | ...
        self.retry_after = retry_after
        self.ratelimit = ratelimit or {}  # what the provider's headers said was left (llm/quota.py)


def parse_json(text: str) -> dict:
    """The JSON object in a model answer; code fences and leading chatter are tolerated."""
    body = _FENCE.sub("", text.strip())
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            raise LLMCallError("bad_json", body[:200]) from None
        try:
            value = json.loads(body[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMCallError("bad_json", str(exc)) from exc
    if not isinstance(value, dict):
        raise LLMCallError("bad_json", "answer is not a JSON object")
    return value
