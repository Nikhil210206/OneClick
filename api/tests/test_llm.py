"""LLM router (ADR-002) and the LLM stages, against a stubbed HTTP transport: no quota is spent.

Covers the fallback matrix (timeout, 429, bad JSON, missing key, both down), the quality/fast race,
answer rejection, cooldowns, cost accounting, and what the pipeline does with an LLM answer: select
mode turns sentence ids into the article's own steps, invented ids are dropped, background variations
reach the cache, and a failed LLM run is capped at the rules-only score (and, cached like any valid
answer, repeats identically).
"""

import json
import time
from contextlib import ExitStack
from pathlib import Path

import httpx
import pytest

from app import cache
from app.config import Settings, settings
from app.llm import openai_compat, quota, registry, router
from app.llm.errors import LLMCallError, parse_json
from app.models import Intent, SiisSentence, Slots
from app.pipeline import capacity
from app.pipeline import enrich as enrich_module
from app.pipeline import extract as extract_module
from app.pipeline.run import run_stream, run_with_variations

ROOT = Path(__file__).resolve().parents[2]
REQUEST = json.loads((ROOT / "data/fixtures/touch_lag/request.json").read_text(encoding="utf-8"))
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _gemini_ok(payload: dict, tokens=(100, 50, 20)):
    body = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}, "finishReason": "STOP"}],
        "usageMetadata": {
            "promptTokenCount": tokens[0],
            "candidatesTokenCount": tokens[1],
            "thoughtsTokenCount": tokens[2],
        },
    }
    return httpx.Response(200, json=body)


def _mistral_ok(payload: dict):
    body = {
        "choices": [{"message": {"content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 80, "completion_tokens": 40},
    }
    return httpx.Response(200, json=body)


def _clients(gemini_handler, mistral_handler):
    return {
        "gemini": httpx.Client(transport=httpx.MockTransport(gemini_handler)),
        "mistral": httpx.Client(transport=httpx.MockTransport(mistral_handler)),
    }


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral")
    router.reset_cooldowns()
    yield
    router.reset_cooldowns()


@pytest.fixture
def gemini_first(monkeypatch, keys):
    """The paid-tier shape: Gemini primary for call A, Mistral fallback."""
    monkeypatch.setattr(settings, "enrich_model", "gemini-3.1-flash-lite")
    monkeypatch.setattr(settings, "fallback_model", "mistral-small-2603")


def _complete(clients, info, **kwargs):
    return router.complete_json(
        "enrich", {"query": "screen is black"}, SCHEMA, stage="enrich", info=info, clients=clients, **kwargs
    )


# ---- router: fallback matrix ------------------------------------------------------------------------
def test_primary_answers_and_cost_counts_thinking_tokens(gemini_first):
    seen = {}

    def gemini(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return _gemini_ok({"ok": True})

    info = {}
    assert _complete(_clients(gemini, lambda r: pytest.fail("no fallback")), info) == {"ok": True}
    assert settings.enrich_model in seen["url"]
    assert seen["body"]["generationConfig"]["responseJsonSchema"] == SCHEMA
    assert seen["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": settings.enrich_thinking}
    assert "screen is black" in seen["body"]["contents"][0]["parts"][0]["text"]
    assert info["model"] == settings.enrich_model and info["tokens_in"] == 100 and info["tokens_out"] == 70
    price_in, price_out = settings.llm_prices[settings.enrich_model]
    assert info["cost_usd"] == pytest.approx((100 * price_in + 70 * price_out) / 1e6)
    assert info["fallback_used"] is False


@pytest.mark.parametrize(
    "failure",
    [
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)),
        lambda r: httpx.Response(429, headers={"retry-after": "7"}, text="quota"),
        lambda r: httpx.Response(503, text="down"),
        lambda r: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}),
    ],
    ids=["timeout", "429", "503", "bad_json"],
)
def test_any_primary_failure_falls_back(gemini_first, failure):
    info = {}
    assert _complete(_clients(failure, lambda r: _mistral_ok({"ok": True})), info) == {"ok": True}
    assert info["model"] == settings.fallback_model and info["fallback_used"] is True
    assert [a["ok"] for a in info["attempts"]] == [False, True]


def test_capacity_errors_put_a_model_on_cooldown_but_timeouts_do_not(gemini_first):
    calls = []

    def gemini(request):
        calls.append(1)
        return httpx.Response(503, text="busy")

    clients = _clients(gemini, lambda r: _mistral_ok({"ok": True}))
    _complete(clients, {})
    info = {}
    _complete(clients, info)  # second request: Gemini is skipped, no call made
    assert len(calls) == 1 and info["attempts"][0]["error"] == "cooldown"
    router.reset_cooldowns()

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    _complete(_clients(slow, lambda r: _mistral_ok({"ok": True})), {})
    assert not router.cooling_down(settings.enrich_model)


def test_rejected_thinking_level_is_retried_without_it(gemini_first):
    bodies = []

    def gemini(request):
        bodies.append(json.loads(request.content))
        if "thinkingConfig" in bodies[-1]["generationConfig"]:
            return httpx.Response(400, text="Unknown name thinkingLevel")
        return _gemini_ok({"ok": True})

    assert _complete(_clients(gemini, lambda r: pytest.fail("no fallback")), {}) == {"ok": True}
    assert len(bodies) == 2


