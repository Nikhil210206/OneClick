# Model bake-off, 28–29 Sep 2026

CLAUDE.md, Decisions #2 and #3. Every number below comes from the files in `raw/` (`raw/aggregate.json`
is the roll-up). Run by Vishaal's session on a Windows dev machine, free tiers only. `config.py` was
not changed: every ladder was set through the `ONECLICK_*_MODELS` environment overrides.

**Scope, as cut during the run.** Only Mistral, Groq and Gemini were tested. NVIDIA was dropped after
probing. Groq testing was stopped by request after screening, coverage and 2 of 3 finalist runs.
Gemma was skipped. So stage 3 has one challenger (`gpt-oss-120b`) with no gate replica or load test,
and there is no stage-3 row for `gpt-oss-20b`.

## How it was measured

- **API.** Started from `api/` on `127.0.0.1:8100`, a fresh throwaway `ONECLICK_SQLITE` for every run
  (the cache starts empty).
- **Launcher patches (runtime only, no engine edit):**
  - A JSONL log of every provider call (stage, model, ok or error kind, ms, tokens) and every stage
    outcome, including skipped rungs. These are the `raw/*.calls.jsonl` files.
  - `settings.fallback_reasoning = ""` for **every** configuration, baseline included (see
    Surprises #1).
- **Judge.** `eval/judge.py`'s own code: `gemini-3.5-flash-lite`, judge-v3, temperature 0, the same
  judge `metrics.md` uses. It was driven by a wrapper that judges saved plans, because Gemini's free
  tier was down for hours on the 28th. Step accuracy is the judge's 0–3 plan score.
- **Issue counts.** Summed over the steps of all judged plans. "Missing fix" counts plans where the
  judge listed at least one article fix the plan leaves out.
- **Pacing.** Screening and finalist runs were paced so the candidate stayed inside its free limits:
  - 6 s between requests on Mistral, 25–30 s on Groq (8K tokens a minute per model).
  - The gate replica, load test and burst were **not** paced.
- **Cold latency.** Client-side ms on requests that missed the cache.
- **Tokens per cold query.** All successful LLM calls (call B, call C, variations) divided by the
  number of cold requests. Call B's share is in brackets; with a race, both racers' tokens count.
- **Query sets.**
  - Screening (12 queries): kit `row_21`, `row_12`, `row_8`, `row_3`, `row_1`, `row_19` (long
    articles, the glued article, a multi-intent complaint), plus unseen `battery_1/2`, `camera_1/2`,
    `perf_1/2`. `row_17` was swapped out because it shares `row_3`'s article and is served from the
    cache.
  - Finalists: all 20 kit rows and all 15 unseen scenarios. 7 kit rows share an article with an
    earlier row, so the semantic cache answers them by design.

## 1. Results per stage

### Stage 1: provider and model-id check
`GET https://integrate.api.nvidia.com/v1/models` listed 81 ids. Every NVIDIA model tried is in
section 2. Groq's own list confirmed `openai/gpt-oss-120b`, `openai/gpt-oss-20b` and
`qwen/qwen3.8-27b`.

### Stage 2: screening, each call-B candidate alone on 12 queries
There is no fallback rung, so a failed or empty answer becomes the engine's rules-only answer. Call C
and variations are pinned to `ministral-8b-latest` for every candidate, so only call B differs. One
run each, so there is no range. Unseen means 2 queries per domain.

| Provider : model | Kit | Battery | Camera | Perf | Issues: irrelevant / missing-fix plans / duplicate / not-an-instruction / ordering | Cold p50 / p95 | Model JSON ok / schema-valid | URL leaks | Unseen non-empty | Who answered (of 12) | 429s / quota skips | Tokens per cold (call B) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **mistral : `ministral-14b-latest` (baseline)** | 2.33 | 2.5 | 3.0 | 2.5 | 0 / 5 / 0 / 1 / 1 | 5.4 / 6.6 s | 12/12 · 12/12 | 0 | 6/6 | 14B 10, rules 2 (timeouts at 6 s) | 0 / 0 | 3119 (1486) |
| **mistral : `ministral-8b-latest` (baseline)** | 2.50 | 2.5 | 2.5 | 2.5 | 2 / 4 / 0 / 1 / 0 | 3.5 / 5.0 s | 12/12 · 12/12 | 0 | 6/6 | 8B 12 | 0 / 0 | 3490 (1844) |
| groq : `openai/gpt-oss-120b` | 2.33 | 2.5 | 3.0 | 2.5 | 9 / 2 / 0 / 1 / 0 | 2.1 / 2.9 s | 10/12 (2 empty) · 12/12 | 0 | 6/6 | Groq 10, rules 2 (empty selections) | 0 / 1 | 4234 (2364) |
| groq : `openai/gpt-oss-20b` | 2.33 | 2.5 | 3.0 | 3.0 | 38 / 2 / 0 / 0 / 0 | 1.9 / 2.5 s | 10/12 (2 empty) · 12/12 | 0 | 6/6 | Groq 10, rules 2 (empty selections) | 0 / 1 | 4206 (2332) |
| groq : `qwen/qwen3.8-27b` | 2.00 | 2.5 | 3.0 | 3.0 | 48 / 3 / 0 / 2 / 0 | 1.5 / 2.2 s | 9/12 (3 empty) · 12/12 | 0 | 6/6 | Groq 9, rules 3 (empty selections) | 0 / 1 | 3449 (1815) |
| nvidia : `nvidia/nemotron-3-super-120b-a12b` (dropped) | 1.50 | 1.5 | 2.0 | 2.0 | 53 / 8 / 2 / 2 / 0 | 2.4 / 6.9 s | 1/9 calls answered · 12/12 | 0 | 6/6 | rules 12 | 2 × 503, 6 timeouts, 3 cooldown skips | 1726 (146) |

- **The quota skips** are `row_8` (a ~4K-token prompt) on each Groq model: the 8K tokens a minute
  wasn't enough at 25 s spacing. `row_8` was re-run alone a minute later for each, and that result is
  the one in the table.
- **The high irrelevant counts** for `gpt-oss-20b`, Qwen and Nemotron come mostly from rules-only
  plans. `row_8`'s rules answer alone has 43 steps, 37 of them judged irrelevant. Those are the
  model's failures showing up as rules plans, not bad selections by the model.

### Stage 3: finalist ladders on kit (20) + unseen (15)
Coverage and variations at today's defaults (`ministral-14b-latest` → `ministral-8b-latest` for call C,
`ministral-8b-latest` → `mistral-small-latest` for variations). Fallback is today's
`mistral-small-latest`. Paced: 12 s between requests for the baseline, 30 s for Groq. Values are the
mean of runs (min–max).

