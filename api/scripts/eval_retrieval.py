"""Measure deeplink retrieval against data/gold/deeplink_gold.jsonl.

Run from api/:  python scripts/eval_retrieval.py [--rerank] [--no-leaf] [--no-polarity]

Reports precision@1 and recall@3 over the gold steps that have a real catalog answer, plus how
the no-catalog steps (DUMMY / MANUAL) score, which is what the resolver's threshold will use.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import retrieval
from app.config import settings
from app.models import DraftAction, DraftStep
from app.screengraph import entry_for, load, node_docs, sibling_for
from app.screengraph.resolver import offscreen_tier

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "data" / "kit" / "deeplinks.json"
GOLD = ROOT / "data" / "gold" / "deeplink_gold.jsonl"
GOLD_ARTICLES = ROOT / "data" / "gold" / "deeplink_gold_articles.jsonl"
GRAPH = ROOT / "data" / "build" / "screengraph.json"
NO_CATALOG = {"dummy", "manual"}


def load_gold(path) -> list[dict]:
    """Rows in the team schema (data/gold/README.md): tier + expected_id + acceptable_ids."""
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        # the article-worded file still uses the older DUMMY / MANUAL sentinels
        if "tier" not in row:
            sentinel = row.get("expected_id")
            row["tier"] = {"DUMMY": "dummy", "MANUAL": "manual"}.get(sentinel, "catalog")
            if row["tier"] != "catalog":
                row["expected_id"] = None
        rows.append(row)
    return rows


def accepted_ids(case: dict) -> set[str]:
    ids = {case.get("expected_id"), *(case.get("acceptable_ids") or []), *(case.get("alt_ids") or [])}
    return {i for i in ids if i}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rerank", action="store_true", help="force the cross-encoder on every step")
    parser.add_argument("--no-rerank", action="store_true", help="force the fast path only")
    parser.add_argument("--auto", action="store_true", help="rerank only unsure steps (default path)")
    parser.add_argument("--no-leaf", action="store_true", help="ablation: drop the leaf-name boost")
    parser.add_argument("--no-polarity", action="store_true", help="ablation: drop verb polarity")
    parser.add_argument("--verbose", action="store_true", help="print every miss")
    parser.add_argument("--articles", action="store_true", help="use the article-worded gold set")
    parser.add_argument(
        "--graph", action="store_true", help="ablation: search Screen Graph nodes instead of entries"
    )
    args = parser.parse_args()

    if args.no_leaf:
        settings.leaf_weight = 0.0
    if args.no_polarity:
        settings.polarity_bonus = settings.polarity_penalty = 0.0

    graph = load(KIT, GRAPH)
    if args.graph:
        retrieval.build(node_docs(graph))
    else:
        retrieval.build(retrieval.load_catalog_docs(KIT))
    entries = {e["id"]: e for e in json.loads(KIT.read_text(encoding="utf-8"))["deeplinks"]}
    gold = load_gold(GOLD_ARTICLES if args.articles else GOLD)

    hits1 = hits3 = scored = 0
    refused_manual = refused_wrongly = manual_cases = 0
    no_catalog_scores: list[float] = []
    top_scores: list[float] = []
    elapsed = 0.0

    for case in gold:
        action = DraftAction(
            steps=[DraftStep(text=case["step"])],
            screen_path=case["screen"],
            intent_verb=case.get("verb"),
        )
        offscreen = offscreen_tier(action) is not None
        if case["tier"] == "manual":
            manual_cases += 1
            refused_manual += offscreen
            continue
        if offscreen:
            # Refusing a DUMMY step costs nothing (no catalog entry existed); refusing a step
            # with a real catalog answer is a genuine loss.
            if case["tier"] != "dummy":
                refused_wrongly += 1
                scored += 1
                if args.verbose:
                    print(f"  OVER-REFUSED {case['screen']} [{case.get('verb')}] want {case['expected_id']}")
            continue
        start = time.time()
        mode = True if args.rerank else (False if args.no_rerank else None)
        results = retrieval.search(case["screen"], case.get("verb"), k=3, use_rerank=mode)
        elapsed += time.time() - start
        top_ids = [doc_id for doc_id, _ in results]
        if args.graph:
            top_ids = [e for e in (entry_for(n, case.get("verb")) for n in top_ids) if e]
        else:
            # entries are searched directly; the graph only corrects the on/off choice
            top_ids = [sibling_for(e, case.get("verb")) for e in top_ids]
        top_score = results[0][1] if results else 0.0

        if case["tier"] in NO_CATALOG:
            no_catalog_scores.append(top_score)
            continue

        scored += 1
        accepted = accepted_ids(case)
        top_scores.append(top_score)
        if top_ids[:1] and top_ids[0] in accepted:
            hits1 += 1
        if accepted & set(top_ids):
            hits3 += 1
        elif args.verbose:
            got = ", ".join(f"{i} {entries[i]['message']!r}" for i in top_ids)
            print(f"  MISS {case['screen']} [{case.get('verb')}] want {case['expected_id']} got {got}")

    flags = []
    if args.graph:
        flags.append("screen-graph search")
    if args.rerank:
        flags.append("rerank always")
    if args.no_rerank:
        flags.append("rerank never")
    if args.no_leaf:
        flags.append("no-leaf")
    if args.no_polarity:
        flags.append("no-polarity")
    print(f"\nconfig: {', '.join(flags) or 'entry search + graph polarity fix + leaf + polarity'}")
    print(f"gold cases with a catalog answer: {scored}")
    print(f"precision@1: {hits1}/{scored} = {hits1 / scored:.0%}")
    print(f"recall@3:    {hits3}/{scored} = {hits3 / scored:.0%}")
    if manual_cases:
        print(f"physical steps refused before search: {refused_manual}/{manual_cases}")
    print(f"steps with a catalog answer wrongly refused: {refused_wrongly}")
    print(f"mean latency: {elapsed / len(gold) * 1000:.0f} ms per step")
    if top_scores and no_catalog_scores:
        print(
            f"score separation: catalog answers {min(top_scores):.2f}-{max(top_scores):.2f} "
            f"vs no-catalog steps {min(no_catalog_scores):.2f}-{max(no_catalog_scores):.2f}"
        )


if __name__ == "__main__":
    main()
