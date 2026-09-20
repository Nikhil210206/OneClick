"""Interactive helper for labelling `data/gold/deeplink_gold.jsonl`.

The gold set is split three ways (~33 steps each). This shows the top BM25 candidates from the
catalog so a step can be labelled in a few seconds instead of scrolling 578 entries by hand. The
BM25 here is not the engine's retriever — see `evalkit/bm25.py` for why.

Run from the repo root:

    python eval/tools/label_gold.py --owner nikhil               # interactive, type steps one by one
    python eval/tools/label_gold.py --owner karur --todo my.jsonl  # work through {step, screen, verb} lines
    python eval/tools/label_gold.py --search "turn off touch sensitivity" --verb disable

At each step: a number picks that candidate, `d` labels it `bixby://dummy_positive` (no catalog
entry fits but a link is plausible), `m` labels it manual (no link at all), `s` skips, `q` saves
and quits. Lines already in the output file are skipped, so a session can be resumed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evalkit.bm25 import Bm25Index, load_entries
from evalkit.paths import GOLD_PATH, REPO_ROOT
from evalkit.sets import read_jsonl

DUMMY = "bixby://dummy_positive"
PROMPT = "  pick [0-7 / d=dummy / m=manual / s=skip / q=quit]: "


def show(candidates: list[tuple], limit: int = 8) -> None:
    if not candidates:
        print("  (no BM25 match — try different wording, or label it manual)")
        return
    for i, (entry, score) in enumerate(candidates[:limit]):
        flag = "validatable" if entry.validatable else "key-only"
        print(f"  [{i}] {score:5.2f}  {entry.summary()}  ({flag})")


def ask(text: str) -> str:
    try:
        return input(text).strip()
    except EOFError:
        return "q"


def label_one(index: Bm25Index, step: str, screen: str, verb: str, owner: str) -> dict | None:
    """Returns the gold record, or None when the step was skipped. Raises KeyboardInterrupt on quit."""
    print(f"\n{step}")
    if screen or verb:
        print(f"  screen={screen or '-'}  verb={verb or '-'}")
    candidates = index.search(f"{step} {screen}", verb=verb)
    show(candidates)

    while True:
        choice = ask(PROMPT).lower()
        if choice == "q":
            raise KeyboardInterrupt
        if choice == "s":
            return None
        record = {"step": step, "screen": screen, "verb": verb, "owner": owner}
        if choice == "m":
            record["expected_id"] = None
            record["expected_deeplink"] = None
            return record
        if choice == "d":
            record["expected_id"] = None
            record["expected_deeplink"] = DUMMY
            return record
        if choice.isdigit() and int(choice) < len(candidates):
            entry = candidates[int(choice)][0]
            record["expected_id"] = entry.id
            record["expected_deeplink"] = entry.deeplink
            extra = ask("  also-acceptable ids (comma separated, blank for none): ")
            if extra:
                record["acceptable_ids"] = [s.strip() for s in extra.split(",") if s.strip()]
            parents = ask("  parent-menu ids that should still score 1 (blank for none): ")
            if parents:
                record["parent_ids"] = [s.strip() for s in parents.split(",") if s.strip()]
            return record
        print("  not a choice")


def todo_items(args: argparse.Namespace) -> list[dict]:
    if args.todo:
        return read_jsonl(args.todo)
    return []


def interactive_items() -> list[dict]:
    """Type steps until a blank line. Used when there is no todo file."""
    items = []
    while True:
        step = ask("\nstep text (blank to finish): ")
        if not step:
            return items
        items.append({"step": step, "screen": ask("  screen path: "), "verb": ask("  verb: ")})


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=GOLD_PATH)
    p.add_argument("--owner", default="", help="your name, so the three shares can be told apart")
    p.add_argument("--todo", type=Path, help="JSONL of {step, screen, verb} to work through")
    p.add_argument("--search", help="one-shot: print candidates for this text and exit")
    p.add_argument("--verb", default="", help="polarity verb for --search (enable, disable, open, update)")
    p.add_argument("--top", type=int, default=8)
    args = p.parse_args()

    index = Bm25Index.build(load_entries())

    if args.search:
        show(index.search(args.search, verb=args.verb, top_k=args.top), limit=args.top)
        return 0

    done = {r["step"] for r in read_jsonl(args.out)}
    items = todo_items(args) or interactive_items()
    pending = [i for i in items if i["step"] not in done]
    if done:
        print(f"{len(done)} already labelled, {len(pending)} to go")

    records = []
    try:
        for item in pending:
            record = label_one(index, item["step"], item.get("screen", ""), item.get("verb", ""), args.owner)
            if record:
                records.append(record)
    except KeyboardInterrupt:
        print("\nstopping, saving what is labelled so far")

    if not records:
        print("nothing new to write")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    out = args.out.relative_to(REPO_ROOT) if args.out.is_relative_to(REPO_ROOT) else args.out
    print(f"appended {len(records)} labels to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