def test_an_answer_the_caller_rejects_counts_as_a_failure(gemini_first):
    info = {}
    clients = _clients(lambda r: _gemini_ok({"ok": False}), lambda r: _mistral_ok({"ok": True}))
    assert _complete(clients, info, accept=lambda a: a["ok"]) == {"ok": True}
    assert info["attempts"][0]["error"] == "rejected"


def test_both_down_raises_llm_error_with_both_attempts(gemini_first):
    with pytest.raises(router.LLMError) as err:
        _complete(_clients(lambda r: httpx.Response(500), lambda r: httpx.Response(500)), {})
    assert [a["error"] for a in err.value.attempts] == ["http_500", "http_500"]


def test_missing_primary_key_uses_the_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral")
    monkeypatch.setattr(settings, "enrich_model", "gemini-3.1-flash-lite")
    monkeypatch.setattr(settings, "fallback_model", "mistral-small-2603")
    router.reset_cooldowns()
    info = {}
    assert _complete(_clients(lambda r: pytest.fail("no call"), lambda r: _mistral_ok({"ok": True})), info)
    assert info["attempts"][0]["error"] == "no_key" and router.available()


def test_provider_is_chosen_by_model_family():
    assert router.provider("gemini-3.8-flash") == "gemini"
    for model in ("mistral-small-2603", "ministral-14b-latest", "magistral-small-latest"):
        assert router.provider(model) == "mistral"


# ---- router: the quality / fast race (extract stage) ------------------------------------------------
def _race_clients(delays: dict[str, float], answers: dict[str, dict]):
    def mistral(request):
        model = json.loads(request.content)["model"]
        time.sleep(delays.get(model, 0.0))
        return _mistral_ok(answers[model])

    return _clients(lambda r: httpx.Response(503), mistral)


@pytest.fixture
def race(monkeypatch, keys):
    monkeypatch.setattr(settings, "extract_model", "ministral-14b-latest")
    monkeypatch.setattr(settings, "extract_fast_model", "ministral-8b-latest")
    monkeypatch.setattr(settings, "extract_prefer_deadline_s", 0.4)
    monkeypatch.setattr(settings, "extract_primary_timeout_s", 2.0)
    monkeypatch.setattr(settings, "extract_budget_s", 2.5)
    monkeypatch.setattr(settings, "fallback_model", "gemini-3-flash-preview")


def _extract(clients, info, accept=None):
    return router.complete_json(
        "extract",
        {"query": "q", "sentences": "s"},
        SCHEMA,
        stage="extract",
        info=info,
        clients=clients,
        accept=accept,
    )


def test_race_keeps_the_quality_answer_when_it_is_back_by_the_deadline(race):
    info = {}
    answers = {"ministral-14b-latest": {"ok": True}, "ministral-8b-latest": {"ok": False}}
    assert _extract(_race_clients({"ministral-14b-latest": 0.1}, answers), info) == {"ok": True}
    assert info["model"] == "ministral-14b-latest"


def test_race_takes_the_fast_answer_when_the_quality_one_is_late(race):
    info = {}
    answers = {"ministral-14b-latest": {"ok": True}, "ministral-8b-latest": {"ok": False}}
    started = time.perf_counter()
    assert _extract(_race_clients({"ministral-14b-latest": 1.5}, answers), info) == {"ok": False}
    assert info["model"] == "ministral-8b-latest" and time.perf_counter() - started < 1.2


def test_race_skips_a_rejected_quality_answer(race):
    info = {}
    answers = {"ministral-14b-latest": {"ok": False}, "ministral-8b-latest": {"ok": True}}
    result = _extract(_race_clients({"ministral-8b-latest": 0.2}, answers), info, accept=lambda a: a["ok"])
    assert result == {"ok": True} and info["model"] == "ministral-8b-latest"


def _coverage(clients, info):
    return router.complete_json(
        "coverage", {"query": "q", "paragraphs": "p"}, SCHEMA, stage="coverage", info=info, clients=clients
    )


@pytest.fixture
def coverage_race(monkeypatch, keys):
    monkeypatch.setattr(settings, "coverage_prefer_deadline_s", 0.4)
    monkeypatch.setattr(settings, "coverage_budget_s", 2.5)


def test_call_c_takes_the_fast_model_when_the_primary_stalls(coverage_race):
    """Measured 2026-09-29: 14B stalled to its 6 s timeout on 3 of 6 direct calls while 8B answered in
    ~2 s. Call C once waited on 14B alone, so a stall meant no answer inside the pipeline's window."""
    info = {}
    answers = {"ministral-14b-latest": {"ok": True}, "ministral-8b-latest": {"ok": False}}
    started = time.perf_counter()
    assert _coverage(_race_clients({"ministral-14b-latest": 1.5}, answers), info) == {"ok": False}
    assert info["model"] == "ministral-8b-latest" and time.perf_counter() - started < 1.2


def test_call_c_keeps_the_primary_answer_when_it_is_back_by_the_deadline(coverage_race):
    info = {}
    answers = {"ministral-14b-latest": {"ok": True}, "ministral-8b-latest": {"ok": False}}
    assert _coverage(_race_clients({"ministral-14b-latest": 0.1}, answers), info) == {"ok": True}
    assert info["model"] == "ministral-14b-latest"


