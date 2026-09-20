# Mapping, cache & infra (Karur's lane)

What is built, how it behaves, what the numbers are, and what the other lanes call.
Everything here is measured, not estimated; every figure has the command that produced it.

## What the lane does

```
Vishaal's extractor            YOU                         Vishaal's compiler
   DraftAction        ->   resolve(action)        ->     LinkDecision
   (screen_path,           catalog / dummy /             (tier, entry_id,
    intent_verb,           manual                         node_id, confidence)
    steps)

   request in         ->   cache.lookup(...)      ->     CacheHit | None
   (norm_query,            tier 0 exact, then            (plan, tier, similarity)
    slots, siis_hash)      tier 1 semantic
```

## The API the other lanes call

| Call | Returns | Used by |
| --- | --- | --- |
| `screengraph.resolver.resolve(action)` | `LinkDecision(tier, node_id, entry_id, confidence)` | pipeline, after grounding |
| `screengraph.resolver.validation_for(entry_id)` | the entry's own validation object, verbatim | compiler |
| `pipeline.slots.extract_slots(norm_query)` | `Slots(component, symptom)` | normalize stage, cache guard |
| `cache.lookup(norm_query, slots, siis_hash)` | `CacheHit \| None` | pipeline, before enrich |
| `cache.put(CacheEntry)` | — | pipeline, after compile |
| `cache.init()` | entries loaded from the snapshot | startup |
| `retrieval.dense.embed(texts)` | L2-normalized vectors | grounding, segmenting |
| `obs.metrics.record(trace, latency_ms)` | — | once per served request |
| `obs.trace.new_trace()` / `put(trace)` | `Trace` / — | pipeline |
| `GET /v1/metrics`, `/v1/traces`, `/v1/trace/{id}` | rolling window, last 200 traces | console, eval |

`LinkDecision.tier` is `catalog`, `dummy` or `manual`. On `dummy` the entry id is `DL-DUMMY`
and the compiler writes its own 5-7 word description naming the screen. On `manual` there is no
deeplink and the categorizer decides `manual` vs `critical`.

## How a link is chosen

1. **Off-screen check.** Physical and external steps (restart, visit a service centre, clean the
   port, charge) never reach the search. A Settings path with an in-app verb is always searched,
   whatever words the step contains, so "turn on Fast charging for the cable charger" is not
   mistaken for a physical step.
2. **BM25** over the cleaned catalog (`message` and `qna_description` weighted 2x).
3. **Dense embeddings** (BAAI/bge-small-en-v1.5, ONNX on CPU) over the same documents.
4. **Reciprocal rank fusion** merges the two by position, because a BM25 score of 18.6 and a
   cosine of 0.86 are not comparable.
5. **Adjustments** on the fused score (normalised so the best hit is 1.00):
   - leaf-name match, up to +0.60, two-way so an entry's extra words count against it
     ("Storage" must not score full marks against "Storage Share")
   - polarity +0.25 when the entry type matches the verb, -0.35 when it is the opposite toggle
   - surface -0.30 for TV / Samsung Members entries
6. **Tier decision**: >= 1.60 is a catalog link, otherwise a Settings path gets `dummy`, anything
   vaguer gets `manual`.
7. **Screen Graph** corrects the on/off choice inside the matched screen and supplies the
   validation object.

### Cross-encoder rerank: implemented, disabled

`settings.use_rerank = False`. Measured on 63 labelled steps it changed 9 answers, fixed 1 and
broke 6, at 250 ms per step against 10 ms for the fast path. The catalog is terse fragments
("power saving mode"), which is not what the model was trained on. Re-measure with
`python scripts/eval_retrieval.py --rerank` if the gold set grows.

## Screen Graph

577 catalog entries cluster into **413 screens** by shared validation deeplink, confirmed by
identical cleaned description (the two signals agreed on 419 vs 420 groups before merging).

```
SN-0267 "power saving mode"
    off  -> DL-0411    on -> DL-0412    open -> DL-0519
```

It is **not** the search index: indexing nodes instead of entries measured 87% precision@1
against 94%. It is kept for the on/off correction, the validation lookup, the console's Screen
Graph explorer, and the scaling story in the deck.

## Cache

```
Tier 0  exact     key = sha256(prompt_version | norm_query | siis_hash)[:16]     0.6 ms
Tier 1  semantic  cosine over every stored phrasing (original + variations)      ~20 ms
        a hit needs  similarity >= 0.80  AND  slots agree  AND  same siis_hash
```

- Every variation of a solved query is indexed pointing at the same plan. That is the whole of
  the paraphrase hit rate.
- **The slot guard** is what stops "screen is black" answering "screen is cracked", which
  embeddings rate 0.91 alike. A missing slot is a wildcard; two filled slots that differ block
  the hit.
- **The article hash must match**, so the same question with a different article never gets a
  stale plan.
- SQLite snapshot for restarts, single-flight lock so identical concurrent misses compute once.
- The SIIS cache **ships empty**: the scorer's first call has to be genuinely cold.

### Threshold: 0.80, not 0.85

