"""data/fixtures is a contract other lanes build against: keep it valid, grounded and rule-abiding."""

import json
import re
from itertools import combinations
from pathlib import Path

import pytest

from app.config import settings
from app.models import DraftAction, LinkTier, ResponseMeta, StageEvent, StageName
from app.schema import ContextDeeplinkResponse

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "data" / "fixtures"


def _load(*parts: str):
    return json.loads(FIXTURES.joinpath(*parts).read_text(encoding="utf-8"))


CATALOG = json.loads((ROOT / "data/kit/deeplinks.json").read_text(encoding="utf-8"))["deeplinks"]
BY_ID = {e["id"]: e for e in CATALOG}
BY_ACTION = {}
for _e in CATALOG:
    BY_ACTION.setdefault((_e["deeplink"], _e["description"], _e["message"], _e["originalType"]), []).append(
        _e
    )

SCENARIOS = [s["name"] for s in _load("scenarios.json")["scenarios"]]
STAGES = ["cache", "enrich", "segment", "extract", "ground", "resolve", "compile", "done"]
LEAK = re.compile(
    r"https?://|www\.|\b[\w-]+\.(?:com|net|org|io|co|in|gov|edu)\b|[\w.+-]+@[\w-]+\.[\w.]+", re.IGNORECASE
)
STOP = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "to",
    "of",
    "in",
    "on",
    "for",
    "with",
    "your",
    "you",
    "it",
    "is",
    "are",
    "be",
    "as",
    "at",
    "by",
    "if",
    "then",
    "this",
    "that",
    "these",
    "those",
    "from",
    "into",
    "up",
    "can",
    "may",
    "might",
    "will",
    "would",
    "could",
    "should",
    "when",
    "what",
    "there",
    "their",
    "its",
    "our",
    "we",
    "us",
    "has",
    "have",
    "not",
    "any",
    "all",
    "also",
    "just",
    "very",
    "more",
    "some",
    "such",
    "how",
    "does",
    "do",
    "did",
    "was",
    "were",
    "been",
    "being",
}
POLARITY = {"enable": "onURL", "disable": "offURL", "open": "onClickURL"}


def _terms(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z\-']+", s.lower()) if w not in STOP and len(w) > 2}


def _strings(obj, skip_keys=("deeplink",)):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in skip_keys:
                yield from _strings(v, skip_keys)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, skip_keys)


def _actions(plan):
    return [(g, a) for g in plan["contexts"] for a in g["actions"]]


def test_manifest_points_at_existing_files():
    for name in SCENARIOS:
        for f in ("request.json", "plan.json", "stream.json", "draft_actions.json", "cache_events.json"):
            assert (FIXTURES / name / f).is_file(), f"{name}/{f}"


@pytest.mark.parametrize("name", SCENARIOS)
def test_plan_validates_against_official_schema(name):
    plan = _load(name, "plan.json")
    parsed = ContextDeeplinkResponse.model_validate(plan)
    assert parsed.contexts, "a fixture plan must not be empty"
    ResponseMeta.model_validate(plan["meta"])


@pytest.mark.parametrize("name", SCENARIOS)
def test_plan_and_stream_have_zero_url_leaks(name):
    for doc in (_load(name, "plan.json"), _load(name, "stream.json"), _load(name, "cache_events.json")):
        for s in _strings(doc):
            assert not LEAK.search(s), f"URL-like text in {name}: {s!r}"


@pytest.mark.parametrize("name", SCENARIOS)
def test_deeplinks_are_copied_verbatim_from_the_catalog(name):
    for _, a in _actions(_load(name, "plan.json")):
        for sg in a["stepGroups"]:
            link, val = sg["actionableDeeplink"], sg["validationDeeplink"]
            if a["category"] != "auto":
                assert link is None and val is None, f"{a['actionName']}: only auto actions carry links"
                continue
            assert link is not None
            if link["deeplink"] == "bixby://dummy_positive":
                placeholder = BY_ID["DL-DUMMY"]
                assert link["originalType"] == placeholder["originalType"] and val is None
                assert 5 <= len(link["description"].split()) <= 7 and 5 <= len(link["message"].split()) <= 7
                continue
            key = (link["deeplink"], link["description"], link["message"], link["originalType"])
            assert key in BY_ACTION, f"{a['actionName']}: not a catalog entry"
            assert any(e["validation"] == val for e in BY_ACTION[key]), (
                f"{a['actionName']}: validation altered"
            )