def test_call_c_does_not_race_when_the_primary_is_kept_for_call_b(coverage_race):
    """Near its request limit the primary is left to call B (coverage_leave_primary): one rung, one call."""
    limit = settings.llm_requests_per_minute[settings.coverage_model] - settings.coverage_leave_primary
    for _ in range(limit):
        router._count_call(settings.coverage_model)
    plan = router._Stage("coverage")
    assert plan.race_width == 1 and plan.ladder == [settings.coverage_fallback_model]
    router.reset_cooldowns()


# ---- ladders and quota (llm/registry.py, llm/quota.py) ----------------------------------------------
def test_the_shipped_ladders_write_answers_with_open_weight_mistral_models_only():
    """Bake-off 2026-09-29: call B and call C stay on Ministral; variations run Gemini, then 8B; there
    is no dead last rung (mistral-small-latest is served 0 requests a minute on the free plan)."""
    assert registry.ladder("extract") == ["ministral-14b-latest", "ministral-8b-latest"]
    assert registry.ladder("coverage") == ["ministral-14b-latest", "ministral-8b-latest"]
    assert registry.ladder("variations") == ["gemini-3.1-flash-lite", "ministral-8b-latest"]
    for stage in ("extract", "coverage"):
        assert {registry.parse(m)[0] for m in registry.ladder(stage)} == {"mistral"}


def test_model_ids_name_their_provider():
    assert registry.parse("ministral-14b-latest") == ("mistral", "ministral-14b-latest")
    assert registry.parse("gemini-3.1-flash-lite") == ("gemini", "gemini-3.1-flash-lite")
    assert registry.parse("mistral:ministral-8b-latest") == ("mistral", "ministral-8b-latest")
    assert registry.parse("gemini:some-model") == ("gemini", "some-model")
    assert registry.parse("acme:m") == ("acme", "m") and not registry.known("acme")


def test_ladders_come_from_the_environment_for_a_bake_off(monkeypatch):
    monkeypatch.setenv("ONECLICK_EXTRACT_MODELS", " ministral-8b-latest , mistral:x ,")
    monkeypatch.setenv("ONECLICK_VARIATIONS_MODELS", "")
    fresh = Settings()
    assert fresh.extract_models == ["ministral-8b-latest", "mistral:x"]
    assert fresh.variations_models == ["gemini-3.1-flash-lite", "ministral-8b-latest"]  # empty: default


def test_a_rung_at_its_request_limit_is_skipped_without_a_call(race):
    for _ in range(settings.llm_requests_per_minute["ministral-14b-latest"]):
        router._count_call("ministral-14b-latest")
    called = []

    def mistral(request):
        called.append(json.loads(request.content)["model"])
        return _mistral_ok({"ok": True})

    info = {}
    assert _extract(_clients(lambda r: httpx.Response(503), mistral), info) == {"ok": True}
    assert called == ["ministral-8b-latest"] and info["model"] == "ministral-8b-latest"
    assert {"model": "ministral-14b-latest", "ok": False, "error": "quota", "ms": 0.0} in info["attempts"]


def test_an_unknown_provider_is_a_failed_attempt_not_an_exception(monkeypatch, keys):
    monkeypatch.setattr(settings, "enrich_models", ["acme:m", "ministral-8b-latest"])
    info = {}
    clients = _clients(lambda r: pytest.fail("no Gemini call"), lambda r: _mistral_ok({"ok": True}))
    assert _complete(clients, info) == {"ok": True}
    assert info["attempts"][0]["error"] == "unknown_provider"


def test_token_limits_and_provider_headers_hold_a_model_back(monkeypatch):
    quota.reset()
    monkeypatch.setattr(settings, "llm_tokens_per_minute", {"m": 1000})
    quota.reserve("m", 900)
    assert quota.over("m", 200) and not quota.over("m", 50)
    quota.note_headers("n", {"remaining_requests": 0, "reset_requests_s": 30})
    assert quota.over("n", 1)
    quota.note_headers("o", {"remaining_tokens": 100, "reset_tokens_s": 30})
    assert quota.over("o", 500) and not quota.over("o", 50)
    assert quota.parse_reset("2m59.5s") == pytest.approx(179.5)
    assert quota.parse_reset("250ms") == pytest.approx(0.25) and quota.parse_reset("7") == 7.0
    quota.reset()


def test_mistral_calls_carry_no_reasoning_setting_the_ministral_models_reject():
    body = openai_compat._body("ministral-14b-latest", "p", SCHEMA, 100, True)
    assert "reasoning_effort" not in body and body["response_format"]["json_schema"]["strict"] is True


def test_parse_json_tolerates_fences_and_rejects_non_objects():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": 1} hope that helps') == {"a": 1}
    for bad in ("[1, 2]", "nothing here"):
        with pytest.raises(LLMCallError):
            parse_json(bad)


@pytest.mark.parametrize("mode, extract_version", [("select", "v3"), ("rewrite", "v1")])
def test_prompts_render_every_placeholder(monkeypatch, mode, extract_version):
    monkeypatch.setattr(settings, "extract_mode", mode)
    assert router.prompt_version("extract") == extract_version  # each mode gets its own prompt
    for name, variables in (
        ("enrich", {"query": "q"}),
        ("extract", {"query": "q", "intents": "i", "sentences": "s", "max_actions": 8}),
        ("variations", {"query": "q"}),
        ("coverage", {"query": "q", "paragraphs": "p"}),
    ):
        text = router.render(router.load_prompt(name, router.prompt_version(name)), variables)
        assert "{{" not in text and "TODO" not in text


