"""Regenerates docs/metrics.md from eval outputs, in the Theme 2 spec's Appendix C layout.

Reads whatever exists in eval/results/ and never invents a number: a cell with no measurement
behind it says "not measured" and the reason.

    gates.json     gate_replica.py   section 1 (schema, rules, leaks, catalog validity)
    judge.json     judge.py          section 2 (step accuracy)
    judge_unseen.json  judge.py --api --sets unseen   section 2: the unseen Battery, Camera and
                                     Performance scenarios, joined to the kit score by plan count
    judge_calibration.json  judge.py --out   section 2: the kit plans graded by the unseen set's judge,
                                     when that differs from judge.json's (judges grade on different curves)
    judge_runs/    judge.py --out    section 2: further independent runs (results regenerated cold and
                                     judged again); with two or more, step accuracy is their mean
    ablation.json  ablation.py       sections 2 and 5 (deeplink relevance, mapping ablation)
    loadtest.json  loadtest.py       sections 3 and 4 (latency, cache hit rate, cost)

A response with empty contexts passes every format rule trivially, so section 1 is reported as
not measured until the engine returns non-empty plans.

Usage (from the repo root):
    python eval/report.py
    python eval/report.py --model "ministral-14b-latest" --env "2 vCPU / 4 GB / Ubuntu 22.04"
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evalkit.paths import API_DIR, REPO_ROOT, RESULTS_DIR, RESULTS_JSONL

OUT_PATH = REPO_ROOT / "docs" / "metrics.md"
NM = "not measured"

# Screens a gold step needs that the catalog has no entry for (data/gold tier "dummy"), and why
# a correct plan still carries bixby://dummy_positive or no link there. Written from the catalog
# audit in data/gold/README.md; the report adds the counts measured from the gold set.
CATALOG_GAPS = (
    "Software update, Safe mode, Dark mode, auto-rotate, font size, per-app storage and a true "
    "Factory data reset have no catalog entry, so those steps resolve to `bixby://dummy_positive` "
    "or stay manual. `DL-0022` is the *auto* factory reset, not Factory data reset."
)
# Observations about the kit and the engine that no single run file records.
KIT_MISMATCH = (
    "Some kit rows pair a complaint with an article about another problem: row 7 (a dark screen, the "
    "Multi window article), row 10 (blanking at the hinge, camera flicker under lights), row 20 (a "
    'distorted screen, screen rotation) and rows 3, 11 and 17 (a garbled "things to check first" page '
    "with its words run together). The engine may only use the article, so it answers with the "
    "article's nearest fixes, and the judge marks those plans irrelevant or incomplete."
)
MULTI_INTENT_CACHE = (
    "The cache's slot check looks at what broke, not at how many problems a complaint names, so a two-problem "
    "complaint on an article already solved for one of them can be served that one-problem plan from "
    "the semantic cache."
)


def load(name: str, results_dir: Path) -> dict | None:
    path = results_dir / name
    return json.loads(path.read_text()) if path.exists() else None


def _kit_scores(judged: dict) -> list[tuple]:
    return sorted(
        (r.get("id"), r.get("score")) for r in judged.get("items") or [] if r.get("source") == "kit"
    )


def _kit_plans(judged: dict) -> list[tuple]:
    """Which kit plans were judged, by step count: two judges of the same results.jsonl agree on it."""
    return sorted(
        (r.get("id"), r.get("n_steps")) for r in judged.get("items") or [] if r.get("source") == "kit"
    )


def judge_runs(results_dir: Path, judge: dict | None) -> tuple[list[dict], int]:
    """Independent end-to-end runs judged with the same prompt as judge.json: each run regenerates the
    kit plans cold (the free-tier models answer a little differently every time) and judges them, so
    their spread is the engine's run-to-run variance, which one run cannot show.

    The runs count only while judge.json is one of them (same kit plans, same scores): after an engine
    change judge.json is re-judged, and runs of the old engine must not stand in for it. Returns the
    runs to use and how many were set aside as stale."""
    folder = results_dir / "judge_runs"
    if not judge or not folder.is_dir():
        return [], 0
    runs = [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]
    runs = [r for r in runs if r.get("prompt_version") == judge.get("prompt_version") and r.get("judged")]
    mine = _kit_scores(judge)
    if runs and not any(_kit_scores(r) == mine for r in runs):
        return [], len(runs)
    return runs, 0


def pct(value: float | None) -> str:
    return NM if value is None else f"{value * 100:.1f}%"


def ms(value: float | None) -> str:
    return NM if value is None else f"{value:.1f}"


def commit_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def engine_models(load: dict | None) -> str | None:
    """The models that answered the cold pass of `loadtest.py --mode api`, most used first."""
    models = (((load or {}).get("api") or {}).get("cold") or {}).get("models") or {}
    return (
        ", ".join(f"{model_name(m)} ({n} cold {'query' if n == 1 else 'queries'})" for m, n in models.items())
        or None
    )


def embed_model() -> str:
    """The embedding model id as configured, read from config.py without importing the engine."""
    text = (API_DIR / "app" / "config.py").read_text()
    match = re.search(r'embed_model:\s*str\s*=\s*"([^"]+)"', text)
    return f"{match.group(1)} (ONNX via fastembed)" if match else NM


def llm_cooldown_s() -> str:
    """How long a model answering 429/5xx is skipped, read from config.py without importing the engine."""
    text = (API_DIR / "app" / "config.py").read_text()
    match = re.search(r"llm_cooldown_s:\s*float\s*=\s*([\d.]+)", text)
    return f"{float(match.group(1)):g} s" if match else "a short"


def model_name(model: str) -> str:
    """A cold answer with no model is a decline (`no_match`), not an unnamed model."""
    return "no model (declined, no_match)" if model == "none" else model


def local_env() -> str:
    ram = ""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True)
            ram = f" / {int(out.stdout) / 2**30:.0f} GB RAM"
        elif Path("/proc/meminfo").exists():
            kb = int(Path("/proc/meminfo").read_text().split()[1])
            ram = f" / {kb / 2**20:.0f} GB RAM"
    except (OSError, ValueError, subprocess.CalledProcessError):
        pass
    return f"{os.cpu_count()} vCPU{ram} / {platform.system()} {platform.release()} (measurement machine)"


# ------------------------------------------------------------------------------------------ sections


def section1(gates: dict | None) -> list[str]:
    rows = [
        ("Schema-valid output lines", ">= 99%"),
        ("Rule compliance (Goal / Title / Description syntax)", ">= 95%"),
        ("Absolute URL leaks", "0"),
        ("Deeplink catalog validity (exact URI match)", "100%"),
        ("Auto actions carrying valid actionable deeplink", ">= 90%"),
    ]
    values = [NM] * len(rows)
    note = "No gate replica run found (`eval/results/gates.json`)."
    if gates:
        a1 = (gates.get("blocks", {}).get("A1") or {}).get("detail") or {}
        a2 = (gates.get("blocks", {}).get("A2") or {}).get("detail") or {}
        responses, non_empty = a1.get("responses", 0), a1.get("non_empty", 0)
        if non_empty:
            g4 = (gates["gates"].get("G4") or {}).get("value")
            g5 = (gates["gates"].get("G5") or {}).get("value")
            rules = a1.get("rule_pass_rate") or {}
            syntax = [rules[r] for r in ("goal", "title", "description") if r in rules]
            values = [
                pct(g4),
                pct(sum(syntax) / len(syntax) if syntax else None),
                NM if g5 is None else str(g5),
                pct(a2.get("catalog_validity")),
                pct(a2.get("auto_with_link")),
            ]
            note = f"{non_empty} of {responses} responses had non-empty plans (gate replica, `{gates.get('git_sha')}`)."
        else:
            note = (
                f"The gate replica checked {responses} responses and all had empty `contexts`. Empty plans pass every format rule trivially, "
                "so these cells stay unmeasured rather than reading 100%."
            )
    out = [
        "## 1. Schema & Rule Compliance",
        "Evaluated on sample datasets and held-out validation scenarios.",
        "",
        "| Metric | Target | Measured Value |",
        "| :--- | :--- | :--- |",
    ]
    out += [f"| {name} | {target} | {value} |" for (name, target), value in zip(rows, values, strict=True)]
    return [*out, "", f"_{note}_", *(f"_{n}_" for n in hygiene_notes(gates)), ""]


def hygiene_notes(gates: dict | None) -> list[str]:
    """The spec's section 6 robustness checks that the template's table has no row for: determinism,
    hostile inputs, fallback reasons and one action per screen (spec 4.1)."""
    if not gates:
        return []
    out = []
    hygiene = gates.get("hygiene") or {}
    parts = [
        x["detail"]
        for name in ("deterministic_repeat", "always_200", "pure_json")
        if (x := hygiene.get(name)) and x.get("detail")
    ]
    if parts:
        out.append("Deterministic execution and hygiene (live gate replica): " + "; ".join(parts) + ".")
    adv = gates.get("adversarial")
    if adv:
        failed = "; ".join(
            f"{x['id']} ({x.get('kind')}): {', '.join(x['problems'])}" for x in adv["failures"]
        )
        out.append(
            f"Adversarial inputs (`eval/sets/adversarial.jsonl`: typos, Hinglish, three problems in one, URLs, "
            f"emails, markdown and HTML in the article, a prompt injection, a missing, empty or string article, "
            f"an off-topic article, empty and nonsense complaints, an 18k-character article): {adv['passed']} of "
            f"{adv['n']} returned what the case expects (HTTP 200, schema-valid, no URL, a plan or none, and the "
            "fallback the spec names)" + (f". Failed: {failed}." if failed else ".")
        )
    counts = (gates.get("findings") or {}).get("counts") or {}
    warned = [
        f"{counts[code]['n']} {label}"
        for code, label in (
            ("FALLBACK_MISSING", "empty plans without a fallback reason"),
            ("SAME_SCREEN_SPLIT", "actions opening the same catalog entry as another action of their goal"),
            ("CRITICAL_NOT_LAST", "actions after a critical one"),
        )
        if code in counts
    ]
    if warned:
        out.append("Warnings on rules the table has no row for: " + "; ".join(warned) + ".")
    return out


def ours(ablation: dict | None) -> dict | None:
    if not ablation:
        return None
    return next((v for v in ablation["variants"] if v["key"] == "screengraph" and v["measured"]), None)


def judge_note(judge: dict) -> str:
    steps = judge.get("steps") or {}
    verdicts = steps.get("verdicts") or {}
    issues = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in (steps.get("issues") or {}).items())
    by_source = "; ".join(
        f"{src} {v['step_accuracy_mean']:.2f} (n={v['n']})"
        for src, v in (judge.get("by_source") or {}).items()
        if v.get("step_accuracy_mean") is not None
    )
    by_domain = "; ".join(
        f"{d} {v['step_accuracy_mean']:.2f} (n={v['n']})"
        for d, v in (judge.get("by_domain") or {}).items()
        if v.get("step_accuracy_mean") is not None
    )
    parts = [
        (
            f"Step accuracy: {judge.get('judge_model')} as judge (`eval/judge.py`, "
            f"{judge.get('prompt_version')}) over {judge['judged']} plans from {judge.get('source')}"
        ),
        f"by set: {by_source}" if by_source else "",
        f"by domain: {by_domain}" if by_domain else "",
        (
            f"{steps.get('n', 0)} steps: {verdicts.get('correct', 0)} correct, {verdicts.get('partial', 0)} "
            f"partial, {verdicts.get('wrong', 0)} wrong" + (f" (main issues: {issues})" if issues else "")
        ),
        (
            f"{judge.get('order_problems', 0)} plans with an ordering problem, "
            f"{judge.get('plans_missing_a_fix', 0)} missing an article fix, {judge.get('empty_plans', 0)} empty"
        ),
    ]
    if judge.get("link_relevance_mean") is not None:
        parts.append(
            f"end-to-end link relevance {judge['link_relevance_mean']:.2f} / 2 over "
            f"{judge['links_judged']} catalog links"
        )
    if judge.get("failed"):
        parts.append(f"{judge['failed']} plans could not be judged and are left out")
    if judge.get("self_graded"):
        parts.append(f"{judge['self_graded']} plans were written by the judge's own model family")
    parts.append(
        "The kit ships no reference plans (the spec's `samples/` folder is not in it), so the judge grades "
        "each plan against its own SIIS article, the only ground truth the engine is allowed to use"
    )
    return "_" + ". ".join(p for p in parts if p) + "._"


def kit_score(judge: dict | None, runs: list[dict] | None = None) -> float | None:
    """The kit's step accuracy: the mean of the independent runs when there are two or more."""
    runs = runs or []
    if len(runs) >= 2:
        return sum(r["step_accuracy_mean"] for r in runs) / len(runs)
    if not judge:
        return None
    kit = (judge.get("by_source") or {}).get("kit")
    return kit["step_accuracy_mean"] if kit else judge["step_accuracy_mean"]


