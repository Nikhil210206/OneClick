"""Latency and cache efficacy per execution path (metrics.md sections 3 and 4). N >= 30 per path.

Two modes:

    cache  In-process, against the engine's two-tier cache. Warms it with the 20 kit queries
           only, then times the lookup for
             repeat      each kit query again (Tier 0)
             paraphrase  the 200 held-out paraphrases (Tier 1; never used to warm)
             near miss   the 60 near misses, same article, must NOT hit
           The live engine also stores 8-10 LLM variations per solved query, which this mode
           cannot, so its paraphrase hit rate is a lower bound. Times exclude HTTP.

    api    Against a running API. Cold pass first (kit + unseen, so the SIIS cache must start
           empty), then repeat, paraphrase and near-miss passes. Reports the server's
           X-Latency-Ms and the client-side time. The cold path is timed on every call that
           missed the cache in any pass (each ran the full pipeline), with its tokens read from
           /v1/trace; paraphrase hits are checked for serving their own kit query's plan.

Usage (from the repo root):
    python eval/loadtest.py --mode cache                 # needs data/build (see evalkit/engine.py)
    python eval/loadtest.py --mode cache --sweep         # also try several similarity thresholds
    python eval/loadtest.py --mode api --api http://localhost:8000
Writes eval/results/loadtest.json (the other mode's section is kept).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evalkit.paths import RESULTS_DIR
from evalkit.sets import KitRow, load_kit, load_set
from evalkit.stats import norm_query, percentile, rate

OUT_PATH = RESULTS_DIR / "loadtest.json"
MIN_N = 30
TARGET_P95_MS = {"repeat": 300.0, "paraphrase": 300.0, "cold": 8000.0}
TARGET_PARAPHRASE_HIT = 0.80
TARGET_FALSE_HIT_MAX = 0.02


def siis_hash(siis: dict | str | None) -> str | None:
    if siis is None:
        return None
    return hashlib.sha256(json.dumps(siis, sort_keys=True).encode()).hexdigest()[:16]


def summarise(latencies: list[float], hits: int, n: int, **extra) -> dict:
    return {
        "n": n,
        "hits": hits,
        "hit_rate": None if not n else round(hits / n, 3),
        "p50_ms": None if not latencies else round(percentile(latencies, 50), 2),
        "p95_ms": None if not latencies else round(percentile(latencies, 95), 2),
        "enough_samples": n >= MIN_N,
        **extra,
    }


# ---------------------------------------------------------------------------------------------- cache


def run_cache(
    kit: list[KitRow], paraphrases: list[dict], near_misses: list[dict], threshold: float | None
) -> dict:
    from evalkit import engine

    if threshold is not None:
        engine.settings.cache_sim_threshold = threshold
    tau = engine.settings.cache_sim_threshold
    cache = engine.cache
    cache.clear()

    hashes = {row.row_id: siis_hash(row.siis) for row in kit}
    for row in kit:
        query = norm_query(row.query)
        cache.put(
            engine.CacheEntry(
                key=cache.make_key(query, hashes[row.row_id]),
                siis_hash=hashes[row.row_id],
                slots=engine.extract_slots(query),
                plan={"row_id": row.row_id},
                query_texts=[query],
                created_at=time.time(),
            )
        )

    def lookup(text: str, row_id: str):
        query = norm_query(text)
        start = time.perf_counter()
        hit = cache.lookup(query, engine.extract_slots(query), hashes[row_id])
        return hit, (time.perf_counter() - start) * 1000

    lat, hits, n = [], 0, 0
    for _ in range(2):
        for row in kit:
            hit, ms = lookup(row.query, row.row_id)
            lat.append(ms)
            n += 1
            hits += hit is not None and hit.tier == "exact"
    repeat = summarise(lat, hits, n)

    lat, hits, wrong, misses = [], 0, 0, []
    for p in paraphrases:
        hit, ms = lookup(p["query"], p["row_id"])
        lat.append(ms)
        if hit is None:
            misses.append({"id": p["id"], "register": p.get("register"), "query": p["query"]})
            continue
        hits += 1
        wrong += hit.plan.get("row_id") != p["row_id"]
    by_register: dict[str, list[int]] = {}
    for p in paraphrases:
        by_register.setdefault(p.get("register") or "?", [0, 0])[1] += 1
    for p in paraphrases:
        if not any(m["id"] == p["id"] for m in misses):
            by_register[p.get("register") or "?"][0] += 1
    paraphrase = summarise(
        lat,
        hits,
        len(paraphrases),
        wrong_plan=wrong,
        by_register={k: round(h / t, 3) for k, (h, t) in sorted(by_register.items())},
        missed=misses,
    )

    from app.cache import semantic

    lat, false_hits, would_hit, leaked = [], 0, 0, []
    for m in near_misses:
        hit, ms = lookup(m["query"], m["row_id"])
        lat.append(ms)
        _, similarity = semantic.best_match(norm_query(m["query"]))
        would_hit += similarity >= tau
        if hit is not None:
            false_hits += 1
            leaked.append({"id": m["id"], "differs_in": m.get("differs_in"), "query": m["query"]})
    near_miss = summarise(
        lat,
        false_hits,
        len(near_misses),
        false_hit_rate=rate(false_hits, len(near_misses)),
        above_threshold_without_guard=would_hit,
        leaked=leaked,
    )
    cache.clear()
    return {
        "source": "in-process cache lookup (no HTTP, no final scrub)",
        "warmed_with": f"{len(kit)} kit queries, original phrasing only (no LLM variations)",
        "threshold": tau,
        "repeat": repeat,
        "paraphrase": paraphrase,
        "near_miss": near_miss,
    }


# ------------------------------------------------------------------------------------------------ api


def _plan_key(result) -> str | None:
    """The served plan as a comparable string; None for an empty plan, which matches nothing."""
    return json.dumps(result.contexts, sort_keys=True) if result.contexts else None


def classify_near_misses(
    near_misses: list[dict],
    near: list,
    kit_rows: list[KitRow],
    kit_cold: list,
    para: list[tuple[str, object]],
) -> tuple[list[dict], dict]:
    """Where each near-miss cache hit's plan came from, found by comparing the plan it served.

    The live pass stores every answer it computes, so a near miss that misses runs cold and is
    cached, and a later near miss on the same article can hit it. Only a hit on a kit answer is the
    false hit the <= 2% target is about (a different problem getting a plan solved for the kit
    query). Hits on answers created during the test are reported separately. The served plan is
    compared rather than `matched_query`, which names the closest phrasing before the slot guards
    and so can differ from the entry actually served.
    """
    kit_plan = {row.row_id: _plan_key(r) for row, r in zip(kit_rows, kit_cold, strict=False)}
    kit_owner = {}
    for row_id, key in kit_plan.items():
        if key is not None:
            kit_owner.setdefault(key, row_id)
    para_plans = {_plan_key(r) for _, r in para if not r.cache_hit and _plan_key(r)}
    earlier: set[str] = set()
    leaked, counts = [], Counter()
    for m, r in zip(near_misses, near, strict=True):
        key = _plan_key(r)
        if r.cache_hit:
            if key is not None and key == kit_plan.get(m["row_id"]):
                source = "own_kit_answer"
            elif key is not None and key in kit_owner:
                source = "other_kit_answer"
            elif key is not None and key in earlier:
                source = "earlier_near_miss"
            elif key is not None and key in para_plans:
                source = "paraphrase_answer"
            else:
                source = "unidentified"
            counts[source] += 1
            leaked.append(
                {
                    "id": m.get("id"),
                    "query": m["query"],
                    "differs_in": m.get("differs_in"),
                    "tier": r.cache_tier,
                    "source": source,
                }
            )
        if key is not None:
            earlier.add(key)
    return leaked, dict(counts)


def classify_paraphrases(
    paraphrases: list[dict], para: list, kit_rows: list[KitRow], kit_cold: list
) -> tuple[dict, list[dict], int]:
    """Where each paraphrase hit's plan came from (spec 6.1: semantically identical input, same plan).

    The plan its own kit query got in the cold pass is the right answer. The live pass also stores every
    answer it computes, so a paraphrase the cache missed runs cold, and a later paraphrase of the same
    complaint can hit that answer: the right problem, but not the original's plan, reported on its own.
    A plan solved for another complaint (another kit query's, or another complaint's paraphrase) is a
    wrong plan. Returns the hit counts by source, the wrong hits, and how many paraphrases got exactly
    their original's plan.
    """
    own = {row.row_id: _plan_key(r) for row, r in zip(kit_rows, kit_cold, strict=False)}
    kit_plans = {key for key in own.values() if key is not None}
    earlier: dict[str, set[str]] = {}  # plan -> rows whose paraphrase missed and produced it
    served, wrong, same = Counter(), [], 0
    for p, r in zip(paraphrases, para, strict=True):
        key, row = _plan_key(r), p["row_id"]
        same += key is not None and key == own.get(row)
        if not r.cache_hit:
            if key is not None:
                earlier.setdefault(key, set()).add(row)
            continue
        if key is not None and key == own.get(row):
            source = "own_kit_answer"
        elif key is not None and row in earlier.get(key, set()):
            source = "same_complaint_paraphrase"
        elif key is not None and key in kit_plans:
            source = "other_kit_answer"
        elif key is not None and key in earlier:
            source = "other_complaint_paraphrase"
        else:
            source = "unidentified"
        served[source] += 1
        if source not in ("own_kit_answer", "same_complaint_paraphrase"):
            wrong.append({"id": p.get("id"), "row_id": row, "query": p["query"], "source": source})
    return dict(served), wrong, same


def _meta(result) -> dict:
    meta = result.body.get("meta") if isinstance(result.body, dict) else None
    return meta if isinstance(meta, dict) else {}


def run_api(
    base_url: str,
    kit: list[KitRow],
    unseen: list[dict],
    paraphrases: list[dict],
    near_misses: list[dict],
    settle_s: float = 0.0,
):
    from evalkit.client import ApiClient

    client = ApiClient(base_url, timeout_s=60.0)
    siis = {row.row_id: row.siis for row in kit}
    notes = []
    health = client.health()
    if health.status_code != 200:
        raise SystemExit(f"{base_url}/health returned {health.status_code}: {health.error or health.body}")

    def timed(results, hit_expected: bool | None) -> dict:
        # Latency is timed on the path the row names: cache hits for repeat and paraphrase, misses
        # (the full pipeline) for cold. A kit row can hit a plan cached for another row with the
        # same article, and a paraphrase the cache misses runs cold; neither belongs in the other's
        # percentiles. Near misses keep every call, since their point is the false-hit rate.
        hits = sum(bool(r.cache_hit) for r in results)
        ok = sum(r.ok for r in results)
        timed_on = results
        if hit_expected is True:
            timed_on = [r for r in results if r.cache_hit]
        elif hit_expected is None:
            timed_on = [r for r in results if not r.cache_hit]
        server = [r.server_ms for r in timed_on if r.server_ms is not None]
        client_ms = [r.client_ms for r in timed_on]
        out = summarise(server or client_ms, hits, len(results), ok_200=ok)
        out["timed_n"] = len(timed_on)
        out["enough_samples"] = len(timed_on) >= MIN_N
        out["client_p50_ms"] = round(percentile(client_ms, 50) or 0.0, 2)
        out["client_p95_ms"] = round(percentile(client_ms, 95) or 0.0, 2)
        out["latency_source"] = "X-Latency-Ms" if server else "client"
        if hit_expected is False:
            out["false_hit_rate"] = rate(hits, len(results))
        costs = [c for c in (_meta(r).get("cost_usd") for r in timed_on) if isinstance(c, (int, float))]
        if costs:
            out["mean_cost_usd"] = round(sum(costs) / len(costs), 6)
        counted = [r for r in timed_on if r.tokens_in is not None]
        if counted:
            out["tokens_measured"] = len(counted)
            out["mean_tokens_in"] = round(sum(r.tokens_in for r in counted) / len(counted), 1)
            out["mean_tokens_out"] = round(sum(r.tokens_out for r in counted) / len(counted), 1)
        out["models"] = dict(Counter(str(_meta(r).get("model") or "none") for r in timed_on).most_common())
        return out

    def ask(query: str, article):
        # A miss ran the full pipeline: its trace holds the tokens spent on the answer (spec 6.3).
        result = client.troubleshoot(query, article)
        return result if result.cache_hit else client.add_tokens(result)

    cold = [ask(row.query, row.siis) for row in kit]
    cold += [ask(u["query"], u.get("siis_response")) for u in unseen]
    warm = sum(bool(r.cache_hit) for r in cold)
    if warm:
        notes.append(
            f"{warm} of {len(cold)} cold-pass calls were answered from the cache (another request with the "
            "same article had just been solved, or the API did not start empty)."
        )
    if settle_s > 0:
        # The 8-10 variations are generated in the background after each answer; paraphrase hits
        # depend on them being indexed, so let the last cold calls' variations land first.
        time.sleep(settle_s)
    repeat = [client.troubleshoot(row.query, row.siis) for _ in range(2) for row in kit]
    para = [ask(p["query"], siis[p["row_id"]]) for p in paraphrases]
    near = [ask(m["query"], siis[m["row_id"]]) for m in near_misses]
    client.close()
    leaked, by_source = classify_near_misses(
        near_misses,
        near,
        kit,
        cold[: len(kit)],
        [(p["row_id"], r) for p, r in zip(paraphrases, para, strict=True)],
    )
    near_section = timed(near, False)
    kit_hits = by_source.get("own_kit_answer", 0) + by_source.get("other_kit_answer", 0)
    near_section.update(
        any_hit_rate=near_section["hit_rate"],
        false_hits=kit_hits,
        false_hit_rate=rate(kit_hits, len(near)),
        hits_by_source=by_source,
        leaked=leaked,
    )

    served, wrong_list, same = classify_paraphrases(paraphrases, para, kit, cold[: len(kit)])
    registers: dict[str, list[int]] = {}
    for p, r in zip(paraphrases, para, strict=True):
        reg = registers.setdefault(p.get("register") or "?", [0, 0])
        reg[0] += bool(r.cache_hit)
        reg[1] += 1
    para_section = timed(para, True)
    para_section.update(
        wrong_plan=len(wrong_list),
        same_plan=same,
        hits_by_source=served,
        wrong=wrong_list,
        by_register={k: round(h / t, 3) for k, (h, t) in sorted(registers.items())},
    )

    # Every call that missed the cache ran the full pipeline, whichever pass sent it: the cold pass
    # alone has fewer misses than the N >= 30 the template asks for, because kit rows share articles.
    passes = {"cold_pass": cold, "paraphrase_misses": para, "near_miss_misses": near}
    misses = {name: [r for r in calls if not r.cache_hit] for name, calls in passes.items()}
    cold_section = timed([r for calls in misses.values() for r in calls], None)
    cold_section["sources"] = {name: len(calls) for name, calls in misses.items()}
    cold_pass = timed(cold, None)
    cold_section["cold_pass"] = {k: cold_pass[k] for k in ("n", "timed_n", "p50_ms", "p95_ms")}
    return {
        "source": f"HTTP against {base_url}",
        "cold": cold_section,
        "repeat": timed(repeat, True),
        "paraphrase": para_section,
        "near_miss": near_section,
        "notes": notes,
    }


# ----------------------------------------------------------------------------------------------- main


def verdicts(section: dict) -> dict:
    out = {}
    for path, target in TARGET_P95_MS.items():
        p95 = (section.get(path) or {}).get("p95_ms")
        out[f"{path}_p95"] = None if p95 is None else p95 <= target
    hit = (section.get("paraphrase") or {}).get("hit_rate")
    out["paraphrase_hit"] = None if hit is None else hit >= TARGET_PARAPHRASE_HIT
    false = (section.get("near_miss") or {}).get("false_hit_rate")
    out["false_hits"] = None if false is None else false <= TARGET_FALSE_HIT_MAX
    return out


def print_section(name: str, section: dict) -> None:
    print(f"\n[{name}] {section['source']}")
    for path in ("cold", "repeat", "paraphrase", "near_miss"):
        s = section.get(path)
        if not s:
            continue
        hit = "-" if s["hit_rate"] is None else f"{s['hit_rate']:.0%}"
        cost = f"   ${s['mean_cost_usd']:.4f}/query" if "mean_cost_usd" in s else ""
        print(
            f"  {path:<11} n={s['n']:<4} timed {s.get('timed_n', s['n']):<4} hit {hit:>5}   p50 {s['p50_ms']} ms   p95 {s['p95_ms']} ms{cost}"
        )
        if path == "cold" and s.get("models"):
            print(f"  cold answers by model: {s['models']}")
    nm = section.get("near_miss") or {}
    if "hits_by_source" in nm:
        print(
            f"  near-miss false hits (served a kit answer): {nm['false_hits']}/{nm['n']} "
            f"= {nm['false_hit_rate']:.1%}; all hits by source: {nm['hits_by_source']}"
        )
    if "above_threshold_without_guard" in nm:
        print(
            f"  slot guard: {nm['above_threshold_without_guard']}/{nm['n']} near misses cleared the similarity "
            f"threshold, {nm['hits']} got through"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mode", choices=["cache", "api"], required=True)
    parser.add_argument("--api", help="base URL for --mode api")
    parser.add_argument("--sweep", action="store_true", help="cache mode: also try thresholds 0.70-0.90")
    parser.add_argument(
        "--settle", type=float, default=12.0, help="api mode: seconds to wait for background variations"
    )
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    kit = load_kit()
    paraphrases, near_misses = load_set("paraphrases"), load_set("near_miss")
    report = json.loads(args.out.read_text()) if args.out.exists() else {}

    if args.mode == "cache":
        section = run_cache(kit, paraphrases, near_misses, threshold=None)
        if args.sweep:
            section["sweep"] = []
            for tau in (0.70, 0.75, 0.80, 0.85, 0.90):
                s = run_cache(kit, paraphrases, near_misses, threshold=tau)
                section["sweep"].append(
                    {
                        "threshold": tau,
                        "paraphrase_hit": s["paraphrase"]["hit_rate"],
                        "wrong_plan": s["paraphrase"]["wrong_plan"],
                        "false_hit_rate": s["near_miss"]["false_hit_rate"],
                    }
                )
    else:
        if not args.api:
            parser.error("--mode api needs --api URL")
        section = run_api(args.api, kit, load_set("unseen"), paraphrases, near_misses, settle_s=args.settle)

    section["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    section["verdicts"] = verdicts(section)
    report[args.mode] = section
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    print_section(args.mode, section)
    for row in section.get("sweep", []):
        print(
            f"  tau {row['threshold']:.2f}: paraphrase hit {row['paraphrase_hit']:.0%}, "
            f"wrong plan {row['wrong_plan']}, false hits {row['false_hit_rate']:.0%}"
        )
    print(f"\nwritten to {args.out}")


if __name__ == "__main__":
    main()
