"""ADR-002: a ladder of models per stage, the first two raced on extraction; tokens, cost and quota.

On the free tier (settings, engine section) call B races Ministral 14B against 8B and call C runs on
14B then 8B: every model that writes an answer is Mistral's open-weight Ministral. The background
variations call runs on Gemini 3.1 Flash-Lite, then 8B. Each stage's ladder (llm/registry.py) can be
set from the environment for a bake-off (eval/results/bakeoff/SUMMARY.md is the one behind these).

    complete_json("extract", {"query": ..., "sentences": ...}, SCHEMA, stage="extract", info=info)

Per stage (settings): the ladder, a timeout for the first rung inside the stage budget, and the
output budget. Rungs with no key, on cooldown, or over their free-plan quota are skipped without a
call (recorded as attempts, 0 ms).

    race      extraction starts its first two usable rungs together; the first rung's answer wins if it
              is back (and usable) by the prefer deadline, otherwise the first usable answer is taken
    walk      the remaining rungs are tried in order while settings.llm_min_fallback_s of budget is left
    accept    a caller check on the parsed JSON; a rejected answer counts as a failure
    cooldown  a model that answers 429/5xx is skipped for settings.llm_cooldown_s (longer when the 429
              says so, up to llm_cooldown_max_s), so a busy free tier costs one wasted call, not many
    quota     llm/quota.py: requests and tokens per model over the last minute, and what the provider's
              headers said was left, against settings.llm_requests_per_minute / llm_tokens_per_minute

If nothing usable comes back the caller gets LLMError and degrades (template variations, rules-only
extraction). Prompts are versioned files, prompts/<name>.<version>.md with {{placeholders}}; versions
come from settings.prompt_versions, and settings.prompt_version is part of the cache key.
"""

import math
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from app.config import settings
from app.llm import gemini, openai_compat, quota, registry
from app.llm.errors import LLMCallError

PROMPTS = Path(__file__).resolve().parent / "prompts"
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_COOLDOWN_KINDS = ("http_429", "http_5")  # capacity errors; a slow answer (timeout) is not one
_cooldown_until: dict[str, float] = {}
_cooldown_lock = threading.Lock()

# Local runs read the repo-root .env; in Docker the variables come from the environment itself.
load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)


class LLMError(Exception):
    """No provider produced a usable answer for this call; `attempts` says how each one failed."""

    def __init__(self, attempts: list[dict]):
        super().__init__("; ".join(f"{a['model']}: {a['error']}" for a in attempts) or "no provider")
        self.attempts = attempts


def available() -> bool:
    """True when a model on some stage's ladder has its key: the LLM path is live, rules are only a
    fallback. A key for a provider no ladder uses changes nothing."""
    return any(registry.has_key(m) for stage in registry.stages() for m in registry.ladder(stage))


def status() -> dict:
    """For /v1/metrics: which providers have a key (never the key), each stage's ladder, what each rung
    has used of its quota in the last minute and whether it is cooling down."""
    models = {m for stage in registry.stages() for m in registry.ladder(stage)}
    return {
        "providers": {p: registry.has_key(f"{p}:x") for p in [registry.GEMINI, *settings.llm_providers]},
        "ladders": {stage: registry.ladder(stage) for stage in registry.stages()},
        "models": {
            m: {
                "requests_last_minute": quota.calls_last_minute(m),
                "tokens_last_minute": round(quota.tokens_last_minute(m)),
                "cooling_down": cooling_down(m),
            }
            for m in sorted(models)
        },
    }


@lru_cache(maxsize=16)
def load_prompt(name: str, version: str) -> str:
    return (PROMPTS / f"{name}.{version}.md").read_text(encoding="utf-8")


def prompt_version(name: str) -> str:
    """The version of prompts/<name>.<version>.md to load. Call B's prompt follows extract_mode:
    rewrite mode asks for rewritten steps, so it must not get the select-mode prompt."""
    if name == "extract" and settings.extract_mode == "rewrite":
        name = "extract_rewrite"
    return settings.prompt_versions.get(name, settings.prompt_version)


def render(template: str, variables: dict) -> str:
    return _PLACEHOLDER.sub(lambda m: str(variables.get(m.group(1), m.group(0))), template)