def all_domains(
    judge: dict | None,
    runs: list[dict] | None = None,
    unseen_judge: dict | None = None,
    calibration: dict | None = None,
) -> tuple[float | None, str] | None:
    """Kit (Display) and unseen (Battery, Camera, Performance), with a note. The unseen plans come from
    `judge_unseen.json` when it exists. Judges grade on different curves, so the two are averaged into
    one headline (weighted by plans) only when the same model judged both; otherwise the score is None
    and the note compares them under one judge using `judge_calibration.json` (the kit plans graded by
    the unseen set's judge), when that exists."""
    kit = ((judge or {}).get("by_source") or {}).get("kit")
    source = unseen_judge or judge or {}
    unseen = (source.get("by_source") or {}).get("unseen")
    if not kit or not unseen or unseen.get("step_accuracy_mean") is None:
        return None
    k, u = kit_score(judge, runs), unseen["step_accuracy_mean"]
    domains = "; ".join(
        f"{d} {v['step_accuracy_mean']:.2f} (n={v['n']})"
        for d, v in (source.get("by_domain") or {}).items()
        if d != "Display" and v.get("step_accuracy_mean") is not None
    )
    unseen_part = (
        f"the {unseen['n']} held-out unseen scenarios (`eval/sets/unseen.jsonl`, answered cold by a local API "
        f"on an empty cache) score {u:.2f}" + (f" ({domains})" if domains else "")
    )
    kit_part = f"the kit's {kit['n']} Display complaints score {k:.2f}" + (
        " (mean of the independent runs below)" if len(runs or []) >= 2 else ""
    )
    same_judge = source.get("judge_model") == (judge or {}).get("judge_model")
    if same_judge:
        score = (k * kit["n"] + u * unseen["n"]) / (kit["n"] + unseen["n"])
        return score, (
            f"_Across all four domains: {kit_part} and {unseen_part}; the headline is their mean weighted "
            f"by plans ({kit['n'] + unseen['n']})._"
        )
    note = (
        f"_Battery, Camera and Performance: {unseen_part}, judged by {source.get('judge_model')}, not "
        f"{(judge or {}).get('judge_model')}. Judges grade on different curves, so this is not averaged into "
        "the headline, which stays the kit's score"
    )
    cal = (calibration or {}).get("step_accuracy_mean")
    same_plans = calibration and _kit_plans(calibration) == _kit_plans(judge or {})
    if cal is not None and same_plans and calibration.get("judge_model") == source.get("judge_model"):
        verdict = "at least as well as" if u >= cal else "less well than"
        note += (
            f". Under the same judge the kit's {calibration.get('judged')} plans score {cal:.2f} "
            f"(`judge_calibration.json`) against {u:.2f} unseen, so the engine handles the unseen domains "
            f"{verdict} the kit"
        )
    if source.get("self_graded"):
        note += (
            f". {source['self_graded']} of the unseen plans were written by the judge's own model family, so "
            "their score may be optimistic"
        )
    return None, note + "._"


