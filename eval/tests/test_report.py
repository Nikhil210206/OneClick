"""report.py renders the Appendix C layout and never shows a number it was not given."""

import json
from types import SimpleNamespace

import pytest

import report

APPENDIX_C_HEADINGS = [
    "# System Performance Metrics & Evaluation Report",
    "## 1. Schema & Rule Compliance",
    "## 2. Accuracy Benchmarks",
    "## 3. Latency Benchmarks (N >= 30 requests per path)",
    "## 4. Operational Cost & Cache Efficacy",
    "## 5. Architectural Ablation Analysis",
    "## 6. Known Edge Cases & System Limitations",
]


def render(tmp_path, **files) -> str:
    for name, body in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(body))
    args = SimpleNamespace(
        model="m", embeddings="e", env="x", results_dir=tmp_path, results=tmp_path / "results.jsonl"
    )
    return report.build(args)


def row(md: str, label: str) -> str:
    return next(line for line in md.splitlines() if line.startswith(f"| {label}"))


def test_empty_results_render_every_section_as_not_measured(tmp_path):
    md = render(tmp_path)
    positions = [md.index(h) for h in APPENDIX_C_HEADINGS]
    assert positions == sorted(positions)
    assert "not measured" in row(md, "Schema-valid output lines")
    assert "not measured" in row(md, "Step accuracy")
    assert "not measured" in row(md, "Cold query - full pipeline")
    assert "Baseline: Full LLM Deeplink Mapping" in md


GATES_EMPTY_PLANS = {
    "git_sha": "abc",
    "gates": {"G4": {"value": 1.0}, "G5": {"value": 0}},
    "blocks": {
        "A1": {"detail": {"responses": 40, "non_empty": 0, "rule_pass_rate": {"goal": 1.0}}},
        "A2": {"detail": {"catalog_validity": None, "auto_with_link": None}},
    },
}


def test_empty_plans_are_not_reported_as_full_compliance(tmp_path):
    md = render(tmp_path, gates=GATES_EMPTY_PLANS)
    assert "100.0%" not in row(md, "Schema-valid output lines")
    assert "all had empty `contexts`" in md


def test_non_empty_plans_fill_section_1(tmp_path):
    gates = json.loads(json.dumps(GATES_EMPTY_PLANS))
    gates["blocks"]["A1"]["detail"].update(
        non_empty=40, rule_pass_rate={"goal": 1.0, "title": 0.9, "description": 0.8}
    )
    gates["blocks"]["A2"]["detail"].update(catalog_validity=1.0, auto_with_link=0.95)
    md = render(tmp_path, gates=gates)
    assert "| 100.0% |" in row(md, "Schema-valid output lines")
    assert "90.0%" in row(md, "Rule compliance")
    assert "| 0 |" in row(md, "Absolute URL leaks")
    assert "95.0%" in row(md, "Auto actions carrying")


def test_section_1_reports_the_robustness_checks_the_table_has_no_row_for(tmp_path):
    gates = json.loads(json.dumps(GATES_EMPTY_PLANS))
    gates["hygiene"] = {
        "deterministic_repeat": {"detail": "0/20 repeats returned a different plan"},
        "always_200": {"detail": "0/150 calls were not HTTP 200"},
    }
    gates["adversarial"] = {
        "n": 15,
        "passed": 14,
        "failures": [
            {"id": "adv_02", "kind": "hinglish", "problems": ["returned empty contexts, expected a plan"]}
        ],
    }
    gates["findings"] = {"counts": {"SAME_SCREEN_SPLIT": {"n": 3}, "STEP_PERIOD": {"n": 9}}}
    md = render(tmp_path, gates=gates)
    assert "0/20 repeats returned a different plan; 0/150 calls were not HTTP 200" in md
    assert "14 of 15 returned what the case expects" in md
    assert "adv_02 (hinglish): returned empty contexts, expected a plan" in md
    assert "3 actions opening the same catalog entry" in md and "STEP_PERIOD" not in md