# ---- the LLM stages inside the pipeline -------------------------------------------------------------
VARIATIONS = [
    "My S22 display reacts slowly whenever I touch it.",
    "galaxy s22 touch lag delay",
    "Why is my phone so slow to register taps?! Super annoying.",
    "touchscren lagg on my galxy",
    "I am experiencing a noticeable latency when interacting with the display.",
    "the screen takes ages to respond when i swipe",
    "S22 unresponsive touch input issue",
    "Taps register late on my Samsung, what can I do?",
    "My phone ignores my fingers for a second before reacting.",
    "touch response delayed samsung s22 fix",
    "Whenever I type, letters appear a moment later on the screen.",
    "Display feels sluggish to touch, any help?",
]
SELECT_ANSWER = {
    "goals": [
        {
            "problem": "Touchscreen input is delayed and laggy",
            "title": "Touchscreen Input Lag Problem",
            "topic": "Touchscreen Issues",
            "domain": "Display",
            "actions": [
                {
                    "src_ids": ["S31", "S32"],
                    "name": "Turn Off Full Screen Gestures",
                    "description": "It will switch to navigation buttons",
                    "screen_path": "Settings > Display > Navigation bar",
                    "intent_verb": "open",
                },
                {
                    "src_ids": ["S999"],
                    "name": "Recalibrate Touchscreen",
                    "description": "It will recalibrate the touch panel",
                    "screen_path": "",
                    "intent_verb": "none",
                },
            ],
        }
    ]
}


@pytest.fixture
def fake_llm(monkeypatch, keys):
    calls = []

    def complete_json(prompt_name, variables, schema, *, stage=None, info=None, clients=None, accept=None):
        calls.append(prompt_name)
        answer = {"variations": {"variations": VARIATIONS}, "coverage": {"fixes": []}}.get(
            prompt_name, SELECT_ANSWER
        )
        if accept is not None:
            assert accept(answer)
        if info is not None:
            info.update(model=f"fake-{prompt_name}", tokens_in=10, tokens_out=5, cost_usd=0.0001, attempts=[])
        return answer

    monkeypatch.setattr(router, "complete_json", complete_json)
    return calls


def test_select_mode_uses_the_articles_own_steps_and_the_models_intents(fake_llm):
    cache.clear()
    events = {e.stage.value: e.detail for e in run_stream(REQUEST["query"], REQUEST["siis_response"])}
    assert events["extract"]["source"] == "llm" and events["extract"]["mode"] == "select"
    assert events["ground"]["dropped_steps"] == []  # the invented S999 never became a step
    body = events["done"]
    (goal,) = body["contexts"]
    assert goal["goal"] == "Follow these steps to perform this Touchscreen Issues Troubleshooting."
    assert goal["title"] == "Touchscreen input lag"
    by_name = {a["actionName"]: a for a in goal["actions"]}
    action = by_name["Turn Off Full Screen Gestures"]
    assert action["category"] == "auto"
    # the rest are the article's own numbered general-fix steps (restart, charger, updates, support...)
    added = events["extract"]["completed_sections"]
    assert set(by_name) - {"Turn Off Full Screen Gestures"} == set(added)
    assert action["stepGroups"][0]["steps"] == [
        "Go to Settings.",
        "Tap Display.",
        "Tap Navigation bar.",
        "Select Buttons to turn off full screen gestures.",
    ]
    assert body["meta"]["model"] == "fake-extract"
    assert sorted(fake_llm) == ["coverage", "extract", "variations"]
    cache.clear()


def test_background_variations_reach_results_and_the_cache(fake_llm):
    cache.clear()
    body, variations = run_with_variations(REQUEST["query"], REQUEST["siis_response"])
    assert body["contexts"] and settings.variation_min <= len(variations) <= settings.variation_max
    assert set(variations) & set(VARIATIONS), "the model's variations, not only templates"
    paraphrase = next(v for v in variations if v in VARIATIONS)
    events = [e.stage.value for e in run_stream(paraphrase, REQUEST["siis_response"])]
    assert events == ["cache", "done"]  # a stored variation now answers from the cache
    cache.clear()


def test_llm_failure_degrades_to_rules_capped_and_repeats_identically(monkeypatch, keys):
    def down(*args, **kwargs):
        raise router.LLMError([{"model": "x", "ok": False, "error": "http_500", "ms": 1}])

    monkeypatch.setattr(router, "complete_json", down)
    cache.clear()
    events = {e.stage.value: e.detail for e in run_stream(REQUEST["query"], REQUEST["siis_response"])}
    assert events["extract"]["source"] == "rules"
    body = events["done"]
    assert body["contexts"] and all(g["score"] <= settings.rules_only_score_cap for g in body["contexts"])
    repeat = {e.stage.value: e.detail for e in run_stream(REQUEST["query"], REQUEST["siis_response"])}
    assert repeat["done"]["meta"]["cache_tier"] == "exact" and repeat["done"]["contexts"] == body["contexts"]
    cache.clear()