def section2(
    judge: dict | None,
    ablation: dict | None,
    runs: list[dict] | None = None,
    unseen: dict | None = None,
    calibration: dict | None = None,
    stale_runs: int = 0,
) -> list[str]:
    runs = runs or []
    kit = kit_score(judge, runs)
    combined = all_domains(judge, runs, unseen, calibration)
    headline = combined[0] if combined and combined[0] is not None else kit
    step = NM if headline is None else f"{headline:.2f}"
    link = ours(ablation)
    rel = NM if not link else f"{link['all']['relevance_mean']:.2f}"
    out = [
        "## 2. Accuracy Benchmarks",
        "Evaluated against reference ground truth scenarios across Battery, Display, Camera, and Performance.",
        "",
        "| Evaluation Metric | Scale / Anchor | Score |",
        "| :--- | :--- | :--- |",
        f"| Step accuracy (completeness, correctness, ordering) | 0.0 - 3.0 | {step} |",
        f"| Deeplink relevance (exact target screen vs. parent menu) | 0.0 - 2.0 | {rel} |",
        "",
    ]
    if not judge:
        out.append("_Step accuracy: not measured yet — `eval/judge.py` needs the engine's extracted steps._")
    else:
        if combined:
            out.append(combined[1])
        if stale_runs:
            out.append(
                f"_{stale_runs} earlier runs in `eval/results/judge_runs/` were left out: judge.json is not one "
                "of them, so they judged other plans (an older engine or results.jsonl)._"
            )
        if len(runs) >= 2:
            means = [r["step_accuracy_mean"] for r in runs]
            out.append(
                f"_Kit step accuracy is the mean of {len(runs)} independent end-to-end runs, each regenerating "
                f"the kit plans cold and judging them ({', '.join(f'{m:.2f}' for m in means)}; range "
                f"{min(means):.2f}-{max(means):.2f}). The free-tier models answer a little differently each "
                "time, and the judge's verdict on the mismatched kit pairs (section 6) swings with them. "
                "The details below are for the run committed as results.jsonl._"
            )
        out.append(judge_note(judge))
    if link:
        a, by = link["all"], link["by_owner"]
        split = ", ".join(
            f"{owner} {g['relevance_mean']:.2f} (n={g['n']})" for owner, g in sorted(by.items()) if g.get("n")
        )
        out.append(
            f"_Deeplink relevance: the Screen Graph resolver on {a['n']} hand-labelled gold steps "
            f"(`data/gold/deeplink_gold.jsonl`), step by step rather than end to end. Precision@1 "
            f"{pct(a['p_at_1'])} on the {a['p_at_1_n']} steps with a real catalog answer. By labeller: {split}. "
            "The mapping lane tuned its thresholds on this file, so its own labels are the more optimistic half._"
        )
    return [*out, ""]