API_LOAD = {
    "source": "HTTP against http://127.0.0.1:8000",
    "cold": {
        "n": 25,
        "timed_n": 25,
        "p50_ms": 4000.0,
        "p95_ms": 6000.0,
        "hit_rate": 0.0,
        "mean_cost_usd": 0.0,
        "models": {"ministral-14b-latest": 25},
        "tokens_measured": 25,
        "mean_tokens_in": 3120.4,
        "mean_tokens_out": 210.6,
        "sources": {"cold_pass": 20, "paraphrase_misses": 3, "near_miss_misses": 2},
        "cold_pass": {"n": 35, "timed_n": 20, "p50_ms": 4100.0, "p95_ms": 5900.0},
    },
    "repeat": {"n": 40, "timed_n": 40, "p50_ms": 6.0, "p95_ms": 18.0, "hit_rate": 1.0},
    "paraphrase": {
        "n": 200,
        "timed_n": 180,
        "p50_ms": 9.0,
        "p95_ms": 15.0,
        "hit_rate": 0.9,
        "wrong_plan": 2,
        "same_plan": 177,
        "by_register": {"typo": 0.6, "formal": 0.95},
    },
}


def test_api_load_reports_pooled_cold_tokens_small_samples_and_served_plans(tmp_path):
    md = render(tmp_path, loadtest={"api": API_LOAD})
    assert "| 4000.0 | 6000.0 |" in row(md, "Cold query - full pipeline")
    assert "20 from the cold pass (kit + unseen), 3 paraphrases and 2 near misses" in md
    assert "the cold pass alone: p50 4100.0 ms, p95 5900.0 ms (n=20)" in md
    assert "Below the template's N >= 30: cold." in md
    assert "3120 prompt + 211 completion tokens per cold query" in md
    assert "over 25 cold queries" in md
    assert "2 hits served a plan other than their original query's; 177 of 200 got exactly" in md
    assert "typo 60% hit rate over HTTP" in md


def test_declines_the_cooldown_and_wrong_plans_are_named_in_section_6(tmp_path):
    load = json.loads(json.dumps(API_LOAD))
    load["cold"]["models"] = {"ministral-14b-latest": 20, "none": 5}
    load["paraphrase"].update(
        hits=180,
        wrong=[
            {"id": "p1", "row_id": "row_4", "query": "a16 dsplay went balck", "source": "other_kit_answer"}
        ],
        hits_by_source={"own_kit_answer": 170, "same_complaint_paraphrase": 9, "other_kit_answer": 1},
    )
    load["notes"] = ["7 of 35 cold-pass calls were answered from the cache"]
    md = render(tmp_path, loadtest={"api": load})
    assert "no model (declined, no_match) (5 cold queries)" in report.engine_models({"api": load})  # header
    assert "ministral-14b-latest 20, no model (declined, no_match) 5" in md
    assert f"on a {report.llm_cooldown_s()} cooldown" in md and "60 s cooldown" not in md
    assert '1 of 180 paraphrase hits: "a16 dsplay went balck" (row_4)' in md
    assert "9 more hit a second plan for the same complaint" in md
    assert "_7 of 35 cold-pass calls were answered from the cache._" in md


def test_paraphrase_hits_are_reported_by_where_their_plan_came_from(tmp_path):
    load = json.loads(json.dumps(API_LOAD))
    load["paraphrase"]["hits_by_source"] = {
        "own_kit_answer": 150,
        "same_complaint_paraphrase": 28,
        "other_kit_answer": 2,
    }
    md = render(tmp_path, loadtest={"api": load})
    assert (
        "2 hits served a plan solved for another complaint; 28 served the plan of an earlier paraphrase" in md
    )


def test_a_paraphrase_run_without_a_plan_check_does_not_claim_one(tmp_path):
    load = json.loads(json.dumps(API_LOAD))
    del load["paraphrase"]["wrong_plan"], load["paraphrase"]["same_plan"]
    md = render(tmp_path, loadtest={"api": load})
    assert "served the wrong plan" not in md and "was not checked in this run" in md