def test_over_the_cold_capacity_the_answer_is_rules_only_and_no_model_is_called(fake_llm, monkeypatch):
    """Every cold slot is taken: the request does not queue behind them. It gets the article's own steps,
    score-capped and cached for the degraded window, and spends no LLM quota; the next ask with a free
    slot gets a model's answer once that window has passed."""
    monkeypatch.setattr(settings, "cold_queue_wait_s", 0.01)
    cache.clear()
    turned_away = capacity.state()["cold_turned_away"]
    with ExitStack() as held:
        for _ in range(settings.cold_max_concurrent):
            assert held.enter_context(capacity.cold_slot())
        events = {e.stage.value: e.detail for e in run_stream(REQUEST["query"], REQUEST["siis_response"])}
    assert events["extract"]["source"] == "rules" and events["extract"]["capacity"] == "cold_busy"
    assert fake_llm == []  # no extract, coverage or variations call
    body = events["done"]
    assert body["contexts"] and all(g["score"] <= settings.rules_only_score_cap for g in body["contexts"])
    assert capacity.state() == {
        "cold_in_flight": 0,
        "cold_max_concurrent": settings.cold_max_concurrent,
        "cold_turned_away": turned_away + 1,
    }
    monkeypatch.setattr(settings, "degraded_cache_ttl_s", 0.0)
    again = {e.stage.value: e.detail for e in run_stream(REQUEST["query"], REQUEST["siis_response"])}
    assert again["extract"]["source"] == "llm" and "extract" in fake_llm
    cache.clear()


def _answers(*answers):
    """A stand-in router: each answer goes through the caller's accept check, then the call fails the
    way the real router fails when nothing was accepted."""

    def complete_json(prompt_name, variables, schema, *, stage=None, info=None, clients=None, accept=None):
        if prompt_name == "variations":
            return {"variations": VARIATIONS}
        for answer in answers:
            if accept is None or accept(answer):
                if info is not None:
                    info.update(model="fake", tokens_in=1, tokens_out=1, cost_usd=0.0, attempts=[])
                return answer
        raise router.LLMError(
            [{"model": f"m{i}", "ok": False, "error": "rejected", "ms": 1} for i in answers]
        )

    return complete_json


def test_every_model_choosing_nothing_is_a_no_match_not_a_failure(monkeypatch, keys):
    """The prompt says to return no goals when the article does not address the complaint. When every
    model that answered agrees, that is the answer: no rules fallback copying the article's steps."""
    monkeypatch.setattr(router, "complete_json", _answers({"goals": []}, {"goals": [{"actions": []}]}))
    cache.clear()
    events = {e.stage.value: e.detail for e in run_stream(REQUEST["query"], REQUEST["siis_response"])}
    assert events["extract"]["no_match"] is True and events["extract"]["actions"] == []
    assert events["done"]["contexts"] == [] and events["done"]["meta"]["fallback"] == "no_match"
    cache.clear()


def test_one_empty_answer_is_not_trusted(monkeypatch, keys):
    """14B has answered an empty selection for an article that did fit. Alone it decides nothing: the
    other model's selection wins, and with no other answer the engine degrades to rules as before."""
    monkeypatch.setattr(router, "complete_json", _answers({"goals": []}, SELECT_ANSWER))
    cache.clear()
    events = {e.stage.value: e.detail for e in run_stream(REQUEST["query"], REQUEST["siis_response"])}
    assert events["extract"]["source"] == "llm" and events["done"]["contexts"]
    monkeypatch.setattr(router, "complete_json", _answers({"goals": []}))
    cache.clear()
    events = {e.stage.value: e.detail for e in run_stream(REQUEST["query"], REQUEST["siis_response"])}
    assert events["extract"]["source"] == "rules" and events["done"]["contexts"]
    cache.clear()


def test_a_degraded_answer_is_retried_once_its_window_has_passed(monkeypatch, keys):
    """Every model busy: the rules answer is cached (repeats stay identical), but only for
    degraded_cache_ttl_s; after that the same question gets a cold run and a model answer."""

    def down(*args, **kwargs):
        raise router.LLMError([{"model": "x", "ok": False, "error": "http_503", "ms": 1}])

    monkeypatch.setattr(router, "complete_json", down)
    cache.clear()
    first = {e.stage.value: e.detail for e in run_stream(REQUEST["query"], REQUEST["siis_response"])}
    assert first["extract"]["source"] == "rules"
    monkeypatch.setattr(settings, "degraded_cache_ttl_s", 0.0)
    monkeypatch.setattr(router, "complete_json", _answers(SELECT_ANSWER))
    again = {e.stage.value: e.detail for e in run_stream(REQUEST["query"], REQUEST["siis_response"])}
    assert again["done"]["meta"]["cache_hit"] is False and again["extract"]["source"] == "llm"
    cache.clear()


def test_selection_keeps_only_real_sentences_and_splits_instructions():
    sentences = [
        SiisSentence(id="S1", section="s", text="Go to Settings, tap Display, and then tap Dark mode."),
        SiisSentence(id="S2", section="s", text="Dark mode is easier on the eyes."),
    ]
    action = {
        "src_ids": ["S1", "S2", "S9"],
        "name": "A",
        "description": "",
        "screen_path": "",
        "intent_verb": "x",
    }
    goal = {"problem": "Screen too bright", "title": "bright screen", "topic": "", "domain": "Nope"}
    answer = {"goals": [{**goal, "actions": [action]}]}
    actions, topics, intents = extract_module.actions_from_selection(answer, sentences)
    assert [s.text for s in actions[0].steps] == ["Go to Settings.", "Tap Display.", "Tap Dark mode."]
    assert all(s.src_ids == ["S1"] for s in actions[0].steps) and actions[0].intent_verb is None
    assert intents == [Intent(text="Screen too bright", title="Bright screen", domain="Other")]
    assert topics == ["Bright screen"]
    assert not extract_module.usable_selection(answer, {"S7"})