def _latency_rows(load: dict | None) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    rows = [
        ("Cache hit - exact query match", "<= 300 ms", "repeat"),
        ("Cache hit - unseen semantic paraphrase", "<= 300 ms", "paraphrase"),
        ("Cold query - full pipeline extraction & mapping", "<= 8000 ms", "cold"),
    ]
    notes = []
    api = (load or {}).get("api")
    cache = (load or {}).get("cache")
    out = []
    for name, target, path in rows:
        src = api if api and api.get(path) else (cache if cache and cache.get(path) else None)
        s = (src or {}).get(path)
        if not s:
            out.append((name, target, NM, NM))
            continue
        out.append((name, target, ms(s["p50_ms"]), ms(s["p95_ms"])))
    if api:
        pooled = bool((api.get("cold") or {}).get("sources"))
        notes.append(
            f"Measured over HTTP against {_where(api['source'])}, server-side `X-Latency-Ms` where the API "
            "sends it. "
            "Cache rows are timed on the calls that hit, the cold row on the calls that missed"
            + (" the cache in any pass, since each of those ran the full pipeline." if pooled else ".")
        )
    elif cache:
        notes.append(
            "Cache rows are the in-process lookup (`eval/loadtest.py --mode cache`): Tier 0 / Tier 1 lookup "
            "time only, without HTTP or the final scrub. The cold path needs the engine and is not measured yet."
        )
    return out, notes