@pytest.mark.parametrize("name", SCENARIOS)
def test_output_string_rules(name):
    plan = _load(name, "plan.json")
    for g, a in _actions(plan):
        assert re.fullmatch(r"Follow these steps to perform this [A-Z][\w ]+ Troubleshooting\.", g["goal"])
        assert 2 <= len(g["title"].split()) <= 3 and g["title"][0].isupper()
        assert all(w[0].isupper() for w in a["actionName"].split()), a["actionName"]
        words = a["description"].split()
        assert a["description"].startswith("It will") and 5 <= len(words) <= 7, a["description"]
        assert a["category"] in ("auto", "manual", "critical")
        for sg in a["stepGroups"]:
            for step in sg["steps"]:
                assert step.endswith(".") and not re.match(r"^\d", step), step
    names = [a["actionName"] for _, a in _actions(plan)]
    assert len(names) == len(set(names)), "actionName must be deduplicated"


@pytest.mark.parametrize("name", SCENARIOS)
def test_scores_follow_the_formula(name):
    plan = _load(name, "plan.json")
    compile_ev = next(e for e in _load(name, "stream.json") if e["stage"] == "compile")
    inputs = compile_ev["detail"]["score_inputs"]
    assert len(inputs) == len(plan["contexts"])
    for g, i in zip(plan["contexts"], inputs):
        expected = 0.4 * i["relevance"] + 0.3 * i["grounding_coverage"] + 0.3 * i["link_coverage"]
        assert g["score"] == pytest.approx(min(1.0, max(0.0, expected)), abs=0.006)
        assert g["title"] == i["title"]


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_step_traces_to_real_article_sentences(name):
    article = re.sub(r"\s+", " ", _load(name, "request.json")["siis_response"]["content"])
    seg = next(e for e in _load(name, "stream.json") if e["stage"] == "segment")["detail"]
    sentences = {s["id"]: s["text"] for s in seg["sentences"]}
    for text in sentences.values():
        assert text in article, f"sentence is not verbatim from the article: {text!r}"
    draft = _load(name, "draft_actions.json")["actions"]
    for raw in draft:
        action = DraftAction.model_validate(raw)
        for step in action.steps:
            assert step.grounded and step.src_ids, f"{action.name}: {step.text}"
            assert set(step.src_ids) <= set(sentences), f"{action.name}: unknown sentence id"
            source_terms = set().union(*(_terms(sentences[i]) for i in step.src_ids))
            assert _terms(step.text) & source_terms, f"no shared content term: {step.text!r}"
            assert step.grounding_score >= settings.grounding_cos_threshold
        if action.link and action.link.entry_id:
            assert action.link.entry_id in BY_ID


@pytest.mark.parametrize("name", SCENARIOS)
def test_draft_actions_match_the_plan_and_respect_dependencies(name):
    plan = _load(name, "plan.json")
    draft = [DraftAction.model_validate(a) for a in _load(name, "draft_actions.json")["actions"]]
    planned = [(a["actionName"], a["category"]) for _, a in _actions(plan)]
    assert planned == [(d.name, d.category) for d in draft]
    for d in draft:
        assert d.link is not None, f"{d.name}: the compiler input carries a resolved LinkDecision"
        if d.category == "auto":  # auto only when a link resolved
            assert d.link.tier in (LinkTier.catalog, LinkTier.dummy), d.name
        if d.link.tier is LinkTier.catalog:
            assert d.category == "auto", d.name
    order = [d.name for d in draft]
    if name == "touch_lag":
        assert order.index("Back Up Your Data") < order.index("Factory Data Reset")
    if name == "email_not_responding":
        assert order.index("Restart In Safe Mode") < order.index("Uninstall Email App")