def test_the_schema_only_allows_the_articles_own_sentence_ids():
    schema = extract_module.select_schema(["S1", "S2"])
    action = schema["properties"]["goals"]["items"]["properties"]["actions"]["items"]
    assert action["properties"]["ids"]["items"] == {"type": "string", "enum": ["S1", "S2"]}
    assert sorted(action["required"]) == ["desc", "ids", "name", "path", "verb"]
    wire = {
        "goals": [
            {
                "title": "t",
                "actions": [{"ids": ["S1"], "name": "n", "desc": "d", "path": "p", "verb": "open"}],
            }
        ]
    }
    (action,) = extract_module._long_keys(wire)["goals"][0]["actions"]
    assert action == {
        "src_ids": ["S1"],
        "name": "n",
        "description": "d",
        "screen_path": "p",
        "intent_verb": "open",
    }
    shared = extract_module.SELECT_SCHEMA["properties"]["goals"]["items"]["properties"]["actions"]["items"]
    assert shared["properties"]["src_ids"]["items"] == {"type": "string"}  # the shared schema is untouched


def test_a_variation_that_changes_the_problem_is_kept_only_to_reach_eight():
    query = "My Galaxy S22 screen turns completely blank or white when I search for a stock price."
    drifting = "My Samsung Galaxy S22 screen freezes whenever I open the stock app."  # blank -> slow
    faithful = [
        "Galaxy S22 display goes white and empty during stock price searches.",
        "S22 screen blank white stock lookup",
        "Whenever I look up a share price my S22 display turns entirely white.",
        "my s22 screne goes blnak when i serch stocks",
        "Why does my Galaxy S22 screen go blank when I check stocks?",
        "Checking a share price leaves my S22 with a blank screen.",
        "The S22 display blanks out to white in the stock search.",
        "Stock price search makes my Galaxy S22 screen go completely blank.",
        "ugh, S22 screen is all white and empty whenever I check a stock",
    ]
    slots = enrich_module.extract_slots(enrich_module.normalize_query(query))
    kept, dropped = enrich_module.filter_variations(query, [drifting, *faithful], slots)
    assert (
        drifting not in kept
        and {"text": drifting, "reason": "changes_the_problem"}.items()
        <= {**next(d for d in dropped if d["text"] == drifting)}.items()
    )
    kept, _ = enrich_module.filter_variations(query, [drifting, faithful[0]], slots)
    assert drifting in kept  # too few otherwise: 8-10 variations is a hard rule


def test_an_action_named_after_another_sentence_is_renamed_from_its_screen():
    sentences = [
        SiisSentence(id="S7", section="s", text="If your screen protector is peeling, please remove it."),
        SiisSentence(
            id="S9",
            section="s",
            text="Go to Settings, tap Display, and then tap the switch next to Touch sensitivity.",
        ),
        SiisSentence(
            id="S20", section="s", text="Press and hold the Power button and the Volume down button."
        ),
    ]
    goal = {"problem": "p", "title": "t t", "topic": "", "domain": "Display"}
    wrong = {
        "src_ids": ["S9"],
        "name": "Remove Damaged Screen Protector",
        "description": "",
        "screen_path": "Settings > Display > Touch sensitivity",
        "intent_verb": "enable",
    }
    physical = {
        "src_ids": ["S20"],
        "name": "Force Restart Device",
        "description": "",
        "screen_path": "",
        "intent_verb": "restart",
    }
    right = {
        "src_ids": ["S7"],
        "name": "Remove Screen Protector",
        "description": "",
        "screen_path": "",
        "intent_verb": "none",
    }
    actions, _, _ = extract_module.actions_from_selection(
        {"goals": [{**goal, "actions": [wrong, physical, right]}]}, sentences
    )
    assert [a.name for a in actions] == [
        "Enable Touch sensitivity",
        "Force Restart Device",
        "Remove Screen Protector",
    ]


def test_a_confirmation_step_never_stands_alone():
    """The model sometimes picks only "Tap Restart again to confirm."; the sentence that starts the
    restart comes with it, and only from the same section."""
    sentences = [
        SiisSentence(id="S1", section="Restart", text="Press and hold the Power button, then tap Restart."),
        SiisSentence(id="S2", section="Restart", text="Tap Restart again to confirm."),
        SiisSentence(id="S3", section="Other", text="Tap Done again."),
    ]
    action = {"name": "Restart", "description": "", "screen_path": "", "intent_verb": "restart"}
    for chosen, expected in ((["S2"], ["S1", "S2"]), (["S1", "S2"], ["S1", "S2"]), (["S3"], ["S3"])):
        answer = {
            "goals": [
                {
                    "problem": "p",
                    "title": "t t",
                    "topic": "",
                    "domain": "Other",
                    "actions": [{**action, "src_ids": chosen}],
                }
            ]
        }
        actions, _, _ = extract_module.actions_from_selection(answer, sentences)
        assert list(dict.fromkeys(i for s in actions[0].steps for i in s.src_ids)) == expected