def _where(source: str) -> str:
    """Where the API ran, without printing its URL (metrics.md is graded like any output: no URLs)."""
    local = any(h in source for h in ("localhost", "127.0.0.1", "0.0.0.0"))
    return "a local API started on an empty cache" if local else "the deployed API"


def section3(load: dict | None) -> list[str]:
    rows, notes = _latency_rows(load)
    sections = [s for s in ((load or {}).get("api"), (load or {}).get("cache")) if s]
    ns = []
    small = []
    for s in sections[:1]:
        ns = [
            f"{p} n={s[p].get('timed_n', s[p]['n'])}"
            + (f" of {s[p]['n']}" if s[p].get("timed_n", s[p]["n"]) != s[p]["n"] else "")
            for p in ("repeat", "paraphrase", "cold")
            if s.get(p)
        ]
        small = [
            p for p in ("repeat", "paraphrase", "cold") if s.get(p) and s[p].get("timed_n", s[p]["n"]) < 30
        ]
        notes += [n if n.rstrip().endswith(".") else n.rstrip() + "." for n in s.get("notes") or []]
        cold = s.get("cold") or {}
        if cold.get("sources"):
            src = cold["sources"]
            cp = cold.get("cold_pass") or {}
            notes.append(
                f"The cold row pools every full-pipeline run: {src.get('cold_pass', 0)} from the cold pass "
                f"(kit + unseen), {src.get('paraphrase_misses', 0)} paraphrases and "
                f"{src.get('near_miss_misses', 0)} near misses the cache missed"
                + (
                    f"; the cold pass alone: p50 {ms(cp.get('p50_ms'))} ms, p95 {ms(cp.get('p95_ms'))} ms "
                    f"(n={cp.get('timed_n')})."
                    if cp.get("timed_n")
                    else "."
                )
            )
    out = [
        "## 3. Latency Benchmarks (N >= 30 requests per path)",
        "",
        "| Execution Path | Target (P95) | P50 (ms) | P95 (ms) |",
        "| :--- | :--- | :--- | :--- |",
    ]
    out += [f"| {name} | {target} | {p50} | {p95} |" for name, target, p50, p95 in rows]
    out.append("")
    if ns:
        notes.append("Samples: " + ", ".join(ns) + ".")
    if small:
        notes.append(
            f"Below the template's N >= 30: {', '.join(small)}. Treat those percentiles as indicative."
        )
    return [*out, *(f"_{n}_" for n in notes), ""]