| Ladder (call B) | Runs | Kit | Unseen | Battery | Camera | Perf | Issues per run: irrelevant / missing-fix plans / duplicate / not-an-instruction / ordering | Cold p50 / p95 per run | Model JSON ok / schema-valid | URL leaks | Unseen non-empty | Who answered (cold, all runs) | 429s / quota skips | Tokens per cold (call B) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Baseline: `ministral-14b-latest` ⟷ `ministral-8b-latest`** | 3 | **2.53** (2.45–2.60) | 2.67 (2.67–2.67) | 2.40 (2.4–2.4) | 2.93 (2.8–3.0) | 2.67 (2.6–2.8) | 14.3 / 8.0 / 1.3 / 4.0 / 0.3 | 4.7/6.4, 4.9/6.4, 5.0/6.5 s | 168/168 · 105/105 | 0 | 45/45 | 14B 65, 8B 15, rules 4 (+21 cache) | 0 / 0 | 4303 (2799) |
| `groq:openai/gpt-oss-120b` ⟷ `ministral-14b-latest` → `ministral-8b-latest` | 2 (run 3 stopped) | **2.53** (2.45–2.60) | 2.70 (2.60–2.80) | 2.40 (2.2–2.6) | 3.00 (3.0–3.0) | 2.70 (2.6–2.8) | 11.5 / 4.5 / 2.5 / 3.5 / 0.5 | 2.6/6.4, 3.0/4.6 s | 104/113 (9 empty) · 70/70 | 0 | 30/30 | Groq 46, 14B 8, 8B 1, rules 1 (+14 cache) | 0 / 2 | 4983 (3505) |

