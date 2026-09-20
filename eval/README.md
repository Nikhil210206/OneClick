# Evaluation harness

This is our own copy of the organisers' automated scorer, plus the quality measurements that feed `docs/metrics.md`.

It is deliberately **independent of the engine**. Nothing in `eval/` imports `app.compiler` or `app.pipeline`, so a bug in the engine's scrub or trimmer shows up here instead of being checked by itself. The one shared file is the official `api/app/schema.py`.

## Setup

Run everything from the repo root:

```bash
pip install -r api/requirements.txt -r eval/requirements.txt
pytest eval/tests                      # unit tests for the checkers
ruff check eval && ruff format --check eval
```

## Gate replica

```bash
# Offline: audit a results file (G3, G4, G5, A1, A2, A5)
python eval/gate_replica.py --results results.jsonl

# Live: against a freshly started API (G2, G4, G5, A1-A4). Restart the API first so the SIIS cache is empty.
python eval/gate_replica.py --api http://localhost:8000

# Both, failing the run if an enforced gate fails or was not measured
python eval/gate_replica.py --api http://localhost:8000 --results results.jsonl --enforce G2,G3,G4,G5
```

In live mode, each kit query is sent twice, first cold and then as a repeat, with its own SIIS article. Up to 60 paraphrases from `sets/paraphrases.jsonl` are sent with the same article, and every `sets/unseen.jsonl` scenario is sent once. The report is printed and written to `eval/results/gates.json`.

| Check | Rule (source) | Enforced as |
| --- | --- | --- |
| G2 | `/health` returns 200 `{"status":"ok"}` (FAQ Q9) | gate |
| G3 | ≥ 95% of kit queries have a line in the results file (FAQ Q9) | gate |
| G4 | ≥ 90% of responses validate against `schema.py` (FAQ Q9) | gate |
| G5 | Zero URLs anywhere: schemes, `www.`, `.com`/`.html`-style domains, emails, markdown links and images, HTML link tags. `bixby://` is allowed only inside deeplink fields (FAQ Q6, spec 4.2) | gate |
| A1 (15) | Schema, goal regex, title 2–3 words, description "It will" + 5–7 words, no leaks, score 0–1 | mean pass rate × 15 |
| A2 (15) | Deeplink in the catalog (or `dummy_positive`), validation is the entry's **own** and unchanged; auto actions have a link | (validity + auto-link rate) / 2 × 15 |
| A3 (15) | Repeat p95 ≤ 300 ms with ≥ 90% hits; paraphrase hits ≥ 80%; cold p95 ≤ 8 s | 5 per sub-target met |
| A4 (10) | Unseen scenarios give valid, non-empty responses | share × 10 |
| A5 (5) | 8–10 unique variations per line; mean pairwise token Jaccard reported | share × 5 |

The points are **our estimate**. The organisers publish the blocks and targets, but not the weighting inside each block.

Also reported: always-200, pure JSON, identical plans on a repeat call, warnings for style rules, and whether the cache was warm before the "cold" call.

### Goal regex: `--goal-mode`

The FAQ gives the goal as `Follow these steps to perform this <Name> Troubleshooting.`, with a trailing period. `sample_output.json` and the spec's Appendix B omit the period.

The default, `strict`, follows the FAQ, as the compiler does. `--goal-mode lenient` accepts both forms. This is still an open question with the organisers.

Note: the kit's own `sample_output.json` also breaks the 5–7-word description rule, with 9- and 12-word descriptions. Treat it as a reference for the *shape* of a response, not for its rules.

## CI

`.github/workflows/ci.yml` runs on every PR:

- the api tests and lint
- the eval tests and lint
- the gate replica against a fresh `uvicorn`, with **G2, G4 and G5 enforced** and the rest reported

The `gates.json` report is uploaded as an artifact.

## Layout

| Path | What |
| --- | --- |
| `evalkit/checks.py` | Per-response and per-results-line rules, with a URL-leak scanner |
| `evalkit/catalog.py` | Deeplink validity against `data/kit/deeplinks.json` |
| `evalkit/client.py` | HTTP client that records latency, cache flag and tier the way a scorer would |
| `evalkit/sets.py`, `evalkit/stats.py` | Kit and set loaders; percentiles, Jaccard, query normalisation |
| `gate_replica.py` | G2–G5 and A1–A5 |
| `judge.py`, `loadtest.py`, `ablation.py`, `report.py` | Step accuracy and deeplink relevance, latency at N ≥ 30, the mapping ablation, and `docs/metrics.md` |
| `sets/` | Paraphrase, near-miss, unseen and adversarial sets (see `sets/README.md`) |
| `results/` | JSON outputs of the runs above, read by `report.py` |