def variant(key, name, rel, measured=True):
    if not measured:
        return {"key": key, "name": name, "row": key, "measured": False, "reason": "MISTRAL_API_KEY not set"}
    group = {
        "n": 10,
        "relevance_mean": rel,
        "p_at_1": 0.8,
        "p_at_1_n": 5,
        "tier_accuracy": 0.9,
        "wrong_link_rate": 0.1,
    }
    return {
        "key": key,
        "name": name,
        "row": key,
        "measured": True,
        "all": group,
        "by_owner": {"a": group},
        "latency_ms": {"p50": 1.0, "p95": 2.0},
        "cost_per_step_usd": 0.0,
        "misses": [{"gold_tier": "catalog", "got_tier": "dummy", "screen": "Settings > Volume"}],
    }


ABLATION = {
    "commit": "abc",
    "gold": {"n": 10, "by_tier": {"catalog": 5, "dummy": 3, "manual": 2}, "by_owner": {"a": 10}},
    "variants": [
        variant("llm", "Baseline: Full LLM Deeplink Mapping", None, measured=False),
        variant("screengraph", "Ours: Screen Graph resolver", 1.8),
    ],
}


def test_ablation_fills_relevance_and_the_table(tmp_path):
    md = render(tmp_path, ablation=ABLATION)
    assert "| 1.80 |" in row(md, "Deeplink relevance")
    assert "MISTRAL_API_KEY not set" in row(md, "Baseline")
    assert "2.0 ms / action" in row(md, "Ours")
    assert "| not measured |" in row(md, "Ours")  # no judge run, so no shared step accuracy
    assert "Settings > Volume" in md  # over-cautious fallback surfaces in section 6


def test_ablation_shares_the_kit_step_accuracy_and_scales_per_query(tmp_path):
    plan = {"response": {"contexts": [{"actions": [{}, {}, {}, {}]}]}}
    (tmp_path / "results.jsonl").write_text(json.dumps(plan) + "\n")
    judge = {**JUDGE, "by_source": {"kit": {"n": 20, "step_accuracy_mean": 2.6}}}
    md = render(tmp_path, ablation=ABLATION, judge=judge)
    ours = row(md, "Ours")
    assert "2.60 (shared extraction)" in ours
    assert "8.0 ms / query (2.0 ms / action)" in ours


def test_cache_mode_latency_and_hit_rate(tmp_path):
    section = {
        "source": "in-process",
        "warmed_with": "20 kit queries",
        "repeat": {"n": 40, "p50_ms": 0.2, "p95_ms": 0.3, "hit_rate": 1.0},
        "paraphrase": {"n": 200, "p50_ms": 8.0, "p95_ms": 12.0, "hit_rate": 0.805, "wrong_plan": 0},
        "near_miss": {"n": 60, "hits": 3, "hit_rate": 0.05, "false_hit_rate": 0.05, "leaked": []},
    }
    md = render(tmp_path, loadtest={"cache": section})
    assert "| 0.2 | 0.3 |" in row(md, "Cache hit - exact query match")
    assert "80.5%" in row(md, "Semantic cache hit rate")
    assert "not measured" in row(md, "Cold query - full pipeline")
    assert "5.0%" in md


@pytest.mark.parametrize("value", [None, 0.5])
def test_pct(value):
    assert report.pct(value) == ("not measured" if value is None else "50.0%")


JUDGE = {
    "judged": 20,
    "failed": 0,
    "empty_plans": 1,
    "self_graded": 0,
    "step_accuracy_mean": 2.35,
    "by_source": {"kit": {"n": 20, "step_accuracy_mean": 2.35}},
    "steps": {
        "n": 60,
        "verdicts": {"correct": 50, "partial": 6, "wrong": 4},
        "issues": {"not_an_instruction": 4},
    },
    "link_relevance_mean": 1.8,
    "links_judged": 10,
    "order_problems": 2,
    "plans_missing_a_fix": 3,
    "judge_model": "gemini-3-flash-preview",
    "prompt_version": "judge-v1",
    "source": "results.jsonl",
}