class _Stage:
    """What one call may use: its ladder, thinking level, timeouts and output budget."""

    def __init__(self, stage: str):
        self.stage = stage
        self.prefer_deadline: float | None = None
        self.race_width = 1  # rungs started together (extraction races two)
        self.cools = True  # a 429/5xx here puts the model on cooldown for every stage
        self.keep_requests = 0  # per-minute requests of each rung this stage leaves to the others
        ladder = registry.ladder(stage)
        if stage == "coverage":
            # An extra, never the answer. Its last resort is its own fast model, never the slow last
            # resort, and it moves off a rung before that rung's per-minute request limit is near: the
            # primary is kept for call B (settings.coverage_leave_primary).
            self.thinking = None
            self.keep_requests = settings.coverage_leave_primary
            near_limit = [m for m in ladder[:-1] if quota.over(m, 0, keep_requests=self.keep_requests)]
            ladder = [m for m in ladder if m not in near_limit]
            self.primary_timeout = self.budget = settings.coverage_budget_s
            self.max_tokens = settings.coverage_max_tokens
            self.cools = False  # its 429 must not cost call B the primary for a minute
            # The pipeline waits ~4.5 s for it. 14B alone stalled to its 6 s timeout on 3 of 6 direct calls
            # (2026-09-29) with 8B answering in ~2 s every time, and a stalled call C meant no verdict on
            # whether the article fits the complaint. Racing adds no 14B request (call C makes it anyway).
            self.race_width = 2 if len(ladder) > 1 else 1
            self.prefer_deadline = settings.coverage_prefer_deadline_s
        elif stage == "enrich":
            self.thinking = settings.enrich_thinking
            self.primary_timeout, self.budget = settings.enrich_primary_timeout_s, settings.enrich_budget_s
            self.max_tokens = settings.enrich_max_tokens
        elif stage == "variations":
            self.thinking = None
            self.primary_timeout = self.budget = settings.variations_budget_s
            self.max_tokens = settings.variations_max_tokens
        else:
            self.thinking = settings.extract_thinking
            self.primary_timeout, self.budget = settings.extract_primary_timeout_s, settings.extract_budget_s
            self.max_tokens = settings.extract_max_tokens
            self.race_width = 2
            self.prefer_deadline = settings.extract_prefer_deadline_s
        self.ladder = ladder
        self.model = ladder[0] if ladder else None
        self.fast_model = ladder[1] if self.race_width > 1 and len(ladder) > 1 else None


def provider(model: str) -> str:
    """gemini-* goes to Google, a "provider:" prefix to that provider, every other id to Mistral."""
    return registry.parse(model)[0]


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = settings.llm_prices.get(model, (0.0, 0.0))
    return round((tokens_in * price_in + tokens_out * price_out) / 1_000_000, 6)


def cooling_down(model: str) -> bool:
    with _cooldown_lock:
        return _cooldown_until.get(model, 0.0) > time.monotonic()


def _cool(model: str, kind: str, retry_after: str | None = None) -> None:
    if not kind.startswith(_COOLDOWN_KINDS):
        return
    seconds = settings.llm_cooldown_s
    asked = quota.parse_reset(retry_after) if kind == "http_429" else None
    if asked:
        seconds = min(max(seconds, asked), settings.llm_cooldown_max_s)
    with _cooldown_lock:
        _cooldown_until[model] = time.monotonic() + seconds


def _count_call(model: str, tokens: float = 0.0) -> list[float]:
    return quota.reserve(model, tokens)


def calls_last_minute(model: str) -> int:
    return quota.calls_last_minute(model)


def reset_cooldowns() -> None:
    with _cooldown_lock:
        _cooldown_until.clear()
    quota.reset()


def _call(model: str, prompt: str, schema: dict, plan: _Stage, timeout: float, clients: dict) -> dict:
    name = provider(model)
    if name == registry.GEMINI:
        return gemini.call(
            prompt,
            schema,
            model=model,
            thinking=plan.thinking,
            timeout=timeout,
            max_tokens=plan.max_tokens,
            client=clients.get(registry.GEMINI),
        )
    return openai_compat.call(
        model, prompt, schema, timeout=timeout, max_tokens=plan.max_tokens, client=clients.get(name)
    )


