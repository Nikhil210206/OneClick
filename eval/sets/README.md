# Test sets

Held-out inputs for the evaluation harness. Regenerate nothing: these are authored files, and the
JSONL *is* the source of truth. Run the validator after any edit.

```bash
python eval/sets/validate_sets.py            # all sets + the gold labels
python eval/sets/validate_sets.py --set unseen
```

| File | Rows | One line looks like |
| --- | --- | --- |
| `paraphrases.jsonl` | 200 (10 per kit row) | `{id, row_id, query, register, source, expect: "hit"}` |
| `near_miss.jsonl` | 60 (3 per kit row) | `{id, row_id, query, expected_slots, differs_in, expect: "miss"}` |
| `unseen.jsonl` | 15 (5 each Battery / Camera / Performance) | `{id, domain, query, siis_response: {title, content}, expect}` |
| `adversarial.jsonl` | 15 | `{id, kind, query, siis_response?, expect: {...}, note}` |

## What each set is for

**paraphrases** feeds A3's paraphrase-hit rate. Ten rewordings of every kit complaint across five
registers — formal, casual, keyword, frustrated, typo — with the component and symptom kept
identical so a hit is always the correct answer. None of them normalises to its kit query, so the
Tier 0 exact cache can never answer one; only the Tier 1 semantic path can. Mean pairwise Jaccard
inside a row is about 0.20, well under the 0.6 similarity at which `enrich` drops a variation.

**near_miss** is the trap. Each line is sent with the *same* SIIS article as its row and must
**miss**. They overlap their row query far more than the paraphrases do (0.49 against 0.30), so
similarity alone cannot reject them — only the slot guard can. `differs_in` says which slot moved:

- `symptom` — black ↔ cracked, the demo pair
- `component` — screen → battery
- `intent` — the same words, the opposite goal ("I *want* the screen to stay dark")

**unseen** covers A4 generalisation: three domains that appear nowhere in `data/kit`, each with an
article written in the kit's own house style (category prefix, `# Heading`, `## Step n:`, imperative
sentences, no URLs). Camera is included even though the catalog is thin on camera screens, so the
dummy and manual tiers get exercised rather than hidden.

**adversarial** is the only set allowed to contain URLs, addresses, markup or instructions — they
are the payload. Every case asserts the same two invariants whatever else happens:

- `expect.status` is always 200 (hard rule 4: degrade, never error)
- `expect.url_leaks` is always 0 (hard rule 3: no exception)

`expect.contexts` is `any`, `empty` or `non_empty` depending on whether declining is defensible.
The kinds cover heavy typos, Hinglish, three intents in one, URLs / addresses / markdown / HTML in
the article, a prompt injection, the SIIS arriving as a bare string, as `{}` and not at all, an
off-topic article, an empty query, nonsense, and an article far past a normal context budget.

## Rules the validator enforces

Beyond field types and counts it checks the properties that make the sets useful rather than merely
well formed: paraphrases and near misses are genuinely held out, near misses really are closer to
the row than paraphrases are, unseen articles have sections to segment, and no set except
`adversarial` carries a URL — while `adversarial` must carry at least one.