def section4(load: dict | None) -> list[str]:
    src = (load or {}).get("api") or (load or {}).get("cache") or {}
    para = src.get("paraphrase")
    cold_cost = ((load or {}).get("api") or {}).get("cold", {}).get("mean_cost_usd")
    out = [
        "## 4. Operational Cost & Cache Efficacy",
        "",
        "| Metric Item | Target | Measured Value |",
        "| :--- | :--- | :--- |",
        f"| Cold query average inference cost | Tracked | {NM if cold_cost is None else f'${cold_cost:.4f}'} |",
        "| Cache hit inference cost | $0.00 | $0.00 (no LLM call on the hit path, by construction) |",
        f"| Semantic cache hit rate (on unseen paraphrases) | >= 80% | {pct(para and para['hit_rate'])} |",
        "| Cost derivation method | - | (prompt tokens + completion tokens) × rate |",
        "",
    ]
    cold = ((load or {}).get("api") or {}).get("cold") or {}
    if cold_cost is not None:
        models = ", ".join(f"{model_name(m)} {n}" for m, n in (cold.get("models") or {}).items())
        tokens = (
            f" Token use is tracked per request (`/v1/trace`): {cold['mean_tokens_in']:.0f} prompt + "
            f"{cold['mean_tokens_out']:.0f} completion tokens per cold query on average "
            f"(n={cold['tokens_measured']})."
            if cold.get("tokens_measured")
            else ""
        )
        out.append(
            f"_Mean `meta.cost_usd` over {cold.get('timed_n', cold['n'])} cold queries, priced from the token "
            f"counts at the rates in `api/app/config.py` (`llm_prices`); models that answered: {models}. Models "
            f"without a listed rate run on a free plan and cost $0.{tokens}_"
        )
    if para:
        if "wrong_plan" in para:
            by = para.get("hits_by_source") or {}
            again = by.get("same_complaint_paraphrase", 0)
            second = (
                f"; {again} served the plan of an earlier paraphrase of the same complaint (that one missed, "
                "ran cold and was stored, so the complaint now has two plans)"
                if again
                else ""
            )
            same = (
                f"; {para['same_plan']} of {para['n']} got exactly the plan their original query got"
                if "same_plan" in para
                else ""
            )
            plans = (
                f"{para['wrong_plan']} hits served a plan solved for another complaint"
                if by
                else f"{para['wrong_plan']} hits served a plan other than their original query's"
            ) + f"{second}{same}."
        else:
            plans = "whether each hit served the right plan was not checked in this run."
        out.append(
            f"_Hit rate over {para['n']} held-out paraphrases (`eval/sets/paraphrases.jsonl`, never used to warm "
            f"the cache); {plans}" + (f" {src['warmed_with']}." if src.get("warmed_with") else "") + "_"
        )
    nm = src.get("near_miss")
    if nm:
        out.append(
            f"_False hits on {nm['n']} near misses (same article, different problem): {pct(nm.get('false_hit_rate'))} "
            "(target <= 2%)._"
        )
        by = nm.get("hits_by_source")
        if by:
            other = {k: v for k, v in by.items() if k not in ("own_kit_answer", "other_kit_answer")}
            out.append(
                f"_Counted as false hits: near misses served a kit answer ({by.get('own_kit_answer', 0)} their own "
                f"row's, {by.get('other_kit_answer', 0)} another row's with the same article). The live pass also "
                "caches every answer it computes, so a near miss can hit an answer created earlier in the same "
                "test; those are reported, not counted: "
                + (", ".join(f"{k.replace('_', ' ')} {v}" for k, v in other.items()) or "none")
                + "._"
            )
    return [*out, ""]


def actions_per_plan(results: Path) -> float | None:
    """Mean actions per plan in the submitted results file: one deeplink lookup per action."""
    counts = []
    if results.exists():
        for line in results.read_text().splitlines():
            if not line.strip():
                continue
            contexts = (json.loads(line).get("response") or {}).get("contexts") or []
            n = sum(len(g.get("actions") or []) for g in contexts if isinstance(g, dict))
            if n:
                counts.append(n)
    return sum(counts) / len(counts) if counts else None


