"""G2-G5 + A1-A5 replica; sends each query twice with an empty SIIS cache.

Our own copy of the organisers' automated scorer (FAQ Theme 2 Q9-Q12, Q21). The points it prints are an
estimate: the organisers publish the blocks and targets, not the weighting inside each block.

Run from the repo root:

    python eval/gate_replica.py --results results.jsonl             # offline: G3, G4, G5, A1, A2, A5
    python eval/gate_replica.py --api http://localhost:8000         # live: G2, G4, G5, A1-A4
    python eval/gate_replica.py --api URL --results results.jsonl --enforce G2,G3,G4,G5

Live mode needs a freshly started API (empty SIIS cache); otherwise cold numbers are not cold.
Writes eval/results/gates.json and exits non-zero when an --enforce'd gate fails or was not measured.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from evalkit.catalog import Catalog, LinkStats, check_deeplinks
from evalkit.checks import A1_RULES, FAIL, Finding, a1_rule_pass, check_response, check_results_line
from evalkit.client import ApiClient, CallResult
from evalkit.paths import REPO_ROOT, RESULTS_DIR
from evalkit.sets import KitRow, load_kit, load_set, read_jsonl
from evalkit.stats import mean_pairwise_jaccard, norm_query, percentile, rate

TARGETS = {
    "G3_coverage": 0.95,
    "G4_schema": 0.90,
    "A3_repeat_p95_ms": 300.0,
    "A3_repeat_hit": 0.90,
    "A3_paraphrase_hit": 0.80,
    "A3_cold_p95_ms": 8000.0,
}
BLOCK_MAX = {"A1": 15, "A2": 15, "A3": 15, "A4": 10, "A5": 5}
EXAMPLES_PER_CODE = 3


class Audit:
    """Accumulates per-response findings, A1 rule passes and link statistics."""

    def __init__(self, catalog: Catalog, goal_mode: str):
        self.catalog = catalog
        self.goal_mode = goal_mode
        self.responses = 0
        self.non_empty = 0
        self.schema_valid = 0
        self.a1_passes: Counter[str] = Counter()
        self.links = LinkStats()
        self.findings: list[tuple[str, Finding]] = []

    def add_response(self, source: str, body: object) -> list[Finding]:
        found = check_response(body, self.goal_mode)
        link_findings, stats = check_deeplinks(body, self.catalog)
        found += link_findings
        self.links.add(stats)
        self.responses += 1
        contexts = body.get("contexts") if isinstance(body, dict) else None
        self.non_empty += bool(contexts)
        self.schema_valid += not any(x.code == "SCHEMA" for x in found)
        for rule, ok in a1_rule_pass(found).items():
            self.a1_passes[rule] += ok
        self.add_findings(source, found)
        return found

    def add_findings(self, source: str, found: list[Finding]) -> None:
        self.findings += [(source, x) for x in found]

    @property
    def url_leaks(self) -> int:
        return sum(1 for _, x in self.findings if x.code == "URL_LEAK")

    def finding_summary(self) -> dict:
        counts: dict[str, dict] = {}
        examples: dict[str, list] = defaultdict(list)
        for source, x in self.findings:
            c = counts.setdefault(x.code, {"severity": x.severity, "block": x.block, "n": 0})
            c["n"] += 1
            if x.severity == FAIL:
                c["severity"] = FAIL
            if len(examples[x.code]) < EXAMPLES_PER_CODE:
                examples[x.code].append({"source": source, "path": x.path, "message": x.message})
        ordered = sorted(counts.items(), key=lambda kv: (kv[1]["severity"] != FAIL, -kv[1]["n"], kv[0]))
        return {"counts": dict(ordered), "examples": dict(examples)}


def gate(passed: bool | None, value: object, target: object, detail: str) -> dict:
    return {"pass": passed, "value": value, "target": target, "detail": detail}


def latency_summary(calls: list[CallResult]) -> dict:
    client = [c.client_ms for c in calls]
    server = [c.server_ms for c in calls if c.server_ms is not None]
    hits = [c for c in calls if c.cache_hit]
    return {
        "n": len(calls),
        "p50_ms": percentile(client, 50),
        "p95_ms": percentile(client, 95),
        "server_p50_ms": percentile(server, 50),
        "server_p95_ms": percentile(server, 95),
        "hit_rate": rate(len(hits), len(calls)),
    }


# --------------------------------------------------------------------------- offline (results.jsonl)


def run_offline(path: Path, kit: list[KitRow], audit: Audit, report: dict) -> None:
    lines = read_jsonl(path)
    report["results_file"] = {"path": str(path), "lines": len(lines)}
    covered: set[str] = set()
    variation_ok = 0
    diversities: list[float] = []
    for i, line in enumerate(lines):
        source = f"results[{i}]"
        line_findings = check_results_line(line)
        audit.add_findings(source, line_findings)
        if not isinstance(line, dict):
            continue
        if isinstance(line.get("response"), dict):
            audit.add_response(source, line["response"])
            if isinstance(line.get("query"), str):
                covered.add(norm_query(line["query"]))
        if not any(x.severity == FAIL and x.block == "A5" for x in line_findings):
            variation_ok += 1
        variations = [v for v in line.get("query_variations") or [] if isinstance(v, str)]
        div = mean_pairwise_jaccard(variations)
        if div is not None:
            diversities.append(div)

    kit_keys = {norm_query(r.query) for r in kit}
    hit = len(kit_keys & covered)
    cov = rate(hit, len(kit_keys))
    report["gates"]["G3"] = gate(
        cov is not None and cov >= TARGETS["G3_coverage"],
        cov,
        TARGETS["G3_coverage"],
        f"{hit}/{len(kit_keys)} kit queries have a line in {path.name}",
    )
    a5 = rate(variation_ok, len(lines))
    report["blocks"]["A5"] = {
        "points": None if a5 is None else round(BLOCK_MAX["A5"] * a5, 2),
        "max": BLOCK_MAX["A5"],
        "detail": {
            "lines_with_8_10_unique_variations": variation_ok,
            "lines": len(lines),
            "mean_pairwise_jaccard": sum(diversities) / len(diversities) if diversities else None,
            "note": "lower Jaccard = more lexically diverse",
        },
    }


# --------------------------------------------------------------------------- live (API)


def run_live(api: ApiClient, kit: list[KitRow], audit: Audit, report: dict, n_para: int, seed: int) -> None:
    health = api.health()
    body_ok = isinstance(health.body, dict) and health.body.get("status") == "ok"
    report["gates"]["G2"] = gate(
        health.status_code == 200 and body_ok,
        health.body,
        {"status": "ok"},
        health.error or f"HTTP {health.status_code}",
    )
    if health.status_code is None:
        report["notes"].append(f"API unreachable at {api.base_url}; live checks skipped")
        return

    all_calls: list[CallResult] = []
    cold: list[CallResult] = []
    repeat: list[CallResult] = []
    nondeterministic = 0
    warm_before_cold = 0
    for row in kit:
        first = api.troubleshoot(row.query, row.siis)
        second = api.troubleshoot(row.query, row.siis)
        all_calls += [first, second]
        audit.add_response(f"{row.row_id}/cold", first.body)
        audit.add_response(f"{row.row_id}/repeat", second.body)
        if first.cache_hit:
            warm_before_cold += 1
        else:
            cold.append(first)
        repeat.append(second)
        if first.ok and second.ok and _plan(first) != _plan(second):
            nondeterministic += 1
    if warm_before_cold:
        report["notes"].append(
            f"{warm_before_cold}/{len(kit)} first calls were already cache hits: "
            "restart the API with an empty "
            "SIIS cache for honest cold numbers"
        )

    by_row = {r.row_id: r for r in kit}
    paraphrases = [p for p in load_set("paraphrases") if p.get("row_id") in by_row]
    para_calls: list[CallResult] = []
    if paraphrases and n_para > 0:
        sample = random.Random(seed).sample(paraphrases, min(n_para, len(paraphrases)))
        for p in sample:
            call = api.troubleshoot(p["query"], by_row[p["row_id"]].siis)
            para_calls.append(call)
            audit.add_response(f"paraphrase/{p.get('id')}", call.body)
    else:
        report["notes"].append("eval/sets/paraphrases.jsonl is empty: paraphrase hit rate not measured")
    all_calls += para_calls

    unseen = load_set("unseen")
    unseen_ok = 0
    for u in unseen:
        call = api.troubleshoot(u["query"], u.get("siis_response"))
        all_calls.append(call)
        found = audit.add_response(f"unseen/{u.get('id')}", call.body)
        schema_ok = not any(x.code == "SCHEMA" for x in found)
        unseen_ok += call.ok and schema_ok and bool(call.contexts)
    if not unseen:
        report["notes"].append("eval/sets/unseen.jsonl is empty: A4 generalization not measured")

    lat = {
        "cold": latency_summary(cold),
        "repeat": latency_summary(repeat),
        "paraphrase": latency_summary(para_calls),
    }
    report["latency"] = lat
    non200 = sum(1 for c in all_calls if c.status_code != 200)
    impure = sum(1 for c in all_calls if c.status_code == 200 and not c.pure_json)
    report["hygiene"] = {
        "always_200": gate(non200 == 0, non200, 0, f"{non200}/{len(all_calls)} calls were not HTTP 200"),
        "pure_json": gate(impure == 0, impure, 0, f"{impure} bodies not served as application/json"),
        "deterministic_repeat": gate(
            nondeterministic == 0,
            nondeterministic,
            0,
            f"{nondeterministic}/{len(kit)} repeats returned a different plan",
        ),
    }

    r, p, c = lat["repeat"], lat["paraphrase"], lat["cold"]
    repeat_ok = _meets(r["p95_ms"], r["hit_rate"], TARGETS["A3_repeat_p95_ms"], TARGETS["A3_repeat_hit"])
    para_ok = None if p["n"] == 0 else (p["hit_rate"] or 0) >= TARGETS["A3_paraphrase_hit"]
    cold_ok = None if c["n"] == 0 else c["p95_ms"] <= TARGETS["A3_cold_p95_ms"]
    parts = {"repeat": repeat_ok, "paraphrase": para_ok, "cold": cold_ok}
    measured = [ok for ok in parts.values() if ok is not None]
    report["blocks"]["A3"] = {
        "points": None if not measured else 5.0 * sum(bool(ok) for ok in measured),
        "max": BLOCK_MAX["A3"],
        "detail": {
            **{f"{k}_pass": v for k, v in parts.items()},
            "targets": {k: v for k, v in TARGETS.items() if k.startswith("A3")},
        },
    }
    a4 = rate(unseen_ok, len(unseen))
    report["blocks"]["A4"] = {
        "points": None if a4 is None else round(BLOCK_MAX["A4"] * a4, 2),
        "max": BLOCK_MAX["A4"],
        "detail": {"unseen_valid_non_empty": unseen_ok, "unseen": len(unseen)},
    }


def _plan(call: CallResult) -> str:
    return json.dumps(call.contexts, sort_keys=True)


def _meets(p95: float | None, hit: float | None, p95_max: float, hit_min: float) -> bool | None:
    if p95 is None:
        return None
    return p95 <= p95_max and (hit or 0) >= hit_min


# --------------------------------------------------------------------------- shared blocks


def finish(audit: Audit, report: dict) -> None:
    n = audit.responses
    g4 = rate(audit.schema_valid, n)
    report["gates"]["G4"] = gate(
        None if g4 is None else g4 >= TARGETS["G4_schema"],
        g4,
        TARGETS["G4_schema"],
        f"{audit.schema_valid}/{n} responses validate against schema.py",
    )
    if n:
        report["gates"]["G5"] = gate(
            audit.url_leaks == 0, audit.url_leaks, 0, f"{audit.url_leaks} leaks in output"
        )
        rule_rates = {rule: audit.a1_passes[rule] / n for rule in A1_RULES}
        report["blocks"]["A1"] = {
            "points": round(BLOCK_MAX["A1"] * sum(rule_rates.values()) / len(rule_rates), 2),
            "max": BLOCK_MAX["A1"],
            "detail": {"rule_pass_rate": rule_rates, "responses": n, "non_empty": audit.non_empty},
        }
        if audit.non_empty < n:
            report["notes"].append(
                f"{n - audit.non_empty}/{n} responses have empty contexts; "
                "empty plans pass format rules trivially"
            )
    links = audit.links
    parts = [x for x in (links.validity, links.auto_link_rate) if x is not None]
    report["blocks"]["A2"] = {
        "points": round(BLOCK_MAX["A2"] * sum(parts) / len(parts), 2) if parts else None,
        "max": BLOCK_MAX["A2"],
        "detail": {
            "catalog_validity": links.validity,
            "auto_with_link": links.auto_link_rate,
            "dummy_rate": links.dummy_rate,
            "actionable_links": links.act_total,
            "catalog_links": links.act_catalog,
            "dummy_links": links.act_dummy,
            "validation_links": links.val_total,
            "auto_actions": links.auto_total,
        },
    }
    report["findings"] = audit.finding_summary()
    measured = {k: v for k, v in report["blocks"].items() if v["points"] is not None}
    report["estimate"] = {
        "points": round(sum(v["points"] for v in measured.values()), 2),
        "max_measured": sum(v["max"] for v in measured.values()),
        "max": sum(BLOCK_MAX.values()),
        "not_measured": sorted(set(BLOCK_MAX) - set(measured)),
    }


# --------------------------------------------------------------------------- output


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.1%}" if value <= 1 else f"{value:.0f}"
    return json.dumps(value) if isinstance(value, (dict, list)) else str(value)


def print_report(report: dict) -> None:
    status = {True: "PASS", False: "FAIL", None: "n/a "}
    print("\nOneClick gate replica (our estimate, not the official scorer)")
    print(
        f"  api: {report.get('api') or '-'}   "
        f"results: {(report.get('results_file') or {}).get('path', '-')}   "
        f"goal mode: {report['goal_mode']}"
    )
    print("\nGATES")
    names = {"G2": "health", "G3": "coverage >=95%", "G4": "schema-valid >=90%", "G5": "zero URL leaks"}
    for g in ("G2", "G3", "G4", "G5"):
        x = report["gates"].get(g)
        if x is None:
            print(f"  {g} {names[g]:<22} n/a   not measured in this mode")
        else:
            print(f"  {g} {names[g]:<22} {status[x['pass']]}  {_fmt(x['value']):<8} {x['detail']}")
    for name, x in report.get("hygiene", {}).items():
        print(f"  -- {name:<22} {status[x['pass']]}  {x['detail']}")
    print("\nBLOCKS (estimated points)")
    for b, x in report["blocks"].items():
        pts = "not measured" if x["points"] is None else f"{x['points']:.1f}/{x['max']}"
        print(f"  {b}  {pts}")
    est = report["estimate"]
    print(f"  total {est['points']:.1f}/{est['max_measured']} measured (of {est['max']})")
    if report.get("latency"):
        print("\nLATENCY (client-side ms)")
        for path, x in report["latency"].items():
            if x["n"]:
                print(
                    f"  {path:<11} n={x['n']:<3} p50={x['p50_ms']:.0f}  p95={x['p95_ms']:.0f}  "
                    f"hit={_fmt(x['hit_rate'])}"
                )
    counts = report["findings"]["counts"]
    if counts:
        print("\nFINDINGS")
        for code, c in list(counts.items())[:15]:
            ex = report["findings"]["examples"][code][0]
            print(
                f"  {c['severity']:<4} {c['block']:<3} {code:<24} x{c['n']:<4} {ex['source']} {ex['path']}: "
                f"{ex['message'][:90]}"
            )
    for note in report["notes"]:
        print(f"\nnote: {note}")
    print()


def git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip() or None
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", help="base URL of the running API, e.g. http://localhost:8000")
    ap.add_argument("--results", type=Path, help="results.jsonl to audit offline")
    ap.add_argument("--paraphrases", type=int, default=60, help="paraphrases to send in live mode")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument(
        "--goal-mode",
        choices=["strict", "lenient"],
        default="strict",
        help="strict = FAQ form with trailing period; lenient also accepts sample_output style",
    )
    ap.add_argument("--enforce", default="", help="comma list of gates that must pass, e.g. G2,G4,G5")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR / "gates.json")
    ap.add_argument("--timeout", type=float, default=30.0, help="per-request timeout in seconds")
    args = ap.parse_args(argv)
    if not args.api and not args.results:
        ap.error("give --api and/or --results")

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "api": args.api,
        "goal_mode": args.goal_mode,
        "gates": {},
        "blocks": {},
        "notes": [],
    }
    kit = load_kit()
    audit = Audit(Catalog.load(), args.goal_mode)
    if args.results:
        run_offline(args.results, kit, audit, report)
    if args.api:
        api = ApiClient(args.api, args.timeout)
        try:
            run_live(api, kit, audit, report, args.paraphrases, args.seed)
        finally:
            api.close()
    finish(audit, report)
    report["blocks"] = dict(sorted(report["blocks"].items()))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print_report(report)
    print(f"wrote {args.out.relative_to(REPO_ROOT) if args.out.is_relative_to(REPO_ROOT) else args.out}")

    enforced = [g.strip().upper() for g in args.enforce.split(",") if g.strip()]
    failed = [g for g in enforced if not (report["gates"].get(g) or {}).get("pass")]
    if failed:
        print(f"ENFORCED GATES FAILED OR NOT MEASURED: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
