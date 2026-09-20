"""Schema and sanity checks for the four eval sets and the deeplink gold file.

Run from the repo root before committing a change to any set:

    python eval/sets/validate_sets.py            # all sets
    python eval/sets/validate_sets.py --set unseen --verbose

Beyond field types this enforces the properties that make the sets *useful* rather than merely
well formed:

- paraphrases and near misses are held out, so none of them may normalise to a kit query. A
  paraphrase that matches exactly would be answered by the Tier 0 cache and would tell us nothing
  about the Tier 1 semantic path.
- near misses must overlap their row query *more* than the paraphrases do on average. That is the
  point of the set: similarity alone cannot separate them, only the slot guard can.
- no set except `adversarial` may contain a URL, an address or markup. In `adversarial` those are
  the payload, so the check is inverted and at least one case must carry one.

Exits non-zero if anything fails, so CI can run it.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evalkit.bm25 import load_entries
from evalkit.checks import check_url_leaks
from evalkit.paths import GOLD_PATH, REPO_ROOT, SETS_DIR
from evalkit.sets import load_kit, read_jsonl
from evalkit.stats import jaccard, mean_pairwise_jaccard, norm_query

SET_NAMES = ("paraphrases", "near_miss", "unseen", "adversarial")

REGISTERS = {"formal", "casual", "keyword", "frustrated", "typo"}
DIFFERS_IN = {"symptom", "component", "intent"}
DOMAINS = {"Battery", "Camera", "Performance"}
CONTEXTS_EXPECT = {"any", "empty", "non_empty"}
GOLD_TIERS = {"catalog", "dummy", "manual"}
DUMMY_URI = "bixby://dummy_positive"

PARAPHRASES_PER_ROW = 10
NEAR_MISS_PER_ROW = 3
UNSEEN_PER_DOMAIN = 5


class Report:
    """Collects failures and warnings so one run reports everything, not just the first problem."""

    def __init__(self, verbose: bool = False) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.verbose = verbose

    def fail(self, where: str, message: str) -> None:
        self.failures.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")

    def note(self, message: str) -> None:
        self.notes.append(message)

    def require(self, cond: bool, where: str, message: str) -> bool:
        if not cond:
            self.fail(where, message)
        return cond


def _as_list(value: object) -> list:
    """expected_id is a single id or null; acceptable_ids and parent_ids are lists."""
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _require_fields(row: dict, fields: dict[str, type | tuple], where: str, rep: Report) -> None:
    for name, kind in fields.items():
        if name not in row:
            rep.fail(where, f"missing field {name!r}")
        elif not isinstance(row[name], kind):
            rep.fail(where, f"{name!r} should be {kind}, got {type(row[name]).__name__}")


def _check_unique(rows: list[dict], field: str, name: str, rep: Report) -> None:
    dupes = [v for v, c in Counter(r.get(field) for r in rows).items() if c > 1]
    if dupes:
        rep.fail(name, f"duplicate {field}: {dupes[:5]}")


def _check_no_leaks(rows: list[dict], name: str, rep: Report) -> None:
    for row in rows:
        codes = sorted({f.code for f in check_url_leaks(row)})
        if codes:
            rep.fail(f"{name}/{row.get('id')}", f"contains {', '.join(codes)}")


def check_paraphrases(rows: list[dict], kit_ids: set[str], kit_norm: dict[str, str], rep: Report) -> None:
    name = "paraphrases"
    _check_unique(rows, "id", name, rep)
    _check_unique(rows, "query", name, rep)
    _check_no_leaks(rows, name, rep)

    by_row: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        where = f"{name}/{row.get('id')}"
        _require_fields(row, {"id": str, "row_id": str, "query": str, "register": str}, where, rep)
        if row.get("row_id") not in kit_ids:
            rep.fail(where, f"row_id {row.get('row_id')!r} is not a kit row")
            continue
        if row.get("register") not in REGISTERS:
            rep.fail(where, f"register {row.get('register')!r} not in {sorted(REGISTERS)}")
        if not str(row.get("query", "")).strip():
            rep.fail(where, "empty query")
        if norm_query(row.get("query", "")) == kit_norm.get(row["row_id"]):
            rep.fail(where, "identical to the kit query after normalisation, so it is not held out")
        by_row[row["row_id"]].append(row)

    for row_id in sorted(kit_ids):
        got = len(by_row.get(row_id, []))
        rep.require(
            got == PARAPHRASES_PER_ROW, f"{name}/{row_id}", f"{got} paraphrases, want {PARAPHRASES_PER_ROW}"
        )

    for row_id, group in sorted(by_row.items()):
        diversity = mean_pairwise_jaccard([r["query"] for r in group])
        if diversity is not None and diversity > 0.6:
            rep.warn(
                f"{name}/{row_id}", f"mean pairwise Jaccard {diversity:.2f} is above the 0.6 drop threshold"
            )
        missing = REGISTERS - {r.get("register") for r in group}
        if missing:
            rep.warn(f"{name}/{row_id}", f"no paraphrase in register(s) {sorted(missing)}")


def check_near_miss(rows: list[dict], kit_ids: set[str], kit_norm: dict[str, str], rep: Report) -> None:
    name = "near_miss"
    _check_unique(rows, "id", name, rep)
    _check_unique(rows, "query", name, rep)
    _check_no_leaks(rows, name, rep)

    counts: Counter = Counter()
    for row in rows:
        where = f"{name}/{row.get('id')}"
        _require_fields(
            row,
            {"id": str, "row_id": str, "query": str, "expected_slots": dict, "differs_in": str},
            where,
            rep,
        )
        if row.get("row_id") not in kit_ids:
            rep.fail(where, f"row_id {row.get('row_id')!r} is not a kit row")
            continue
        if row.get("differs_in") not in DIFFERS_IN:
            rep.fail(where, f"differs_in {row.get('differs_in')!r} not in {sorted(DIFFERS_IN)}")
        if row.get("expect") != "miss":
            rep.fail(where, "expect should be 'miss'")
        slots = row.get("expected_slots") or {}
        if set(slots) - {"component", "symptom"}:
            rep.fail(where, f"unexpected slot keys {sorted(set(slots) - {'component', 'symptom'})}")
        if norm_query(row.get("query", "")) == kit_norm.get(row["row_id"]):
            rep.fail(where, "identical to the kit query, so it is not a near miss")
        counts[row["row_id"]] += 1

    for row_id in sorted(kit_ids):
        got = counts.get(row_id, 0)
        rep.require(
            got == NEAR_MISS_PER_ROW, f"{name}/{row_id}", f"{got} near misses, want {NEAR_MISS_PER_ROW}"
        )


def check_unseen(rows: list[dict], kit_norm: dict[str, str], rep: Report) -> None:
    name = "unseen"
    _check_unique(rows, "id", name, rep)
    _check_unique(rows, "query", name, rep)
    _check_no_leaks(rows, name, rep)

    kit_queries = set(kit_norm.values())
    domains: Counter = Counter()
    for row in rows:
        where = f"{name}/{row.get('id')}"
        _require_fields(row, {"id": str, "domain": str, "query": str, "siis_response": dict}, where, rep)
        if row.get("domain") not in DOMAINS:
            rep.fail(where, f"domain {row.get('domain')!r} not in {sorted(DOMAINS)}")
        else:
            domains[row["domain"]] += 1
        if norm_query(row.get("query", "")) in kit_queries:
            rep.fail(where, "this query is in the kit, so it is not unseen")
        siis = row.get("siis_response") or {}
        if not siis.get("title"):
            rep.fail(where, "siis_response.title is empty")
        content = siis.get("content") or ""
        if "## Step" not in content:
            rep.fail(
                where, "siis_response.content has no '## Step' section, so segmentation has nothing to split"
            )
        if len(content) < 400:
            rep.warn(where, f"article is only {len(content)} characters, which is thin for grounding")

    for domain in sorted(DOMAINS):
        got = domains.get(domain, 0)
        rep.require(
            got == UNSEEN_PER_DOMAIN, f"{name}/{domain}", f"{got} scenarios, want {UNSEEN_PER_DOMAIN}"
        )


def check_adversarial(rows: list[dict], rep: Report) -> None:
    name = "adversarial"
    _check_unique(rows, "id", name, rep)
    _check_unique(rows, "kind", name, rep)

    carries_payload = 0
    for row in rows:
        where = f"{name}/{row.get('id')}"
        _require_fields(row, {"id": str, "kind": str, "query": str, "expect": dict, "note": str}, where, rep)
        expect = row.get("expect") or {}
        if expect.get("status") != 200:
            rep.fail(where, "expect.status must be 200: hard rule 4 says the API always answers 200")
        if expect.get("url_leaks") != 0:
            rep.fail(where, "expect.url_leaks must be 0: hard rule 3 admits no exception")
        if expect.get("contexts") not in CONTEXTS_EXPECT:
            rep.fail(where, f"expect.contexts {expect.get('contexts')!r} not in {sorted(CONTEXTS_EXPECT)}")
        if check_url_leaks(row):
            carries_payload += 1

    if not rep.require(carries_payload > 0, name, "no case carries a URL, address or markup payload"):
        return
    rep.note(f"{carries_payload}/{len(rows)} adversarial cases carry a URL or markup payload, as intended")


def check_gold(rows: list[dict], rep: Report) -> None:
    """Every id must exist in the catalog, and the tier must agree with the link that was recorded."""
    name = "gold"
    if not rows:
        rep.note("data/gold/deeplink_gold.jsonl is empty; the three shares are still being labelled")
        return
    _check_unique(rows, "step", name, rep)

    entries = {e.id: e for e in load_entries()}
    owners: Counter = Counter()
    tiers: Counter = Counter()
    for row in rows:
        where = f"{name}/{row.get('step', '')[:40]}"
        _require_fields(row, {"step": str, "screen": str, "verb": str, "tier": str}, where, rep)
        if "expected_id" not in row:
            rep.fail(where, "missing expected_id (use null for a manual or dummy step)")
        tier = row.get("tier")
        if tier not in GOLD_TIERS:
            rep.fail(where, f"tier {tier!r} not in {sorted(GOLD_TIERS)}")
            continue
        tiers[tier] += 1
        owners[row.get("owner") or "unassigned"] += 1

        for field in ("expected_id", "acceptable_ids", "parent_ids"):
            for entry_id in _as_list(row.get(field)):
                if entry_id not in entries:
                    rep.fail(where, f"{field} names {entry_id!r}, which is not in the catalog")

        expected_id, link = row.get("expected_id"), row.get("expected_deeplink")
        if tier == "catalog":
            if not expected_id:
                rep.fail(where, "tier 'catalog' needs an expected_id")
            elif expected_id in entries and link != entries[expected_id].deeplink:
                rep.fail(where, f"expected_deeplink does not match the catalog URI for {expected_id}")
        elif tier == "dummy":
            if expected_id:
                rep.fail(where, "tier 'dummy' must not name a catalog id; use parent_ids for a near miss")
            if link != DUMMY_URI:
                rep.fail(where, f"tier 'dummy' must record {DUMMY_URI}")
        else:
            if expected_id or link:
                rep.fail(where, "tier 'manual' must carry no link at all")

    rep.note(f"gold labels by owner: {dict(sorted(owners.items()))}, {len(rows)} of 100")
    rep.note(f"gold labels by tier: {dict(sorted(tiers.items()))}")


def cross_check(paraphrases: list[dict], near_miss: list[dict], kit_q: dict[str, str], rep: Report) -> None:
    """Near misses must be lexically closer to their row than paraphrases are, or the set is not a trap."""
    if not paraphrases or not near_miss:
        return
    p = sum(jaccard(r["query"], kit_q[r["row_id"]]) for r in paraphrases if r.get("row_id") in kit_q)
    n = sum(jaccard(r["query"], kit_q[r["row_id"]]) for r in near_miss if r.get("row_id") in kit_q)
    p_mean, n_mean = p / len(paraphrases), n / len(near_miss)
    rep.note(f"mean overlap with the row query: paraphrases {p_mean:.2f}, near misses {n_mean:.2f}")
    if n_mean <= p_mean:
        rep.fail(
            "cross-check",
            f"near misses ({n_mean:.2f}) are no closer to the row query than paraphrases ({p_mean:.2f}); "
            "similarity alone would already separate them, so the set does not test the slot guard",
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--set", dest="only", choices=SET_NAMES, help="validate just this set")
    p.add_argument("--verbose", action="store_true", help="list every row that was checked")
    args = p.parse_args(argv)

    rep = Report(args.verbose)
    kit = load_kit()
    kit_ids = {r.row_id for r in kit}
    kit_q = {r.row_id: r.query for r in kit}
    kit_norm = {r.row_id: norm_query(r.query) for r in kit}

    loaded = {name: read_jsonl(SETS_DIR / f"{name}.jsonl") for name in SET_NAMES}
    wanted = [args.only] if args.only else list(SET_NAMES)

    for name in wanted:
        rows = loaded[name]
        print(f"{name:14s} {len(rows):4d} rows")
        if not rows:
            rep.fail(name, "file is empty")
            continue
        if name == "paraphrases":
            check_paraphrases(rows, kit_ids, kit_norm, rep)
        elif name == "near_miss":
            check_near_miss(rows, kit_ids, kit_norm, rep)
        elif name == "unseen":
            check_unseen(rows, kit_norm, rep)
        else:
            check_adversarial(rows, rep)

    if not args.only:
        gold = read_jsonl(GOLD_PATH)
        print(f"{'gold':14s} {len(gold):4d} rows")
        check_gold(gold, rep)
        cross_check(loaded["paraphrases"], loaded["near_miss"], kit_q, rep)

    for note in rep.notes:
        print(f"  note  {note}")
    for warning in rep.warnings:
        print(f"  WARN  {warning}")
    for failure in rep.failures:
        print(f"  FAIL  {failure}")

    root = SETS_DIR.relative_to(REPO_ROOT)
    if rep.failures:
        print(f"\n{len(rep.failures)} failure(s) in {root}")
        return 1
    print(f"\nall checked sets are valid ({len(rep.warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