def test_judge_fills_step_accuracy(tmp_path):
    md = render(tmp_path, judge=JUDGE)
    assert "| 2.35 |" in row(md, "Step accuracy")
    assert "gemini-3-flash-preview as judge" in md
    assert "not an instruction 4" in md and "2 plans with an ordering problem" in md
    assert "step accuracy (`judge.py`)" not in md


def test_several_judged_runs_report_their_mean_and_range(tmp_path):
    runs = tmp_path / "judge_runs"
    runs.mkdir()
    for n, mean in enumerate((2.65, 2.55, 2.45)):
        (runs / f"run{n}.json").write_text(json.dumps({**JUDGE, "step_accuracy_mean": mean}))
    (runs / "other_prompt.json").write_text(
        json.dumps({**JUDGE, "prompt_version": "judge-v0", "step_accuracy_mean": 0.1})
    )
    md = render(tmp_path, judge=JUDGE)
    assert "| 2.55 |" in row(md, "Step accuracy")  # the mean of the three same-prompt runs
    assert "mean of 3 independent end-to-end runs" in md and "range 2.45-2.65" in md


def test_unseen_plans_join_the_kit_score_weighted_by_plans(tmp_path):
    runs = tmp_path / "judge_runs"
    runs.mkdir()
    for n, mean in enumerate((2.7, 2.5)):
        (runs / f"run{n}.json").write_text(json.dumps({**JUDGE, "step_accuracy_mean": mean}))
    judge = {
        **JUDGE,
        "by_source": {
            "kit": {"n": 20, "step_accuracy_mean": 2.35},
            "unseen": {"n": 15, "step_accuracy_mean": 2.0},
        },
        "by_domain": {
            "Battery": {"n": 5, "step_accuracy_mean": 2.2},
            "Display": {"n": 20, "step_accuracy_mean": 2.35},
        },
    }
    md = render(tmp_path, judge=judge, ablation=ABLATION)
    assert "| 2.34 |" in row(md, "Step accuracy")  # (2.6 * 20 + 2.0 * 15) / 35, kit from the runs
    assert "score 2.60 (mean of the independent runs below)" in md and "(Battery 2.20 (n=5))" in md
    assert "2.60 (shared extraction)" in row(md, "Ours")  # the ablation shares the kit score only


def test_unseen_judged_separately_is_merged_and_its_judge_named(tmp_path):
    unseen = {
        **JUDGE,
        "judge_model": "ministral-14b-latest",
        "self_graded": 15,
        "by_source": {"unseen": {"n": 15, "step_accuracy_mean": 2.0}},
        "by_domain": {"Camera": {"n": 15, "step_accuracy_mean": 2.0}},
    }
    calibration = {**JUDGE, "judge_model": "ministral-14b-latest", "step_accuracy_mean": 1.6}
    md = render(tmp_path, judge=JUDGE, judge_unseen=unseen, judge_calibration=calibration)
    assert "| 2.35 |" in row(md, "Step accuracy")  # different judges: never averaged
    assert "judged by ministral-14b-latest, not gemini-3-flash-preview" in md
    assert "(Camera 2.00 (n=15))" in md
    assert "score 1.60 (`judge_calibration.json`) against 2.00 unseen" in md
    assert "at least as well as the kit" in md and "15 of the unseen plans were written" in md


