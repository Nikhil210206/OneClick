# Gold labels

`deeplink_gold.jsonl` is the hand-labelled answer key for link resolution. It is what
`eval/judge.py` scores deeplink relevance and precision@1 against, and what `eval/ablation.py`
compares the retrieval variants on. Target is 100 steps, roughly 33 each.

One line per step:

```json
{"step": "...", "screen": "Settings > Display > Touch sensitivity", "verb": "enable",
 "expected_id": "DL-0126", "expected_deeplink": "bixby://masked/act/...", "tier": "catalog",
 "owner": "nikhil", "acceptable_ids": [], "parent_ids": [], "note": "why, when it is not obvious"}
```

| Field | Meaning |
| --- | --- |
| `step` | the step text as a plan would render it; unique across the file |
| `screen` / `verb` | what the resolver is given: screen path and intent verb |
| `tier` | `catalog`, `dummy` or `manual` — see below |
| `expected_id` | the one right catalog entry, or `null` for dummy and manual |
| `expected_deeplink` | that entry's URI, `bixby://dummy_positive`, or `null` |
| `acceptable_ids` | other entries that are equally correct (the catalog has true duplicates) |
| `parent_ids` | parent menus that should still score 1 when the exact screen has no entry |

## Tiers

`tier` records the honest outcome, not an aspiration:

- **catalog** — a real entry exists and is the right screen.
- **dummy** — no catalog entry fits, but a link is plausible, so `bixby://dummy_positive`.
- **manual** — no link is appropriate at all: a hardware button sequence, a Quick settings tile.

Keeping `dummy` and `manual` in the file is deliberate. They are the measurement of where the
catalog runs out, which is what "dummy rate by catalog gap" in `docs/metrics.md` reports. Deleting
them would make the numbers look better and mean less.

## Labelling

```bash
python eval/tools/label_gold.py --owner <you>                  # type steps one at a time
python eval/tools/label_gold.py --owner <you> --todo mine.jsonl  # work through {step, screen, verb}
python eval/tools/label_gold.py --search "turn off fast charging" --verb disable
```

The tool ranks candidates with its own BM25 (`eval/evalkit/bm25.py`), which is **not** the engine's
retriever — labelling the answer key with the thing it grades would only measure self-agreement.
Treat the candidate list as a shortlist, not an answer: on the labels here BM25's top hit was wrong
8 times in 24, picking Intelligent Wi-Fi over Wi-Fi, fast *wireless* charging over fast cable
charging, Bluetooth *tethering* over Bluetooth, and a monitoring entry over the brightness screen.
Always read the chosen entry's own `description` before accepting it.

Watch for these while labelling:

- **`message` can disagree with `description`.** `DL-0397` reads "Disable Adaptive Display" but its
  description is adaptive *battery*. Match on the description.
- **`DL-0022` is the auto factory reset**, not Factory data reset, so a Factory data reset step is a
  catalog gap rather than a match.
- **Only the 138 `onURL` entries are fully validatable**; every `offURL`, `onClickURL` and
  `updateURL` entry is key-only and can never show "Verified".
- **Some entries are exact duplicates** (`DL-0518` and `DL-0552`). List the second in
  `acceptable_ids`.