`data/fixtures/*/cache_events.json` says `"threshold": 0.85`. Measured on 30 held-out
paraphrases, 0.85 hit only **63%** where block A3 needs 80%, and below 0.80 paraphrases start
matching the *wrong* cached plan. 0.80 gives 90% with zero wrong plans and zero false hits.
**This is the one contract difference that needs a decision** - either the fixture moves to 0.80
or we discuss.

## Slots

`data/slot_lexicon.json`: 10 components, 12 symptoms, phrase match only, no LLM.

- **Earliest mention wins**, because a complaint names its subject first: "my screen went black
  ... cannot use Smart Switch" is a screen problem, not an app problem.
- **Denied phrases are ignored**: "there is no physical damage" must not read as `cracked`.
- Longer phrase breaks a tie, so "touch screen" beats "screen".

All 20 kit queries read as `screen`; 18 also get a symptom. Grow the lexicon freely - it is data,
not code.

## Measured results

| Metric | Target | Measured |
| --- | --- | --- |
| Deeplink precision@1 (87-step gold) | - | **94%** (59/63) |
| Recall@3 | - | 95% |
| Wrong link attached | as low as possible | **1.6%** (1/63) |
| Declined to `dummy` when a link existed | - | 8% (5/63) |
| `resolver_cases.json` (Vishaal's contract) | 12/12 | **12/12**, no `must_not_match` hit |
| Physical steps refused before search | all | 20/20, **0** false refusals |
| Article-worded gold (8 steps) | - | 88% p@1, 100% r@3 |
| Cache repeat hit | >= 90%, p95 <= 300 ms | **100%** at 0.6 ms |
| Cache paraphrase hit | >= 80% | **90%** at ~20 ms |
| Cache false hits | <= 2% | **0%** |
| Resolve latency | <= 500 ms budget | 10-14 ms |
| Boot to healthy | < 20 s | 0.3 s local, 1.5 s in Docker |

Per question rather than per step: a question yields about 5 steps, so roughly **92% of answers
carry no wrong link at all**.

### How much to trust these

The 87-step gold file and the paraphrase set were written by this lane, so they flatter. The
honest independent number is **12/12 on `resolver_cases.json`**, which Vishaal wrote and which
includes deliberate lookalike traps. More labels from the other lanes (TEAM.md asks ~33 each)
would make the rest trustworthy.

## Running it

```bash
# whole service, no Python setup needed
docker compose up --build          # /health 200 in ~23 ms, boot 1.5 s
curl localhost:8000/health
curl localhost:8000/v1/metrics

# local development
cd api && pip install -r requirements.txt
python scripts/build_screengraph.py   # 577 entries -> 413 screens
python scripts/build_index.py         # embeds the catalog once -> data/build/
uvicorn app.main:app --reload
pytest                                # 85 tests

# measurement
python scripts/eval_retrieval.py                 # 87-step gold
python scripts/eval_retrieval.py --articles      # article-worded gold
python scripts/eval_retrieval.py --graph         # ablation: search nodes not entries
python scripts/eval_retrieval.py --no-polarity   # ablation: what polarity is worth
python scripts/eval_cache.py --sweep             # threshold sweep
```

`data/build/` is gitignored; the Docker image builds it at image-build time, so a container
starts warm and needs no network.

## Files

| Path | What |
| --- | --- |
| `api/app/retrieval/` | bm25, dense (+ save/load), fuse, rerank (off), scoring in `__init__` |
| `api/app/screengraph/` | `clean` (boilerplate, polarity, surface), `build` (clustering), `resolver` |
| `api/app/cache/` | `exact`, `semantic`, `slot_guard`, `store` (SQLite), `no_siis` (stub) |
| `api/app/pipeline/slots.py` | the only file this lane owns under `pipeline/` |
| `api/app/obs/` | `metrics`, `trace`, `logging`, `readiness` |
| `api/scripts/` | `build_screengraph`, `build_index`, `eval_retrieval`, `eval_cache` |
| `data/gold/` | 87-step gold, article-worded gold, cache paraphrases |
| `data/slot_lexicon.json` | the slot word lists |

## Still open in this lane

- **Device simulator** (`device/`, `routes/device.py`): demo only. Note the catalog quirk - only
  the 138 `onURL` entries carry condition and value, so only those can show "Verified".
- **No-SIIS lookup** (`cache/no_siis.py`, `scripts/build_lookup.py`): a query with no article.
- **`data/appliance_exclusions.json`** is still an empty list.
- **Deployment**: the image runs anywhere, but no public URL is set up. The gates assume a
  reachable API during the judging window; a tunnel is the quick fallback.

## Notes for review

- `api/app/main.py` is Vishaal's file. This lane added the lifespan warm-up and made `/health`
  return 503 until the catalog, Screen Graph, index and cache are loaded, which is what the
  `TODO(B)` in it asked for. Worth his sign-off.
- `api/app/cache/lookup.py` was renamed to `cache/no_siis.py`: a module named `lookup` inside the
  package shadows `cache.lookup()`, the function the pipeline calls. The function name
  `lookup_no_siis` is unchanged.
- The Docker build context moved to the repo root, because `api/` alone contains no `data/kit`
  to search, and `.env` is now optional so a fresh clone can start.