def section5(ablation: dict | None, shared: float | None = None, per_plan: float | None = None) -> list[str]:
    out = [
        "## 5. Architectural Ablation Analysis",
        "",
        "| Architecture Variant | Step Accuracy | Latency (P95) | Cost / Query | Key Observations |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    if not ablation:
        names = (
            "Baseline: Full LLM Deeplink Mapping",
            "Variant A: Hybrid BM25 + Dense Embedding Retrieval",
            "Variant B: Pure Rules-Based Deeplink Mapping",
        )
        out += [f"| {n} | {NM} | {NM} | {NM} | {NM} |" for n in names]
        return [*out, ""]
    step_cell = NM if shared is None else f"{shared:.2f} (shared extraction)"
    for v in ablation["variants"]:
        if not v["measured"]:
            out.append(f"| {v['name']} | {NM} | {NM} | {NM} | {NM}: {v['reason']} |")
            continue
        a = v["all"]
        obs = (
            f"deeplink relevance {a['relevance_mean']:.2f}/2, P@1 {pct(a['p_at_1'])}, right tier "
            f"{pct(a['tier_accuracy'])}, wrong or unsafe link on {pct(a['wrong_link_rate'])} of steps"
        )
        step_p95, step_cost = v["latency_ms"]["p95"], v["cost_per_step_usd"] or 0.0
        if per_plan:
            latency = f"{step_p95 * per_plan:.1f} ms / query ({step_p95:.1f} ms / action)"
            cost = f"${step_cost * per_plan:.5f}" if step_cost else "$0.00"
        else:
            latency = f"{step_p95:.1f} ms / action"
            cost = f"${step_cost:.5f} / action" if step_cost else "$0.00"
        out.append(f"| {v['name']} | {step_cell} | {latency} | {cost} | {obs} |")
    g = ablation["gold"]
    tiers = ", ".join(f"{n} {t}" for t, n in g["by_tier"].items())
    per_query = (
        f"Per-query latency and cost are the per-action p95 and cost times the {per_plan:.1f} actions of the "
        "mean submitted plan, one lookup per action, so the latency is an upper bound. "
        if per_plan
        else ""
    )
    out += [
        "",
        (
            f"_Mapping-only ablation: every variant maps the same {g['n']} gold steps ({tiers}). The "
            "variants only choose links, so they share one extraction and one step-accuracy score (the "
            "kit's score from section 2); they differ in deeplink relevance (0-2 rubric in "
            f"`eval/evalkit/relevance.py`), precision@1 and safety. {per_query}"
            f"Commit `{ablation.get('commit')}`._"
        ),
        "",
    ]
    return out


def section6(ablation: dict | None, load: dict | None, gates: dict | None, judge: dict | None) -> list[str]:
    items = [f"**Catalog gaps.** {CATALOG_GAPS}"]
    link = ours(ablation)
    if ablation:
        g = ablation["gold"]["by_tier"]
        items.append(
            f"**Dummy rate by catalog gap.** {g.get('dummy', 0)} of {ablation['gold']['n']} gold steps need a "
            f"Settings screen the catalog lacks, and {g.get('manual', 0)} are physical steps that take no link."
        )
    if link:
        wrong_dummy = [m for m in link["misses"] if m["gold_tier"] == "catalog" and m["got_tier"] == "dummy"]
        if wrong_dummy:
            screens = "; ".join(sorted({m["screen"] for m in wrong_dummy}))
            items.append(
                f"**Over-cautious links.** {len(wrong_dummy)} steps with a real catalog entry fell back to the "
                f"placeholder because the match scored under the catalog floor: {screens}."
            )
    cache = (load or {}).get("api") or (load or {}).get("cache") or {}
    nm = cache.get("near_miss")
    counted = [
        leak
        for leak in (nm or {}).get("leaked") or []
        if leak.get("source", "own_kit_answer") in ("own_kit_answer", "other_kit_answer")
    ]
    if counted:
        axes = Counter(leak.get("differs_in") or "?" for leak in counted)
        items.append(
            f"**Near-miss cache hits.** {len(counted)} of {nm['n']} near misses were served a kit answer "
            f"({', '.join(f'{k}: {v}' for k, v in sorted(axes.items()))}): "
            + "; ".join(f'"{leak["query"]}"' for leak in counted[:3])
            + ("." if len(counted) <= 3 else "; ...")
        )
    para = cache.get("paraphrase")
    if para and para.get("by_register"):
        weak = {k: v for k, v in para["by_register"].items() if v < 0.8}
        if weak:
            warmed = (
                "over HTTP, with the cache holding each kit query and its generated variations"
                if (load or {}).get("api")
                else "with the cache warmed on the original phrasing only"
            )
            items.append(
                "**Weak paraphrase registers.** "
                + ", ".join(f"{k} {v * 100:.0f}%" for k, v in weak.items())
                + f" hit rate {warmed}."
            )
    if para and para.get("wrong"):
        wrong, hits = para["wrong"], para.get("hits") or para.get("timed_n") or para["n"]
        again = (para.get("hits_by_source") or {}).get("same_complaint_paraphrase", 0)
        items.append(
            f"**Paraphrases served another complaint's plan.** {len(wrong)} of {hits} paraphrase hits: "
            + "; ".join(f'"{w["query"]}" ({w["row_id"]})' for w in wrong[:3])
            + (f"; and {len(wrong) - 3} more" if len(wrong) > 3 else "")
            + ". The full list is in `eval/results/loadtest.json` (`paraphrase.wrong`)."
            + (
                f" {again} more hit a second plan for the same complaint, made when an earlier paraphrase "
                "missed and ran cold."
                if again
                else ""
            )
        )
    items.append(f"**Mismatched kit pairs.** {KIT_MISMATCH}")
    cold = cache.get("cold") or {}
    if cold.get("models"):
        answered = ", ".join(
            f"{model_name(m)} {n}" for m, n in sorted(cold["models"].items(), key=lambda kv: -kv[1])
        )
        items.append(
            f"**Free-tier variance.** Cold answers in the load test came from {answered}. Which model "
            f"answers depends on the free tier's load (a 429 puts a model on a {llm_cooldown_s()} cooldown, "
            "and past the prefer deadline the faster model's answer is taken), so the same complaint can get "
            "a different plan on another run; the cache then serves the first one identically."
        )
    if judge:
        items.append(
            f"**Completeness.** The model picks at most `extract_max_actions` (8) actions per goal so a "
            f"cold answer stays inside the 8 s budget on the free tier; a second, parallel call (coverage) "
            f"marks the article paragraphs that help, and the ones the model skipped are added from the "
            f"article's own sentences, with a numbered procedure's general-fix steps (up to "
            f"`extract_complete_max_actions`, 12). Under load that call moves from 14B to 8B, which judges "
            f"relevance less well. The judge marked a missing article fix on "
            f"{judge.get('plans_missing_a_fix')} of {judge.get('n')} plans. "
            f"Critical actions come last as the spec requires, after contacting support; "
            f"{judge.get('order_problems')} plans were still marked with an ordering problem."
        )
    items.append(f"**Multi-intent and the cache.** {MULTI_INTENT_CACHE}")
    pending = []
    if not judge:
        pending.append("step accuracy (`judge.py`)")
    if not (load or {}).get("api"):
        pending.append("cold-path latency and cost over HTTP (`loadtest.py --mode api`)")
    if not gates or not ((gates.get("blocks", {}).get("A1") or {}).get("detail") or {}).get("non_empty"):
        pending.append("schema and rule compliance on non-empty plans (`gate_replica.py`)")
    if ablation and any(not v["measured"] for v in ablation["variants"]):
        pending.append("the LLM mapping baseline (`ablation.py`, needs `MISTRAL_API_KEY`)")
    if pending:
        items.append("**Not measured yet:** " + "; ".join(pending) + ".")
    return ["## 6. Known Edge Cases & System Limitations", "", *(f"* {i}" for i in items), ""]


def build(args) -> str:
    rd = args.results_dir
    gates, judge = load("gates.json", rd), load("judge.json", rd)
    ablation, load_ = load("ablation.json", rd), load("loadtest.json", rd)
    runs, stale_runs = judge_runs(rd, judge)
    header = [
        "# System Performance Metrics & Evaluation Report",
        f"**Model(s):** {args.model or engine_models(load_) or NM}",
        f"**Embeddings:** {args.embeddings or embed_model()}",
        f"**Environment:** {args.env or local_env()}",
        "",
        (
            f"_Generated by `eval/report.py` at commit `{commit_sha()}` on "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. Template: Theme 2 spec, "
            "Appendix C. Every number comes from a run in `eval/results/`; anything without one says "
            "“not measured”._"
        ),
        "",
    ]
    body = [
        *section1(gates),
        *section2(
            judge,
            ablation,
            runs,
            load("judge_unseen.json", rd),
            load("judge_calibration.json", rd),
            stale_runs,
        ),
        *section3(load_),
        *section4(load_),
        *section5(
            ablation,
            kit_score(judge, runs),
            actions_per_plan(getattr(args, "results", RESULTS_JSONL)),
        ),
        *section6(ablation, load_, gates, judge),
    ]
    return "\n".join([*header, *body]).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--model", help="provider/model id(s) of the LLM stages")
    parser.add_argument("--embeddings", help="embedding model id (default: read from api/app/config.py)")
    parser.add_argument("--env", help="vCPU / RAM / OS of the serving machine (default: this machine)")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--results", type=Path, default=RESULTS_JSONL, help="plans for actions per query")
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()
    args.out.write_text(build(args))
    print(f"written to {args.out}")


if __name__ == "__main__":
    main()