- **`gpt-oss-120b`'s empty selections** (8, plus one from 14B) were recovered by the race: 14B's
  answer was used. That is why its cold p95 still reaches 6.4 s in run 1.
- **Every cold request calls 14B as well,** because call B races its first two rungs. Groq doesn't
  take load off Mistral; it only answers sooner, and the race raises tokens per query.

Baseline gate replica and load test (unpaced, fresh API each). The `gpt-oss-120b` equivalents were
lost to a run collision and not repeated after Groq was stopped (Surprises #9).

| Baseline run | Result |
|---|---|
| `gate_replica.py` | G2 PASS, G4 PASS (115/115), G5 PASS (0 leaks). A1 15, A2 15, A3 15, A4 10: 55/55 of the blocks measured. Cold p50 3.8 s / p95 6.3 s (n=13). Repeat p95 20 ms (100% hits). Paraphrase 88.3% hits. 0/20 repeats changed plan. |
| `loadtest.py --mode api` | Cold p50 4.2 s / p95 6.3 s (28 timed). Repeat p95 17 ms (100%). Paraphrase 90% hits. Near-miss false hits 1/60 = 1.7%. Call B answered by 14B 60, 8B 18, failed → rules 8. Call C by 8B 78, 14B 6. 429s: `mistral-small-latest` 2. 31 cooldown skips (all `mistral-small-latest`). |

### Stage 4a: call C (coverage) candidates
Each alone. Call B is pinned to `ministral-8b-latest` alone and variations to 8B; screening's 12
queries; one run each.

| Provider : model (call C) | Kit | Battery | Camera | Perf | Issues: irrelevant / missing-fix plans / duplicate / not-an-instruction / ordering | Call C p50 / p95 | Call C answered | Model JSON errors | Cold p95 | Schema / leaks / unseen non-empty | 429s | Tokens per cold |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **mistral : `ministral-14b-latest` (baseline primary)** | 2.67 | 2.5 | 2.5 | 3.0 | 5 / 2 / 0 / 1 / 0 | 2.0 / 3.2 s | 12/12 | 0 | 5.6 s | 12/12 · 0 · 6/6 | 0 | 3556 |
| **mistral : `ministral-8b-latest` (baseline fallback)** | 2.67 | 2.5 | 3.0 | 2.5 | 2 / 3 / 2 / 1 / 0 | 1.6 / 3.3 s | 12/12 | 0 | 6.1 s | 12/12 · 0 · 6/6 | 0 | 3545 |
| groq : `qwen/qwen3.8-27b` | 2.83 | 2.5 | 3.0 | 3.0 | 0 / 1 / 0 / 1 / 0 | 0.8 / 0.9 s | 12/12 | 0 | 6.7 s | 12/12 · 0 · 6/6 | 0 | 3543 |
| groq : `openai/gpt-oss-20b` | 2.83 | 2.5 | 3.0 | 2.5 | 2 / 2 / 2 / 1 / 0 | 0.9 / 1.1 s | 10/12 | 2 (`json_validate_failed`) | 6.6 s | 12/12 · 0 · 6/6 | 0 | 3365 |
| groq : `openai/gpt-oss-120b` | 2.67 | 2.5 | 2.5 | 2.5 | 2 / 2 / 0 / 1 / 0 | 1.5 / 2.5 s | 11/12 | 1 (`json_validate_failed`) | 6.6 s | 12/12 · 0 · 6/6 | 0 | 3559 |

- **Cold p95 here is set by call B** (8B alone, which timed out once in the `gpt-oss` runs), not by
  call C.
- **12 queries, one run:** differences of 0.17 are within the judge's run-to-run spread (stage 3's
  baseline kit score moved 0.15 between identical runs).