@pytest.mark.parametrize("name", SCENARIOS)
def test_stream_fixture_shape_and_done_equals_plan(name):
    events = [StageEvent.model_validate(e) for e in _load(name, "stream.json")]
    assert [e.stage.value for e in events] == STAGES
    plan = _load(name, "plan.json")
    assert events[-1].detail == plan
    stage_ms = sum(e.ms for e in events if e.stage not in (StageName.done, StageName.error))
    assert plan["meta"]["latency_ms"] == pytest.approx(stage_ms, abs=0.5)
    ground = next(e for e in events if e.stage is StageName.ground).detail
    assert ground["kept_steps"] + len(ground["dropped_steps"]) == ground["proposed_steps"]


@pytest.mark.parametrize("name", SCENARIOS)
def test_query_variations_are_lexically_diverse(name):
    query = _load(name, "request.json")["query"]
    enrich = next(e for e in _load(name, "stream.json") if e["stage"] == "enrich")["detail"]
    kept = enrich["variations"]
    assert settings.variation_min <= len(kept) <= settings.variation_max

    def jac(a, b):
        ta, tb = set(re.findall(r"\w+", a.lower())), set(re.findall(r"\w+", b.lower()))
        return len(ta & tb) / len(ta | tb)

    for v in kept:
        assert jac(v, query) < settings.variation_jaccard_max, v
    for a, b in combinations(kept, 2):
        assert jac(a, b) < settings.variation_jaccard_max, (a, b)
    for d in enrich["dropped_variations"]:
        assert d["jaccard"] >= settings.variation_jaccard_max or d["reason"] == "over_limit"


@pytest.mark.parametrize("name", SCENARIOS)
def test_cache_hit_variants(name):
    variants = _load(name, "cache_events.json")
    plan = _load(name, "plan.json")
    for tier, v in variants.items():
        ev = StageEvent.model_validate(v["event"])
        assert ev.stage is StageName.cache and ev.detail["hit"] and ev.detail["tier"] == tier
        assert ev.detail["similarity"] >= (settings.cache_sim_threshold if tier == "semantic" else 1.0)
        assert v["meta"]["cache_hit"] and v["meta"]["cache_tier"] == tier and v["meta"]["cost_usd"] == 0.0
        assert v["meta"]["latency_ms"] < plan["meta"]["latency_ms"] / 10


def test_resolver_cases_are_consistent_with_the_catalog():
    cases = _load("resolver_cases.json")["cases"]
    assert len({c["id"] for c in cases}) == len(cases) >= 10
    seen_tiers = set()
    for c in cases:
        action = DraftAction.model_validate(c["action"])
        assert action.link is None, "resolver input has no link yet"
        exp = c["expected"]
        tier = LinkTier(exp["tier"])
        seen_tiers.add(tier)
        assert exp["basis"] in ("design-explicit", "interpretation")
        for wrong in exp["must_not_match"]:
            assert wrong in BY_ID and wrong != exp["entry_id"]
        if tier is LinkTier.catalog:
            entry = BY_ID[exp["entry_id"]]
            assert exp["original_type"] == entry["originalType"]
            if action.intent_verb in POLARITY:
                assert entry["originalType"] == POLARITY[action.intent_verb], c["id"]
        elif tier is LinkTier.dummy:
            assert exp["entry_id"] == "DL-DUMMY"
        else:
            assert exp["entry_id"] is None
    assert seen_tiers == set(LinkTier), "cover catalog, dummy and manual"


def test_kit_input_queries_are_matched_by_the_scenarios():
    kit_queries = {
        ln.strip() for ln in (ROOT / "data/kit/input.txt").read_text(encoding="utf-8").splitlines()
    }
    for s in _load("scenarios.json")["scenarios"]:
        if not s["authored_query"]:
            assert s["query"] in kit_queries