def test_enrich_llm_on_the_critical_path_still_works(monkeypatch, keys):
    def complete_json(prompt_name, variables, schema, *, stage=None, info=None, clients=None, accept=None):
        return {
            "canonical_query": "Touchscreen input is delayed",
            "intents": [{"text": "Touch input lags", "domain": "Display", "title": "Touch input lag"}],
            "variations": VARIATIONS,
        }

    monkeypatch.setattr(router, "complete_json", complete_json)
    monkeypatch.setattr(settings, "enrich_llm_on_critical_path", True)
    intents, variations, detail = enrich_module.enrich_with_report(
        REQUEST["query"], Slots(component="screen")
    )
    assert detail["source"] == "llm" and intents[0].title == "Touch input lag"
    assert settings.variation_min <= len(variations) <= settings.variation_max


# ---- call C's verdict, and the word check that must agree with it (coverage.v2, pipeline/mismatch.py) ---
UNRELATED = {"match": "unrelated", "fixes": []}
RELATED = {"match": "related", "fixes": []}


def _with_verdict(monkeypatch, verdict: dict | None, problem: str = "The phone gets hot"):
    """Call B picks the touchscreen action and states `problem`; call C answers `verdict`; None = call C down."""
    select = {"goals": [{**SELECT_ANSWER["goals"][0], "problem": problem, "title": "Phone overheating"}]}

    def complete_json(prompt_name, variables, schema, *, stage=None, info=None, clients=None, accept=None):
        if prompt_name == "variations":
            return {"variations": VARIATIONS}
        if prompt_name == "coverage" and verdict is None:
            raise router.LLMError([{"model": "m", "ok": False, "error": "timeout", "ms": 1}])
        answer = verdict if prompt_name == "coverage" else select
        if accept is not None:
            assert accept(answer)
        if info is not None:
            info.update(model=f"fake-{prompt_name}", tokens_in=1, tokens_out=1, cost_usd=0.0, attempts=[])
        return answer

    monkeypatch.setattr(router, "complete_json", complete_json)


def _run_cold(query: str):
    cache.clear()
    events = {e.stage.value: e.detail for e in run_stream(query, REQUEST["siis_response"])}
    cache.clear()
    return events


def test_an_article_is_turned_away_when_call_c_and_the_word_check_agree(monkeypatch, keys):
    """The reported bug: "phone is hot" against the touchscreen article returned touchscreen fixes at 0.84."""
    _with_verdict(monkeypatch, UNRELATED)
    events = _run_cold("phone is hot")
    assert events["extract"]["no_match"] is True
    gate = events["extract"]["mismatch"]
    assert (
        gate["acted"] and gate["verdict"] == "unrelated" and gate["terms"] == ["hot"] and gate["shared"] == []
    )
    assert events["done"]["contexts"] == [] and events["done"]["meta"]["fallback"] == "no_match"


def test_the_models_verdict_alone_never_turns_an_article_away(monkeypatch, keys):
    """Measured: 16% of the paraphrases of well-matched kit rows came back `unrelated` (row 8: 10 of 10).
    The complaint shares its words with the article, so the word check says no and the plan stands."""
    _with_verdict(monkeypatch, UNRELATED)
    events = _run_cold(REQUEST["query"])
    gate = events["extract"]["mismatch"]
    assert gate["verdict"] == "unrelated" and gate["acted"] is False and gate["shared"]
    (goal,) = events["done"]["contexts"]
    assert "Turn Off Full Screen Gestures" in [a["actionName"] for a in goal["actions"]]


def test_the_word_check_alone_never_turns_an_article_away(monkeypatch, keys):
    _with_verdict(monkeypatch, RELATED)
    events = _run_cold("phone is hot")
    assert events["extract"]["mismatch"]["acted"] is False and events["done"]["contexts"]


def test_call_bs_problem_statement_is_not_the_customers_words(monkeypatch, keys):
    """Call B explains causes in its problem statement ("background processes or software malfunctions"),
    words the touchscreen article shares with any complaint. Counted as support they blinded the check for
    "phone is hot" (seen on the real models), so only the customer's own words count."""
    _with_verdict(monkeypatch, UNRELATED, problem="Phone overheating due to background processes or software")
    events = _run_cold("phone is hot")
    gate = events["extract"]["mismatch"]
    assert gate["acted"] is True and gate["terms"] == ["hot"] and events["done"]["contexts"] == []


def test_a_misspelt_complaint_is_never_turned_away(monkeypatch, keys):
    _with_verdict(monkeypatch, UNRELATED)
    events = _run_cold("phoen is hot")
    gate = events["extract"]["mismatch"]
    assert gate["verdict"] == "unrelated" and gate["acted"] is False and gate["typos"] == ["phoen"]
    assert events["done"]["contexts"]


def test_without_call_cs_answer_the_gate_stays_shut(monkeypatch, keys):
    _with_verdict(monkeypatch, None)
    events = _run_cold("phone is hot")
    assert events["extract"]["mismatch"]["verdict"] is None and events["done"]["contexts"]