### Stage 4b: variations candidates
- **Setup.** Each candidate alone (no fallback), through the engine's own `_variations_llm` and
  `filter_variations`, on the 20 kit queries.
- **Cache check.** Each kit query plus its kept variations goes into the engine's cache, then the 200
  held-out paraphrases and 60 near misses are looked up (the `loadtest --mode cache` method).
- **A5.** 8–10 unique variations passed for all 20 queries with every candidate, because templates
  top up whatever the model misses.

| Provider : model | When | Calls OK | p50 / p95 | Kept from model (vs templates) | Mean pairwise Jaccard (lower = more diverse) | Paraphrase hits | Wrong plan served | Near-miss false hits | Errors |
|---|---|---|---|---|---|---|---|---|---|
| (no variations: query only) | – | – | – | – | – | 83.0% | 0 | 1/60 | – |
| **mistral : `ministral-8b-latest` (baseline)** | 28 Sep, 22:05 | 20/20 | 3.6 / 4.8 s | 91% | 0.251 | 84.5% | 1 | 1/60 | – |
| mistral : `ministral-3b-latest` | 28 Sep, 22:05 | 20/20 | 2.0 / 2.7 s | 94% | 0.223 | 84.5% | 2 | 1/60 | – |
| **gemini : `gemini-3.1-flash-lite`** | 29 Sep, 08:15 | 20/20 | 2.5 / 5.8 s | 96% | **0.127** | **85.0%** | **0** | 1/60 | – |
| gemini : `gemini-3.5-flash-lite` | 29 Sep, 08:15 | 8/20 | 1.9 / 3.2 s | 35% | 0.132 | 84.0% | 3 | 1/60 | 2 × 429, 10 cooldown skips (the judge was using the same model's quota) |
| gemini : `gemini-3.1-flash-lite` | 28 Sep, 22:55 | 0/20 | – | 0% | (templates) | 83.5% | 2 | 1/60 | 5 × 503, 1 timeout, 14 cooldown skips |
| gemini : `gemini-3.5-flash-lite` | 28 Sep, 22:55 | 0/20 | – | 0% | (templates) | 83.5% | 2 | 1/60 | 3 × 503, 8 timeouts, 9 cooldown skips |
| gemini : `gemma-4-26b-a4b-it` | 28 Sep, 22:55 | 17/20 | 7.3 / 8.9 s | 83% | 0.178 | 84.5% | 1 | 1/60 | 3 timeouts (10 s budget) |
| gemini : `gemma-4-31b-it` | 28 Sep, 22:55 | 0/20 | – | 0% | (templates) | 83.5% | 2 | 1/60 | 4 × 500, 5 timeouts, 11 cooldown skips |

Tokens per call were not recorded for this bench.

### Stage 5: burst, recommended ladder
- **Ladder.** Call B and call C are the baseline. Variations are `gemini-3.1-flash-lite` →
  `ministral-8b-latest`, with fallback left at today's default.
- **Test.** 35 kit+unseen queries sent at once to a fresh API, then the same 35 at once again.
- **Baseline comparison.** Stage 3's unpaced load test above, which sends the same queries one at a
  time.

| | 200s | Schema-valid | URL leaks | Empty | Cold p50 / p95 / max | Who answered | 429s |
|---|---|---|---|---|---|---|---|
| Cold burst (35 at once, 9.3 s wall) | 35/35 | 35/35 | 0 | 0/35 | 3.3 / 7.0 / 7.0 s | rules 31 (27 turned away by the cold-capacity guard, 4 call-B failures at the 6.3 s budget), 14B 2, 8B 2 | 0 |
| Repeat burst (35 at once) | 35/35 | 35/35 | 0 | 0/35 | hits p95 144 ms, max 147 ms, 100% hits | cache (it re-serves the 31 rules answers, cached for `degraded_cache_ttl_s`) | 0 |
| Baseline load test (sequential, for comparison) | all | all | 0 | – | 4.2 / 6.3 s | 14B 60, 8B 18, rules 8 | 2 (`mistral-small-latest`) |

In the burst, variations came from `gemini-3.1-flash-lite` 8/8, and call C from 14B 5 and 8B 3.
Tokens: 37,581 for the 35 cold requests.

## 2. Models that failed or were dropped

| Provider : model | Why |
|---|---|
| nvidia : `nvidia/nemotron-nano-3-30b-a3b`, `moonshotai/kimi-k2.6`, `nv-mistralai/mistral-nemo-12b-instruct`, `mistralai/mistral-large-2-instruct` | Listed by `/v1/models` but **404 "Function … Not found for account"**. No 403 family-registration error was seen for any model. |
| nvidia : `nvidia/nemotron-3-super-120b-a12b` | Rejects `nvext.guided_json` (400 "unknown field `guided_json`"); accepts `response_format` json_schema. Screened alone: answered 1 of 9 calls (2.3 s), 6 timeouts at 6 s, 2 × 503 "Service temporarily overloaded". Screen score 1.50 is all rules-only answers. Too slow and unreliable. |
| nvidia : `nvidia/nemotron-3.5-lightning-30b-a3b` | Rejects `nvext.guided_json`. With `response_format`, and even on "Reply ok", no answer within 30–40 s (28 Sep and 29 Sep). |
| nvidia : `openai/gpt-oss-20b`, `google/gemma-4-31b-it`, `deepseek-ai/deepseek-v4.1-flash`, `z-ai/glm-5.3-flash`, `z-ai/glm-5.3`, `moonshotai/kimi-k3` | No answer within 30–40 s, even to a one-line prompt, on both days. |
| mistral : `mistral-small-latest` (today's `fallback_model`) | Free plan returns **429 with `x-ratelimit-limit-req-minute: 0`**: it is never served. As the last rung it can only add a 429 and a cooldown (2 × 429, 31 cooldown skips in the load test). |
| gemini : `gemma-4-26b-a4b-it`, `gemma-4-31b-it` for call B | Gemini's API returns 400 for Gemma when the schema has `minItems`/`maxItems` ("Request contains an invalid argument") and when a thinking level is set ("Thinking level is not supported for this model"). Call B's schema has both. 26B works for variations (simpler schema) at 7–9 s, too slow for call B's 6 s. Skipped by request before a schema-fixed screen. |
| gemini : `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-3.5-flash`, `gemini-3-flash-preview`, `gemini-flash-lite-latest` (28 Sep, 21:50–23:00) | 503 "This model is currently experiencing high demand"; the successes took 12–28 s. Fine by 08:00 on the 29th (0.7–0.8 s). |
| groq : `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` | Not failed. Groq testing was stopped by request. Their weaknesses: false "no match" (empty selection) on 2, 2 and 3 of 6 hard kit rows; 8K tokens a minute (~2 cold requests a minute); `json_validate_failed` 400s from `gpt-oss` as call C. |

## 3. Recommended ladder per stage

The Decisions #3 rule adopts a new ladder only if it **beats today's on both sets, keeps cold p95 ≤ 7 s,
stays 100% schema-valid with 0 leaks, and gives 100% non-empty answers on unseen.**

### Call B (extract): keep `ministral-14b-latest` ⟷ `ministral-8b-latest`

| Check | Baseline (today) | `gpt-oss-120b` ladder | Passes the rule? |
|---|---|---|---|
| Kit step accuracy | 2.53 (2.45–2.60), 3 runs | 2.53 (2.45–2.60), 2 runs | **No:** a tie, not a win |
| Unseen step accuracy | 2.67 | 2.70 | +0.03, within noise |
| Cold p95 ≤ 7 s | 6.4–6.5 s | 4.6–6.4 s | yes |
| Schema-valid, 0 leaks | 100%, 0 | 100%, 0 | yes |
| Unseen non-empty 100% | 45/45 | 30/30 | yes |

- **`gpt-oss-120b` doesn't pass.** It gives a faster p50 (2.6–3.0 s against 4.7–5.0 s) and fewer
  plans missing a fix (4.5 against 8.0 per run). But its step accuracy isn't better, it has no gate
  replica or load test, and Groq's 8K tokens a minute means an unpaced scorer gets mostly Ministral
  answers anyway.
- **The baseline itself** meets every absolute criterion of the rule.
- **Change: set `fallback_model` to `""`** (or to any rung the free plans actually serve) instead of
  `mistral-small-latest`.
  - A different-provider last rung (e.g. `groq:openai/gpt-oss-120b`, 1–3 s) would cover a Mistral
    outage, but was not measured *as a last rung*.
- **Budgets: no change to `extract_prefer_deadline_s` (6.0) or `extract_budget_s` (6.3).**
  - 14B timed out on 15 of 84 cold call-B attempts in stage 3, and 8B answered most of those.
  - 14B's p95 is 6.0–6.2 s. Raising the deadline would push cold p95 past 7 s.
  - Dropping `reasoning_effort` (below) saves one ~0.3 s round trip per call, which should take 14B's
    p95 under the deadline. Re-measure after that change before touching budgets.

### Call C (coverage): keep `ministral-14b-latest` → `ministral-8b-latest`

- **14B and 8B tie:** 2.67 kit and 2.67 unseen each. Call C p95 is 3.2–3.4 s, inside
  `coverage_wait_s` = 4.5 s in every run.
- **The best result was Groq Qwen** (2.83 / 2.83, 0.8 s, 12/12), but it's one 12-query run. That
  can't meet a rule written for 3 runs on kit + unseen, and Groq testing was stopped. It's the first
  candidate to retry if Groq comes back.
- **No change to `coverage_wait_s` (4.5 s) or `coverage_grace_s`.**

### Variations: `gemini-3.1-flash-lite` → `ministral-8b-latest`

| | Baseline `ministral-8b-latest` | `gemini-3.1-flash-lite` (29 Sep) |
|---|---|---|
| Calls OK | 20/20 | 20/20 (0/20 on the night of the 28th) |
| p95 | 4.8 s | 5.8 s (budget 10 s) |
| Diversity (Jaccard) | 0.251 | 0.127 |
| Paraphrase hits | 84.5% | 85.0% |
| Wrong plan served | 1 | 0 |
| A5 | 20/20 | 20/20 |

- **Why:** it earns the Gemini bonus (T2-Q19) and gives the most diverse variations. The 8B fallback
  matters, because Gemini's free tier went fully unavailable for hours.
- **The Decisions #3 quality rule doesn't apply here.** Variations never write an answer: the burst
  test served every answer on time while Gemini produced 8/8 variations in the background.
- **Keep `variations_budget_s` at 10 s.**
- **Drop `mistral-small-latest` from this ladder too.**

### Changes found along the way (not model choices)
1. **`fallback_reasoning = ""`.** Every Ministral call gets **400 "reasoning_effort is not enabled for
   this model"** and is retried without it. Measured on 28 Sep: each 400 also uses up a request of
   14B's 30 a minute (remaining 29 → 27 → 25 across three good calls). So today 14B serves about 15
   answers a minute, and every call pays an extra ~0.3 s. All stages above ran with this off, the
   baseline included.
2. **`fallback_model`:** see call B above.

## 4. Reference ceiling

None was run. No frontier or paid model was measured, and the largest model tested was
`gpt-oss-120b` (Groq). The only anchor is the judge's own scale: the best plans score 3/3, and
the kit's mismatched rows (7, 10, 20; the glued 3/11/17) cap every model below that. See `metrics.md`
section 6.

## 5. Files changed or created (nothing committed, nothing reverted)

`git status` at the time of writing. Only the first three rows were touched by this bake-off; the
other rows were already uncommitted work in progress and were not edited.

| Path | Status | What changed / why |
|---|---|---|
| `api/app/llm/openai_compat.py` | `??` (already new and untracked) | Added a model- or provider-level `schema_param` (`nvext` → `nvext.guided_json`, else `response_format`) and a per-model `extra_body`, so NVIDIA models could be probed. With NVIDIA dropped it's unused and can be reverted. |
| `api/tests/test_llm.py` | `M` (already modified) | Added `test_the_schema_goes_where_the_provider_or_the_model_asks` for the change above. The LLM tests pass (31). `ruff format --check` flags line ~332, which is in the existing WIP, not this test. |
| `eval/results/bakeoff/` (`SUMMARY.md`, `raw/` with 58 files, 2.3 MB) | `??` | This report and its raw outputs. Not covered by `.gitignore` (which only matches `eval/results/*.json`), so it would be committed if added. |
| `eval/results/judge_cache.json` | gitignored | The judge's cache gained this bake-off's judgments. `judge.json`, `gates.json` and `loadtest.json` were **not** overwritten: every run used its own `--out`. |
| `api/app/config.py`, prompts, `CLAUDE.md`, `docs/*`, `api/app/llm/{router,registry,quota,gemini,mistral,errors}.py`, `api/app/main.py`, `api/app/pipeline/{extract,run,capacity}.py`, `api/tests/conftest.py` | `M` / `??` | Already uncommitted; **not touched**. No config or prompt edits. |

- **Harness (not in the repo):** the launcher, runner, judge wrapper, variations bench, burst and
  aggregate scripts live in the session scratchpad. They applied every override at runtime: the
  ladders through `ONECLICK_*_MODELS`, and `fallback_model`, `fallback_reasoning`, the NVIDIA provider
  and the Gemma schema strip by patching `settings` in memory.

## 6. Surprises

1. **`reasoning_effort` costs Ministral double.** See section 3. It is the biggest capacity win
   found, bigger than any model swap.
2. **The default last rung is dead.** `mistral-small-latest` has a free-plan limit of 0 requests a
   minute.
3. **NVIDIA's free endpoint was unusable** on both days: timeouts past 30–40 s for one-line prompts,
   404s for 4 listed models, and 503 "overloaded" for Nemotron. The Nemotron 3 family also rejects
   `nvext.guided_json`, the field `CLAUDE.md` says NVIDIA needs.
4. **Gemini's free tier went fully unavailable in the Indian evening** (US midday): 503 on every
   model, the judge included, successes after 12–28 s. It was fine the next morning. Nothing on the
   answer path should depend on it.
5. **Groq models give false "no match"** on long or mismatched kit articles: 2–3 of the 6 hard kit
   rows each, returning `{"goals": []}` in ~6 output tokens. All three did it on `row_1`, where both
   Ministral models answered. The race and the two-vote rule for empty answers recover it in a
   ladder, but alone they'd return rules answers.
6. **Groq's quota wall is tokens, not requests.** At 8K tokens a minute and ~3–4K tokens per call-B
   prompt (reasoning included for `gpt-oss`), a Groq model serves about 2 cold requests a minute.
7. **Gemma on the Gemini API rejects parts of the JSON schema** (`minItems`/`maxItems`) and any
   thinking level.
8. **A simultaneous burst is mostly rules-only.** The free tier finishes only about 4 concurrent
   call-B runs, and the cold-capacity guard (8) turned away 27 of 35 requests. Everything stayed
   200, valid and non-empty. But the rules answers are cached for 10 minutes, so a scorer's repeat
   pass gets them too.
9. **The semantic cache hits 83% of paraphrases with no variations at all.** Variations add only 1–2
   points, so the variations choice matters for A5 and the bonus, not for A3.
10. **Process note.** An orphaned run script from the evening survived a session restart and started
    its own API on the same port during the morning runs. Every file it touched (Gemma and Nemotron
    screens, the `gpt-oss-120b` gate replica and load test) was discarded, and the screens were
    re-run cleanly. Nothing contaminated is in `raw/`.
