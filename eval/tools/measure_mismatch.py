"""Measure the deterministic half of the mismatch gate (api/app/pipeline/mismatch.py) with no LLM call.

The live gate turns an article away only when call C says `unrelated` AND `mismatch.check` agrees. This
script runs `check` alone, with both paths on, over:

* matched pairs, where it must fire 0 times: the kit rows, the 200 paraphrases and the symptom and intent
  near misses against their row's article, and the unseen scenarios against their own article;
* mismatched pairs, where a fire is recall: near misses that name another part, and every unseen and kit
  complaint against the articles of the other domains.

Usage (from the repo root; the API's venv, since the gate needs wordninja's word list):
    api/.venv/Scripts/python.exe eval/tools/measure_mismatch.py [--show 10] [--out FILE]
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "eval"))

from evalkit.paths import use_api_package
from evalkit.sets import load_kit, load_set

use_api_package()
os.environ.setdefault("ONECLICK_DATA", str(REPO_ROOT / "data"))

from app.config import settings
from app.pipeline import mismatch
from app.pipeline.deglue import _english
from app.pipeline.normalize import clean_siis, display_query, siis_title

KIT_DOMAIN = "Display"  # every kit article is about the screen


def article_text(siis) -> list[str]:
    """The article as the live gate reads it: its cleaned text plus its title (a superset of the
    segmented sentences and section headings extract.py passes)."""
    text, _ = clean_siis(siis)
    return [text, siis_title(siis) or ""]


def pairs() -> list[dict]:
    kit = load_kit()
    by_row = {row.row_id: row for row in kit}
    unseen = load_set("unseen")
    out = []
    for row in kit:
        out.append({"set": "kit", "id": row.row_id, "query": row.query, "siis": row.siis, "matched": True})
    for p in load_set("paraphrases"):
        siis = by_row[p["row_id"]].siis
        out.append({"set": "paraphrases", "id": p["id"], "query": p["query"], "siis": siis, "matched": True})
    for n in load_set("near_miss"):
        siis = by_row[n["row_id"]].siis
        other_part = n.get("differs_in") == "component"
        out.append(
            {
                "set": "near_miss_component" if other_part else "near_miss_symptom_intent",
                "id": n["id"],
                "query": n["query"],
                "siis": siis,
                "matched": not other_part,
            }
        )
    for u in unseen:
        out.append(
            {"set": "unseen", "id": u["id"], "query": u["query"], "siis": u["siis_response"], "matched": True}
        )
    # Cross-domain: each complaint against every article of another domain.
    articles = [(u["domain"], u["id"], u["siis_response"]) for u in unseen]
    articles += [(KIT_DOMAIN, row.row_id, row.siis) for row in kit]
    complaints = [(u["domain"], u["id"], u["query"]) for u in unseen]
    complaints += [(KIT_DOMAIN, row.row_id, row.query) for row in kit]
    seen_articles: set[str] = set()
    unique_articles = []
    for domain, aid, siis in articles:
        key = clean_siis(siis)[1]
        if key not in seen_articles:
            seen_articles.add(key)
            unique_articles.append((domain, aid, siis))
    for cdomain, cid, query in complaints:
        for adomain, aid, siis in unique_articles:
            if cdomain != adomain:
                out.append(
                    {
                        "set": "cross_domain",
                        "id": f"{cid}|{aid}",
                        "query": query,
                        "siis": siis,
                        "matched": False,
                    }
                )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--show", type=int, default=10, help="list this many fires per set")
    parser.add_argument("--out", type=Path, help="write every result as JSON")
    args = parser.parse_args()
    if not _english():
        print("wordninja's word list did not load: run with the API's venv", file=sys.stderr)
        return 2
    settings.mismatch_component_path = True

    fires: dict[str, Counter] = {}
    totals: Counter = Counter()
    examples: dict[str, list[str]] = {}
    rows = []
    for pair in pairs():
        result = mismatch.check([display_query(pair["query"])], article_text(pair["siis"]))
        name = pair["set"]
        totals[name] += 1
        fires.setdefault(name, Counter())
        if result["path"]:
            fires[name][result["path"]] += 1
            examples.setdefault(name, []).append(f"{pair['id']} ({result['path']}, {result['components']})")
        rows.append({**{k: pair[k] for k in ("set", "id", "query", "matched")}, **result})

    print(f"{'set':<26}{'pairs':>7}{'no_shared_word':>16}{'component_absent':>18}{'rate':>8}")
    false_fires = 0
    for name in totals:
        counts = fires[name]
        fired = sum(counts.values())
        matched = next(r["matched"] for r in rows if r["set"] == name)
        if matched:
            false_fires += counts["component_absent"]
        print(
            f"{name:<26}{totals[name]:>7}{counts['no_shared_word']:>16}{counts['component_absent']:>18}"
            f"{fired / totals[name]:>8.1%}{'' if not matched else '  (must be 0)'}"
        )
    for name, items in examples.items():
        if next(r["matched"] for r in rows if r["set"] == name) or args.show:
            print(f"\n{name}: " + "; ".join(items[: args.show]))
    if args.out:
        args.out.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    # The word path's few fires on matched pairs are known (misspelt paraphrases; call C's verdict and call
    # B's typo-free wording protect them live): the bar is on the component path.
    print(f"\ncomponent_absent fires on matched pairs: {false_fires} (must be 0)")
    return 1 if false_fires else 0


if __name__ == "__main__":
    raise SystemExit(main())