def test_the_mismatch_gate_can_be_switched_off(monkeypatch, keys):
    _with_verdict(monkeypatch, UNRELATED)
    monkeypatch.setattr(settings, "coverage_mismatch", "off")
    events = _run_cold("phone is hot")
    assert events["extract"]["mismatch"]["acted"] is False and events["done"]["contexts"]


def _extract_down_but_call_c_answers(monkeypatch, verdict: dict | None):
    """Call B fails (both models busy), so the rules extractor answers; call C still says `verdict`."""

    def complete_json(prompt_name, variables, schema, *, stage=None, info=None, clients=None, accept=None):
        if prompt_name == "variations":
            return {"variations": VARIATIONS}
        if prompt_name == "extract" or verdict is None:
            raise router.LLMError([{"model": "m", "ok": False, "error": "timeout", "ms": 1}])
        if info is not None:
            info.update(model="fake-coverage", tokens_in=1, tokens_out=1, cost_usd=0.0, attempts=[])
        return verdict

    monkeypatch.setattr(router, "complete_json", complete_json)


def test_a_failed_call_b_still_gets_the_same_test_on_the_rules_answer(monkeypatch, keys):
    """The second repro: "gets very hot" on the touchscreen article fell back to rules and returned the
    charger, touch sensitivity, support and restart. Call C had the verdict all along."""
    _extract_down_but_call_c_answers(monkeypatch, UNRELATED)
    events = _run_cold("phone is hot")
    assert events["extract"]["source"] == "rules" and events["extract"]["mismatch"]["acted"] is True
    assert events["done"]["contexts"] == [] and events["done"]["meta"]["fallback"] == "no_match"
    events = _run_cold(REQUEST["query"])  # a complaint the article does cover keeps its rules answer
    assert events["extract"]["mismatch"]["acted"] is False and events["done"]["contexts"]


def test_a_failed_call_b_and_no_verdict_leaves_the_rules_answer_as_it_was(monkeypatch, keys):
    _extract_down_but_call_c_answers(monkeypatch, None)
    events = _run_cold("phone is hot")
    (goal,) = events["done"]["contexts"]
    assert events["extract"]["source"] == "rules" and events["extract"]["mismatch"]["verdict"] is None
    assert "Touch Sensitivity Setting" in [a["actionName"] for a in goal["actions"]]


def test_coverage_schema_requires_the_verdict_and_an_unknown_one_is_ignored(monkeypatch, keys):
    schema = extract_module.coverage_schema(["P1", "P2"])
    assert schema["required"] == ["match", "fixes"]
    assert schema["properties"]["match"]["enum"] == ["related", "unrelated"]

    def complete_json(prompt_name, variables, schema, *, stage=None, info=None, clients=None, accept=None):
        return {"match": "banana", "fixes": [{"p": "P1", "name": "Restart Device"}, {"p": "P9", "name": "x"}]}

    monkeypatch.setattr(router, "complete_json", complete_json)
    fixes, info = extract_module._coverage_llm("q", "article", ["P1", "P2"])
    assert fixes == [{"p": "P1", "name": "Restart Device"}] and info["match"] is None


ARTICLE = ["Restart your phone.", "Touch sensitivity", "Check the charger, then contact support."]


def test_the_word_check_ignores_typos_generic_words_and_model_numbers():
    from app.pipeline import mismatch

    hot = mismatch.check(["My Galaxy S22 phone is hot"], ARTICLE)
    assert hot == {
        "unrelated": True,
        "terms": ["hot"],
        "shared": [],
        "typos": [],
    }  # phone, galaxy, s22: nothing
    assert mismatch.check(["my fon is blak and dead"], ARTICLE)["terms"] == [
        "dead"
    ]  # rare words never trigger
    assert mismatch.check(["asdkjh qwe zzz 12345"], ARTICLE)["unrelated"] is False  # no real word: no verdict
    assert mismatch.check([""], ARTICLE)["unrelated"] is False


def test_a_misspelt_complaint_gets_no_verdict():
    """The misspelt paraphrases are what a bare word check wrongly flags: the words a typo lost may be the
    ones the article shares. So any word that is not English, not Samsung vocabulary and not in the article
    keeps the check shut; a rare but real word ("touchscreen") does not."""
    from app.pipeline import mismatch

    typo = mismatch.check(["phoen is hot"], ARTICLE)
    assert typo["unrelated"] is False and typo["typos"] == ["phoen"] and typo["terms"] == ["hot"]
    assert mismatch.check(["touchscreen is hot"], ARTICLE)["typos"] == []
    # support vocabulary is not a typo: nothing else in the complaint is unknown, so the check can act
    assert mismatch.check(["touchscreen is hot"], ["Restart your phone."])["unrelated"] is True


def test_the_word_check_matches_by_word_stem_and_any_shared_word_saves_the_article():
    from app.pipeline import mismatch

    # "charge" is general-fix vocabulary every article has: no information either way, so no verdict
    assert mismatch.check(["it will not charge"], ARTICLE)["unrelated"] is False
    assert (
        mismatch.check(["the display flickers"], ["The display is fine"])["unrelated"] is False
    )  # shared word
    assert mismatch.check(["it flickers"], ["Screen flickering after an update"])["shared"] == ["flickers"]
    # a rare word (not common English, so it cannot trigger) still saves the article when the article uses it
    assert (
        mismatch.check(["touchscreen is hot"], ["Touch sensitivity", "the touchscreen"])["unrelated"] is False
    )
