"""Measure the cache against data/gold/cache_paraphrases.jsonl.

Run from api/:  python scripts/eval_cache.py [--sweep]

Reports what block A3 scores: repeat-hit rate and latency, paraphrase-hit rate, and the
false-hit rate on near misses (a different problem that sounds alike must NOT hit).
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cache
from app.config import settings
from app.models import CacheEntry
from app.pipeline.slots import extract_slots

ROOT = Path(__file__).resolve().parents[2]
SET = ROOT / "data" / "gold" / "cache_paraphrases.jsonl"
HASH = "article-hash"
PLAN_OF = {}


def load_set() -> list[dict]:
    lines = SET.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def warm(cases: list[dict], with_variations: bool = True) -> None:
    """Store each query as if the pipeline had just solved it."""
    cache.clear()
    for case in cases:
        query = case["query"]
        PLAN_OF[case["id"]] = {"contexts": [{"goal": case["id"]}]}
        cache.put(
            CacheEntry(
                key=cache.make_key(query, HASH),
                siis_hash=HASH,
                slots=extract_slots(query),
                plan=PLAN_OF[case["id"]],
                query_texts=[query, *(case["paraphrases"][:3] if with_variations else [])],
                created_at=time.time(),
            )
        )


def measure(cases: list[dict]) -> dict:
    repeat_hits, repeat_ms = 0, 0.0
    for case in cases:
        start = time.perf_counter()
        hit = cache.lookup(case["query"], extract_slots(case["query"]), HASH)
        repeat_ms += (time.perf_counter() - start) * 1000
        repeat_hits += hit is not None and hit.tier == "exact"

    para_total = para_hits = para_wrong = 0
    para_ms = 0.0
    for case in cases:
        # only phrasings the cache was NOT warmed with, so this measures generalisation
        for text in case["paraphrases"][3:]:
            para_total += 1
            start = time.perf_counter()
            hit = cache.lookup(text, extract_slots(text), HASH)
            para_ms += (time.perf_counter() - start) * 1000
            if hit is None:
                continue
            para_hits += 1
            para_wrong += hit.plan != PLAN_OF[case["id"]]

    false_total = false_hits = 0
    for case in cases:
        for other in cases:
            if other["id"] == case["id"]:
                continue
            for text in other["paraphrases"][3:]:
                false_total += 1
                hit = cache.lookup(text, extract_slots(text), HASH)
                false_hits += hit is not None and hit.plan == PLAN_OF[case["id"]]
        break  # one anchor is enough; every paraphrase is checked against it

    return {
        "repeat_rate": repeat_hits / len(cases),
        "repeat_ms": repeat_ms / len(cases),
        "para_rate": para_hits / para_total,
        "para_ms": para_ms / para_total,
        "para_wrong": para_wrong,
        "false_rate": false_hits / false_total,
    }


def report(label: str, result: dict) -> None:
    print(
        f"{label:>10}  repeat {result['repeat_rate']:4.0%} ({result['repeat_ms']:.2f} ms)"
        f"   paraphrase {result['para_rate']:4.0%} ({result['para_ms']:.0f} ms)"
        f"   wrong-plan {result['para_wrong']}"
        f"   false hits {result['false_rate']:4.0%}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", action="store_true", help="try several similarity thresholds")
    args = parser.parse_args()

    cases = load_set()
    print(
        f"{len(cases)} cached queries, {sum(len(c['paraphrases'][3:]) for c in cases)} held-out paraphrases"
    )
    print("targets: repeat >= 90%, paraphrase >= 80%, false hits <= 2%\n")

    thresholds = [0.70, 0.75, 0.80, 0.85, 0.90] if args.sweep else [settings.cache_sim_threshold]
    for threshold in thresholds:
        settings.cache_sim_threshold = threshold
        warm(cases)
        report(f"sim>={threshold:.2f}", measure(cases))
    cache.clear()


if __name__ == "__main__":
    main()