def test_runs_of_other_plans_do_not_stand_in_for_a_new_judgment(tmp_path):
    runs = tmp_path / "judge_runs"
    runs.mkdir()
    old = [{"id": "row_1", "source": "kit", "score": 3}]
    for n, mean in enumerate((2.7, 2.5)):
        (runs / f"run{n}.json").write_text(json.dumps({**JUDGE, "step_accuracy_mean": mean, "items": old}))
    new = {**JUDGE, "items": [{"id": "row_1", "source": "kit", "score": 2}]}
    md = render(tmp_path, judge=new)
    assert "| 2.35 |" in row(md, "Step accuracy")  # judge.json alone, not the old runs' 2.60
    assert "2 earlier runs in `eval/results/judge_runs/` were left out" in md
    (runs / "run2.json").write_text(json.dumps({**new, "step_accuracy_mean": 2.35}))
    md = render(tmp_path, judge=new)  # judge.json is now one of the runs: all three count
    assert "| 2.52 |" in row(md, "Step accuracy") and "left out" not in md


def test_calibration_of_other_plans_is_not_compared(tmp_path):
    unseen = {**JUDGE, "judge_model": "m", "by_source": {"unseen": {"n": 15, "step_accuracy_mean": 2.0}}}
    kit = {**JUDGE, "items": [{"id": "row_1", "source": "kit", "n_steps": 9}]}
    calibration = {**kit, "judge_model": "m", "items": [{"id": "row_1", "source": "kit", "n_steps": 4}]}
    md = render(tmp_path, judge=kit, judge_unseen=unseen, judge_calibration=calibration)
    assert "judged by m, not" in md and "judge_calibration" not in md


def test_unseen_with_the_same_judge_joins_the_headline(tmp_path):
    unseen = {**JUDGE, "by_source": {"unseen": {"n": 15, "step_accuracy_mean": 2.0}}}
    md = render(tmp_path, judge=JUDGE, judge_unseen=unseen)
    assert "| 2.20 |" in row(md, "Step accuracy")  # (2.35 * 20 + 2.0 * 15) / 35
    assert "Across all four domains" in md


def test_api_cold_cost_and_models(tmp_path):
    cold = {"n": 35, "hits": 0, "hit_rate": 0.0, "p50_ms": 3900.0, "p95_ms": 6400.0, "mean_cost_usd": 0.0}
    cold["models"] = {"ministral-14b-latest": 30, "rules": 5}
    load = {"api": {"source": "HTTP against x", "cold": cold}}
    args = SimpleNamespace(model=None, embeddings="e", env="x", results_dir=tmp_path)
    (tmp_path / "loadtest.json").write_text(json.dumps(load))
    md = report.build(args)
    assert "| $0.0000 |" in row(md, "Cold query average inference cost")
    assert "**Model(s):** ministral-14b-latest (30 cold queries), rules (5 cold queries)" in md
    assert "| 6400.0 |" in row(md, "Cold query - full pipeline")


def test_api_near_miss_sources_are_explained(tmp_path):
    nm = {
        "n": 60,
        "hits": 32,
        "hit_rate": 0.533,
        "false_hit_rate": 0.3,
        "hits_by_source": {"own_kit_answer": 16, "other_kit_answer": 2, "earlier_near_miss": 14},
    }
    md = render(tmp_path, loadtest={"api": {"source": "HTTP", "near_miss": nm}})
    assert "30.0% (target <= 2%)" in md
    assert "16 their own row's, 2 another row's" in md
    assert "earlier near miss 14" in md


def test_near_miss_limitation_counts_only_kit_answers(tmp_path):
    leaked = [
        {"query": "I want my screen dark", "differs_in": "intent", "source": "own_kit_answer"},
        {"query": "screen cracked", "differs_in": "symptom", "source": "earlier_near_miss"},
    ]
    nm = {"n": 60, "hits": 2, "hit_rate": 0.03, "false_hit_rate": 0.017, "leaked": leaked}
    md = render(tmp_path, loadtest={"api": {"source": "HTTP", "near_miss": nm}})
    assert "**Near-miss cache hits.** 1 of 60 near misses were served a kit answer (intent: 1)" in md
    assert "screen cracked" not in md and "slot guard compares" not in md