def complete_json(
    prompt_name: str,
    variables: dict,
    schema: dict,
    *,
    stage: str | None = None,
    info: dict | None = None,
    clients: dict | None = None,
    accept: Callable[[dict], bool] | None = None,
) -> dict:
    """The parsed JSON answer. Fills `info` with model, tokens, cost and the attempts made.

    `clients` (provider name -> httpx.Client, e.g. {"gemini": ..., "mistral": ...}) is for tests and
    pooling.
    """
    plan = _Stage(stage or prompt_name)
    version = prompt_version(prompt_name)
    prompt = render(load_prompt(prompt_name, version), variables)
    clients = clients or {}
    started = time.perf_counter()
    attempts: list[dict] = []
    lock = threading.Lock()
    prompt_tokens = math.ceil(len(prompt) / settings.llm_chars_per_token)
    expected_out = settings.llm_expected_output_tokens.get(plan.stage, 0)

    def estimate(model: str) -> float:
        reasoning = settings.llm_model_options.get(model, {}).get("expected_reasoning_tokens", 0)
        return prompt_tokens + expected_out + reasoning

    def skip_reason(model: str) -> str | None:
        if not registry.known(provider(model)):
            return "unknown_provider"
        if not registry.has_key(model):
            return "no_key"
        if cooling_down(model):
            return "cooldown"
        if quota.over(model, estimate(model), keep_requests=plan.keep_requests):
            return "quota"
        return None

    def attempt(model: str, timeout: float) -> dict | None:
        t = time.perf_counter()
        error, result = skip_reason(model), None  # checked again: another request may have used it up
        if error is None:
            entry = quota.reserve(model, estimate(model))
            try:
                result = _call(model, prompt, schema, plan, timeout, clients)
                quota.settle(entry, result["tokens_in"] + result["tokens_out"])
                quota.note_headers(model, result.get("ratelimit"))
                if accept is not None and not accept(result["json"]):
                    error, result = "rejected", None
            except LLMCallError as exc:
                error = exc.kind
                if exc.kind.startswith("http_4"):
                    quota.settle(entry, 0)  # refused before any generation
                quota.note_headers(model, exc.ratelimit)
                if plan.cools:
                    _cool(model, exc.kind, exc.retry_after)
        with lock:
            attempts.append({"model": model, "ok": error is None, "error": error, "ms": _ms(t)})
        return result

    usable = []
    for model in plan.ladder:
        reason = skip_reason(model)
        if reason is None:
            usable.append(model)
        else:
            attempts.append({"model": model, "ok": False, "error": reason, "ms": 0.0})

    def first_timeout(model: str) -> float:
        # The configured first rung gets its own timeout inside the budget; a rung standing in for a
        # skipped one gets what is left of the budget.
        remaining = plan.budget - (time.perf_counter() - started)
        return min(plan.primary_timeout, remaining) if model == plan.model else remaining

    result, rest = None, usable[1:]
    if plan.race_width > 1 and len(usable) > 1:
        result, rest = _race(plan, attempt, started, usable[0], usable[1]), usable[2:]
    elif usable:
        result = attempt(usable[0], first_timeout(usable[0]))
    for model in rest:
        remaining = plan.budget - (time.perf_counter() - started)
        if result is not None or remaining < settings.llm_min_fallback_s:
            break
        result = attempt(model, remaining)
    if info is not None:
        with lock:
            info["attempts"] = list(attempts)
        info["prompt"] = f"{prompt_name}.{version}"
    if result is None:
        with lock:
            raise LLMError(list(attempts))
    if info is not None:
        info.update(
            model=result["model"],
            tokens_in=result["tokens_in"],
            tokens_out=result["tokens_out"],
            cost_usd=cost_usd(result["model"], result["tokens_in"], result["tokens_out"]),
            fallback_used=result["model"] not in plan.ladder[: plan.race_width],
        )
    return result["json"]


def _race(
    plan: _Stage, attempt: Callable[[str, float], dict | None], started: float, first: str, second: str
) -> dict | None:
    """Two rungs together; the first wins if it is usable by the prefer deadline."""
    timeout = min(plan.primary_timeout, plan.budget)
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-race")
    primary: Future = pool.submit(attempt, first, timeout)
    fast: Future = pool.submit(attempt, second, timeout)
    pool.shutdown(wait=False)  # a losing call finishes on its own, bounded by its timeout
    prefer_until = started + (plan.prefer_deadline or plan.budget)
    give_up_at = started + plan.budget
    while True:
        if primary.done() and primary.result() is not None:
            return primary.result()
        fast_ok = fast.done() and fast.result() is not None
        primary_failed = primary.done() and primary.result() is None
        now = time.perf_counter()
        if fast_ok and (now >= prefer_until or primary_failed):
            return fast.result()
        if primary.done() and fast.done():
            return None
        if now >= give_up_at:
            return None  # stage budget spent; whatever is still running is ignored
        until = prefer_until if fast_ok else give_up_at
        pending = [f for f in (primary, fast) if not f.done()]
        wait(pending, timeout=max(0.01, until - now), return_when=FIRST_COMPLETED)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
