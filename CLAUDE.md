# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository. Shared by the whole team — keep it accurate, and update it in the same PR that changes the behaviour it describes.

## What this is

OneClick is a Smart Guided Troubleshooting engine for the Samsung PRISM GenAI Hackathon 2026 (Theme 2). A vague user complaint plus a SIIS knowledge article go in; a grounded, schema-valid troubleshooting plan with verified Galaxy Settings deeplinks comes out.

The engine is complete end to end. The final-week changes (capacity guard, LLM ladders, a judge-ready README, packaging) and the engine freeze on 1 Oct 2026 are in [Hackathon submission](#hackathon-submission-final-week) at the end of this file. Each module's docstring states which design component it implements (C1–C12); [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) is the design, and its last section lists where the submission differs from it. The only stubs left are the device simulator (`device/simulator.py`, `routes/device.py`), which is cut from the submission and on the roadmap.

## Commands

All API commands run from `api/` (pytest sets `pythonpath = ["."]`, so `app.*` imports resolve from there).

```bash
# API, local
cd api
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload                        # http://localhost:8000
pytest                                               # whole suite
pytest tests/test_api.py::test_health                # single test
ruff check . && ruff format --check .                # lint (line-length 110, target py310)

# Full stack: API on :8000, site on :3000 (ONECLICK_CONSOLE_PORT to move it)
cp .env.example .env                                 # GEMINI_API_KEY, MISTRAL_API_KEY
docker compose up -d --build && curl localhost:8000/health

# Offline builds (run from api/, in this order, before the API can resolve links)
python scripts/build_screengraph.py   # clean catalog -> data/build/screengraph.json
python scripts/build_index.py         # dense vectors over catalog entries (BM25 is built at startup)
python scripts/make_results.py        # cold run over data/kit -> results.jsonl

# Evaluation (run from repo root, against a running API)
python eval/sets/validate_sets.py  # test sets + gold labels; runs in CI, so edit a set and check this
python eval/tools/label_gold.py --owner <you>   # label your ~33 of data/gold/deeplink_gold.jsonl
python eval/gate_replica.py --api http://localhost:8000 --results results.jsonl   # G2-G5 + A1-A5 + adversarial set, each query twice on an empty cache
python eval/judge.py          # step accuracy 0-3, deeplink relevance 0-2 (kit, Display)
python eval/judge.py --api http://localhost:8000 --sets unseen --out eval/results/judge_unseen.json   # Battery, Camera, Performance
python eval/loadtest.py --mode api --api http://localhost:8000   # p50/p95 for repeat-hit, paraphrase-hit, cold
python eval/report.py         # regenerates docs/metrics.md
python eval/tools/record_story.py   # re-records the site's walkthrough from the real engine (spends quota); rebuild console/

# Console stream: runs the real pipeline, so it spends LLM quota (run from repo root)
curl -N -X POST localhost:8000/v1/troubleshoot/stream -H "Content-Type: application/json" \
     -d @data/fixtures/touch_lag/request.json          # ?mock= only applies with settings.stream_mock on

# Console (Next.js 16 + React 19 + Tailwind v4 — see console/README.md)
cd console
npm install
npm run dev                   # http://localhost:3000
npm run lint && npm run typecheck && npm run build
```

`test_all_modules_import` in [api/tests/test_api.py](api/tests/test_api.py) walks every package: a syntax error or bad import anywhere in `app/` fails the suite, even in code nobody calls yet.

## Architecture

One request through [api/app/pipeline/run.py](api/app/pipeline/run.py):

```
normalize -> cache lookup -> enrich (LLM A) -> segment -> extract (LLM B)
          -> ground -> resolve -> categorize -> order -> multi-intent
          -> compile -> validate -> cache write
```

- **normalize / slots** — whitespace and numbering fixes (every line of a numbered kit query), URL/email scrub of the SIIS text **and the complaint** *before any LLM sees them*, lost spaces put back in a glued article (`pipeline/deglue.py`: rows 3/11/17 arrive as "restartyourdeviceandensure..."; only runs no dictionary knows, only in an article with several of them, and only spaces are added — a clean article is never touched), `siis_hash`; slots come from `data/slot_lexicon.json`, never from an LLM. The scrub (`compiler/scrub.py`) canonicalises first (HTML entities, NFKC, invisible characters) and every pattern is length-bounded, so it stays linear on hostile input; keep it that way (no unbounded `+`/`*` before a required character).
- **cache** — Tier 0 exact (`norm_query + siis_hash`), Tier 1 semantic (brute-force cosine over each solved plan's original query *and* its 8–10 variations). A hit requires similarity ≥ τ (0.70) **and** compatible slots (component, intent, one-sided symptom, and for configure requests the add/remove `direction` read from both texts, so "I want to add a floating circle" is never served "...I want to remove it") **and** a matching SIIS hash, from a plan made under the current `prompt_version`. Before matching, misspelt words are put right (`pipeline/spell.py`, `cache_typo_correction`) for the slots and the cache's embeddings only, never for what an LLM reads: "screne stays blnak" matches as the blank-screen complaint it is (in-process cache test, 2026-09-30: typo paraphrases 65% → 92.5%, all paraphrases 83% → 88.5%; with the direction guard near-miss false hits 1 → 0 of 60 at no loss of paraphrase hits). The SIIS cache ships empty.
- **no article** — missing, `null`, `""`, `{}`, whitespace or title-only content is one case ([cache/no_siis.py](api/app/cache/no_siis.py)): the same question answered before → a cached plan from the kit table (results.jsonl, loaded at startup) or any solved plan, similarity ≥ 0.80 + slot guard (`source: cached_plan`) → the full pipeline over a remembered article (every article the API receives, the kit's pre-loaded), similarity ≥ 0.82 and 0.04 ahead of the runner-up, scores scaled by that similarity (`source: retrieved_article`) → otherwise empty. All three carry `fallback: no_siis_context`. The LLM never fills the gap (FAQ Q14, G5). `no_match` means an article was given and there is no grounded answer: nothing survived grounding, every model that answered chose nothing from the article (at least `extract_empty_votes` of them, one of them the ladder's first, 14B: `extract_empty_needs_primary`, because Flash-Lite and 8B alone turned kit row 7 away while 14B was busy), rules-only extraction met an article whose best section scores under `rules_min_relevance`, or the complaint holds no readable word (`pipeline/text.recognisable`).
- **capacity** — at most `cold_max_concurrent` (8) cold pipelines run at once ([pipeline/capacity.py](api/app/pipeline/capacity.py)). A request that finds no slot within `cold_queue_wait_s` (0.25 s) gets the rules-only answer, which calls no model, is score-capped and is cached for `degraded_cache_ttl_s`, instead of queueing. So cache hits never wait behind cold load, and a burst on the open API cannot outspend the free tier. The limit is global, not per IP, because the scorer is one IP. The sync endpoints get `api_thread_limit` (100) worker threads, more than the cold limit, and call C's pool is sized to it (`coverage_workers`). Turned-away requests show `capacity:cold_busy` in their trace.
- **enrich** — canonical query, 1–3 intents, domain, 2–3 word title, 12 candidate variations filtered down to 8–10 (drop token Jaccard ≥ 0.6, drop embedding cosine < 0.6).
- **segment / extract** — SIIS split into sections and numbered sentences `S1…Sn`; the LLM returns actions whose every step cites sentence ids. The listing's breadcrumb (`<categories> <Title> (<categories>): `) is dropped, but article text between it and the first `#` is kept as a first section titled after the breadcrumb (row 3's fixes live there). The model's selection is then repaired by rule, never by adding words: a chosen introducing line ("To perform a factory data reset:") brings the instructions under it, a procedure the model joined at its end gets its start, an action that stops short of its own setting gets the step that reaches it, every method after "There are two methods for this:" is kept, a second procedure hung on an action ("To exit Safe mode, ...") is split off, repeated selections are merged, A selection with no instruction keeps only questions ("Did you drop your phone?"), never explanations.
- **coverage (call C)** — runs next to call B: 14B while 14B has made fewer than 10 requests in the last minute, else 8B (`coverage_leave_primary`), so under load call B keeps 14B to itself marks which of the article's instruction *paragraphs* (its lines, `P1…Pn`) help with the complaint, and names each. The model picks only ids and a name; every step is still the article's own sentence. Paragraphs call B skipped become actions (neighbours in one section merge), a marked paragraph call B took the start of is continued (never past an escalation or another critical kind), and in a numbered procedure the general-fix steps (damage, charging, restart, Safe mode, update, reset, support) and the rest of any step the plan follows are added, up to `extract_complete_max_actions` (12). A numbered heading that is itself the only instruction ("3. Update Device Software") is a step citing its section. Goals call B invented (not close to the complaint in meaning or words) fold into the one it states (`merge_unstated_goals`). Without call C's answer in time, `complete_procedure` (every skipped numbered step) stands in. A 429 on call C never puts 14B on cooldown for call B. Call C also returns `match` (`coverage.v2.md`): `related` or `unrelated`. An article is turned away (no_match) only when the model says `unrelated` **and** `pipeline/mismatch.py` agrees: none of the customer's own words appears anywhere in the article, ignoring stopwords, UI verbs, generic words and the general-fix vocabulary (charge, restart, update, reset, support); a word triggers the check only if it is common English, any shared word (dictionary word or not) saves the article, and a complaint holding a word that is neither English, Samsung vocabulary nor in the article (a misspelling) gets no verdict. Call B's problem statements are deliberately not counted: they explain causes ("software malfunctions") that any article shares, which blinded the check for "phone is hot". It also applies when call B failed and the rules extractor answered (`extract.judge_rules`); `coverage_mismatch = "off"` ignores the verdict. Neither signal alone is safe, and both were measured: embedding relevance cannot make this call ("phone is hot" against the touchscreen article scores 0.62, a real kit row 0.63), the model alone flags 16% of the paraphrases of well-matched kit rows (row 8: 10 of 10; the judge scores those plans 3 of 3, and about a third of the kit's pairs are that loose), and the word check alone flags misspelt paraphrases. Together they act on 0 of 17 kit complaints, 0 of 15 unseen, 0 of 59 near misses and 0 of 168 matched paraphrases (1 of 30 paraphrases of the loose rows 7, 10 and 20), and on "phone is hot" against the touchscreen article; the price is recall: the word check is shut when the article shares any word, and the label has to agree. A second deterministic path (`mismatch_component_path`, 2026-09-30) covers the stray shared word: a fault complaint with a symptom names a part (camera, battery...) that the article never mentions by any of its lexicon phrases ("camera app crashes" against an article that shares only "app"). It still needs call C's `unrelated`, except on a rules-only answer (no key, the capacity limit), where no model can give a label and this path alone decides (`extract.judge_without_verdict`): without it, a keyless run answered a camera complaint from a water-damage article at 0.81. Both checks read the whole cleaned article, breadcrumb and title included: kit row 10's article names the screen only in its title. `eval/tools/measure_mismatch.py` (no LLM) measures the deterministic half: 0 component fires on 295 matched pairs, and it fires on 57% of 615 cross-domain pairs (word path alone 34%). Re-measure with both models before loosening either half. Call C races 14B and 8B (`coverage_prefer_deadline_s`, 4 s): 14B alone stalled to its timeout on 3 of 6 direct calls, and a stalled call C meant no verdict.
- **ground** — a step survives only if it clears the embedding threshold against its cited sentences **and** shares a content term with them, or is a word-for-word run of a cited sentence ("Tap OK." from "Tap Clear data, and then tap OK."). Failing steps are dropped; actions left empty are dropped.
- **resolve** — `screen_path + verb` → BM25 + dense over catalog entries → RRF (cross-encoder rerank off, `use_rerank`) → Screen Graph screen → entry chosen by polarity. Tiered outcome: catalog link, `bixby://dummy_positive`, or manual.
- **compile / validate** — builds the official `ContextDeeplinkResponse`, then URL scrub → schema validation → one repair cycle → drop the offending action.
- **meta.reason / meta.message** — the schema has only `contexts`, so every empty answer, every answer from memory and every rules-only answer says why in `meta` ([compiler/messages.py](api/app/compiler/messages.py)): `no_article`, `no_article_cached_plan`, `no_article_retrieved`, `article_mismatch` (the gate acted, or an already-empty answer's article fails the deterministic check), `no_grounded_fix`, `unreadable_query`, `invalid_request`, `rules_only`. Built by rule and link-free; a cache hit repeats the reason stored with its plan. The console's Live section shows the message.

The endpoints are listed in [README.md](README.md); the full design lives in `docs/Smart Guided Troubleshooting Engine - Architecture & System Design.pdf` and should be exported into [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Hard rules

1. **Never edit [api/app/schema.py](api/app/schema.py).** It is the organisers' official file; ruff is configured to skip it. Every response must validate against it.
2. **Never invent a step.** Every step traces back to SIIS sentence ids. If nothing survives grounding, return empty `contexts` with `fallback: "no_match"` — an empty answer beats a fabricated one.
3. **Zero URLs in output.** No `http(s)`, `www.`, bare domains, emails or markdown links in any string. The catalog `bixby://` URIs in deeplink fields are the only exception. Run the scrub on cache hits too.
4. **Always 200 from `/v1/troubleshoot`,** with a schema-valid body, whatever fails internally. Degrade, never error. Malformed requests too: `TroubleshootRequest` coerces a missing or null query, a number or a list of articles, a query over `query_max_chars` is cut, and a body that is not a JSON object gets the empty `invalid_request` answer (`main.py`). Only a bad query parameter (`?mock=`) keeps FastAPI's 422.
5. **Thresholds and budgets live in [api/app/config.py](api/app/config.py).** Never hard-code a number in a module; add new settings under your own section header.
6. **[api/app/models.py](api/app/models.py) is the frozen shared contract.** Change it only by team agreement, merged by Vishaal.
7. **Never commit `.env` or API keys.** `data/kit/` contains a real-looking address (`kidshome.pin@samsung.com`) — that is exactly why the SIIS scrub runs before the LLM call.
8. **Copy catalog values verbatim.** The deeplink URIs in `data/kit/deeplinks.json` are masked placeholders; match on `description` / `message` / `qna_description` / `originalType`, then copy the URI and the entry's *own* validation object unchanged.
9. **Gates stay green.** After M1, no PR merges if it breaks G2–G5 on the gate replica.

## Output string rules (compiler)

These are graded, so build them by rule rather than letting the LLM free-write them:

| Field | Rule |
| --- | --- |
| `goal` | Strip a trailing "Troubleshooting"/"Configuration" from the topic, then `Follow these steps to perform this {Topic} Troubleshooting.` |
| `title` | LLM-proposed, 2–3 words, sentence case |
| `actionName` | Title Case, deduplicated; same-screen actions merged into one |
| `description` | `It will` + 5–7 words **total, counting "It will"** |
| `steps` | Imperative, trailing period, no numbering |
| `score` | `0.4 * section relevance + 0.3 * grounding coverage + 0.3 * link coverage`, clamped 0–1 (catalog link = 1.0, dummy = 0.5, manual excluded) |
| `category` | `critical` for restart / safe mode / software update / factory reset; `manual` for physical or external steps; `auto` only when a link resolved |

[data/kit/sample_output.json](data/kit/sample_output.json) is the reference shape.

## Conventions

- Python 3.11 in Docker, ruff targets py310 — do not rely on 3.11-only syntax. Line length 110.
- Absolute imports from the `app` package (`from app.models import DraftAction`), never relative.
- Typed signatures and pydantic models throughout: internal models in `models.py`, official output in `schema.py`. Only compiler output crosses into `schema.py` types.
- One module per design component, with the component number in the docstring. Keep stub signatures stable — other lanes code against them.
- LLM calls go through [api/app/llm/router.py](api/app/llm/router.py), never a client directly. The team runs on **free tiers** (no billing): measured on 2026-09-24, every Gemini model answers 503 on the free tier under load and Mistral's free plan returns 429 for Small/Medium, but serves the open-weight Ministral 3B/8B/14B (~80–100 tokens/s, concurrent calls allowed). So call B (extract) runs in **select mode** (`extract.v3.md`): the model lists the article sentence ids of each action instead of rewriting steps, and the steps are the article's own sentences, split into single instructions. The answer is compact one-line JSON with short keys (`ids`, `desc`, `path`, `verb`; `extract._long_keys` renames them), `ids` is an enum of the article's own sentence ids (so no invented id can be decoded), at most `extract_max_actions` (8) actions per goal, and `path` must reach the setting itself, not its parent menu. An action whose name shares no word with its own steps and screen (the model labelled it after another sentence) is renamed from its screen. `ministral-14b-latest` is raced against `gemini-3.5-flash-lite`, with `ministral-8b-latest` stepping into the race when either is skipped (bake-off 2026-09-30, see decision 2); 14B wins if it is back within 6 s (`extract_prefer_deadline_s`; measured 2026-09-27: 14B median 3.9 s, p90 5.9 s, and 8B skips more of an article's fixes), and the stage gives up at 6.3 s so a cold answer stays under 8 s. Intents come from call B too; the 8–10 variations come from a background call (`variations.v1.md`) on **Gemini 3.1 Flash-Lite, then 8B** that never delays an answer, and call C (`coverage.v1.md`) runs alongside call B. Mistral's free plan limits requests **per model per minute** (`x-ratelimit-limit-req-minute`: 14B 30, 8B 188), so 14B is kept for extraction and coverage: with variations on it too, a run of 20 kit rows put it on cooldown and the last 9 answers were 8B's. Each stage walks a model ladder ([llm/registry.py](api/app/llm/registry.py); lists settable by `ONECLICK_EXTRACT_MODELS` etc.). A rung with no key, on cooldown, or at its request limit ([llm/quota.py](api/app/llm/quota.py), which also reads Mistral's rate-limit headers) is skipped without a call. There is no last-resort model: `mistral-small-latest` is served 0 requests a minute on the free plan. A model answering 429/5xx is skipped for 20 s (the limit refills like a bucket: a burst of ~16 14B requests in 30 s draws 429s). All of it is in the engine section of `config.py`; with billing, `extract_mode = "rewrite"` and Gemini models switch back to the original design. Gemini runs at temperature 1.0 (Google's Gemini 3 guidance), Mistral at 0; repeatability comes from the cache and the compiler. With no key set, or every provider down, the pipeline runs rules-only (the article's own instruction sentences from its `rules_max_actions` (4) most relevant sections, score capped at 0.5). Every valid answer is cached, a degraded one too, so repeats are fast and identical; a degraded one only for `degraded_cache_ttl_s` (10 min), after which the question gets a cold run again. Variations that change the problem's slots (a different component, symptom or intent) are dropped unless fewer than 8 would be left. Prompts are versioned files in `api/app/llm/prompts/` (versions in `settings.prompt_versions`); add a new version rather than mutating a shipped one, and change `settings.prompt_version` (the cache-key tag) with it.

## Ownership and workflow

[docs/TEAM.md](docs/TEAM.md) is the authoritative split — read it before touching an unfamiliar directory.

| Lane | Owner | Directories |
| --- | --- | --- |
| Engine & integration | Vishaal (lead) | `api/app/pipeline/` (except `slots.py`), `compiler/`, `llm/`, `main.py`, `routes/troubleshoot.py`, `routes/stream.py`, `models.py`, `scripts/make_results.py` |
| Mapping, cache & infra | Karur | `api/app/screengraph/`, `retrieval/`, `cache/`, `device/`, `obs/`, `pipeline/slots.py`, `routes/device.py`, `routes/metrics.py`, the other `api/scripts/`, Docker & hosting, `data/slot_lexicon.json`, `data/appliance_exclusions.json` |
| Console & evaluation | Nikhil | `console/`, `eval/`, `docs/metrics.md`, `docs/deck/`, `docs/VIDEO.md` |

Stay in your lane; needed changes elsewhere go through a GitHub issue tagging the owner. Branch → PR → green CI → merge, with branches named `feat/<area>-<thing>` or `fix/<area>-<thing>`. No direct pushes to `main`.

## Gotchas

- `data/build/` is gitignored, so the Screen Graph and indexes are never committed — build them locally (and bake them into the image) before expecting link resolution to work.
- The console service (`console/Dockerfile`) is prerendered from repo files outside `console/` — fixtures, prompts, `config.py`, the catalog, gold labels, `eval/sets/`, `docs/metrics.md` — so `.dockerignore` excludes `docs/` and `eval/` *except* `docs/metrics.md` and `eval/sets/`. Keep those exceptions. Its `NEXT_PUBLIC_API_URL` is baked in at build and called by the viewer's browser (`ONECLICK_PUBLIC_API_URL` in compose).
- The docker build context is the repo root. The image copies `data/kit/`, `slot_lexicon.json`, `dependencies.json`, `appliance_exclusions.json` and `results.jsonl` into `/data` and builds the Screen Graph and vectors itself; `data/fixtures/` is **not** in it, so the mock stream only works from a checkout.
- `/health` returns 503 until the catalog, Screen Graph, vector index and cache snapshot are loaded (`app/obs/readiness.py`). Startup also pre-warms the no-article path (~2 s): the kit table from `results.jsonl` and the 11 kit articles. The Docker image must contain `results.jsonl` (`/data/results.jsonl`, or set `ONECLICK_RESULTS`); without it the kit table is empty and only article memory answers.
- `cache.sqlite` (default `sqlite_path`) is written into whatever directory the API is started from, and the API reloads it at boot. A leftover file makes the next gate-replica run start warm, so delete it (or set `ONECLICK_SQLITE`) before measuring cold numbers. Tests (`api/tests/conftest.py`) use a throwaway file and blank LLM keys, so they never warm the dev cache or spend quota. `scripts/make_results.py` always uses a throwaway file (it clears the cache before every row) but needs the real keys: it is the submission run.
- `results.jsonl` at the repo root is generated by `scripts/make_results.py`; it is the submission artefact, not a scratch file.
- `POST /v1/troubleshoot/stream` runs the real pipeline (`pipeline.run.run_stream`); `/v1/troubleshoot` drains the same generator, so the two can never disagree. Setting `settings.stream_mock = True` replays [data/fixtures/](data/fixtures/README.md) instead, with frames marked `X-Mock: true` / `detail.mock`; that only works when the API runs from the repo.
- `data/fixtures/` is a contract for Karur and Nikhil, guarded by [api/tests/test_fixtures.py](api/tests/test_fixtures.py): official schema, zero URLs, verbatim catalog links, every step traced to a real article sentence, the score formula. If you change a fixture, keep those tests green and tell the other lanes.
- Catalog quirk the fixtures and the simulator must respect: all 138 `onURL` (enable) entries carry a full validation object (key, condition, value) and every `offURL`, `onClickURL` and `updateURL` entry is key-only. So the design's "138 fully validatable entries" are exactly the enable toggles, and only those can show "Verified". `DL-0022 View Reset Options` is the *auto* factory reset, not Factory data reset.
- A catalog entry's `message` can contradict its `description`: `DL-0397`/`DL-0398` read "Adaptive Display" but are adaptive **battery**. Match on `description`. Some entries are exact duplicates (`DL-0518`, `DL-0552`). Several common screens have no entry at all — software update, Safe mode, Dark mode, auto-rotate, per-app storage, Factory data reset — so those steps resolve to `bixby://dummy_positive` or stay manual, and that is the correct answer, not a bug.
- **`.gitignore` entries must be anchored.** It started as the stock Python template, whose
  unanchored `lib/` silently swallowed `console/lib/` — the console would have been committed
  without its design tokens and failed to build for everyone else. The distribution/packaging
  entries are now anchored (`/lib/`, `/build/`, `/dist/`…). Do not re-add an unanchored directory
  name; it matches at every level, not just the repo root.
- The story page replays a **real** engine run from `console/recordings/`, written by
  `python eval/tools/record_story.py` (shipping pipeline, real keys, throwaway cache). Re-record after
  any engine change the page shows (prompts, extraction, resolver, compiler) and rebuild the site.
  `data/fixtures/` stays the engine tests' illustrative contract; the page no longer renders it.
- `next dev` generates `console/AGENTS.md` and `console/CLAUDE.md` and keeps regenerating them.
  Both are gitignored — the second would otherwise collide with this file.
- `data/gold/deeplink_gold.jsonl` is the answer key for deeplink precision@1, split ~33 each; 123 are labelled so far (Karur 87, Nikhil 36). Label yours with `eval/tools/label_gold.py`, and read the chosen entry's own description before accepting it — the tool's BM25 shortlist gets 1 in 3 wrong.

## Open questions with the organisers

Answers may change compiler behaviour, so check before "fixing" these. **Answered** by FAQ v4: the goal string ends with a period (Theme 2 Q3, Q6), as the template does. **Still open:** is an extra `meta` key allowed in the body (behind `settings.include_meta`; the spec's Appendix B example carries one and pydantic ignores extra keys, so it stays on); do service-centre steps come before or after critical actions (critical last for now).

## Hackathon submission (final week)

Everything left before the tag, and the organisers' rules behind it. Decided 2026-09-28; update the Status column as items land. The organisers' spec PDF and FAQ v4 are deliberately **not** in the repo (see the rules at the end), so this section is the team's copy of what they say. "T2-Qn" is the FAQ's Theme 2 question n; "Qn" is its general section.

### How we are judged

- **Automated score, 60 points, from the organisers' scorer calling our API** (T2-Q21, Q22). The scorer:
  - calls `/health`
  - sends the canonical kit queries (cold latency)
  - sends the same queries again (cache hit)
  - sends paraphrases
  - sends unseen scenarios with new SIIS payloads

  It sends no API key and no auth header, so the API stays open. **The judges run it locally** from the repo (Vishaal, 2026-09-28): the submission asks for a reproducible README and Docker files (Q17), and the form has no field for a URL. So the README is the whole automated score. It must take a judge from clone to a working `/health` and a first answer on a clean machine, with or without LLM keys.
- **Gates** (all must pass, or the automated score does not count; T2-Q9):
  - G2: `/health` → `{"status":"ok"}`, body exactly that
  - G3: ≥ 95% of test queries in `results.jsonl`
  - G4: ≥ 90% schema-valid
  - G5: zero URL leaks
- **Blocks** (T2-Q10):
  - A1 schema & format: 15
  - A2 deeplinks exist in the catalog, and every auto action has one: 15
  - A3 cache & latency: 15. Repeat p95 ≤ 300 ms at ≥ 90% hits, paraphrase hits ≥ 80%, cold p95 ≤ 8 s, measured on the judge's machine against our API running there.
  - A4 unseen scenarios valid and **non-empty**: 10
  - A5 8–10 diverse variations: 5

  Invented steps are penalised in manual review (T2-Q14). The spec's metrics.md template wants **N ≥ 30 requests per latency path**.
- **Jury review** of the tagged repo, deck, video and final demo (Q22):
  - working prototype & functionality 30%
  - technical depth & feasibility 25%
  - innovation & originality 20%
  - relevance to theme 15%
  - presentation & documentation 10%

  The jury also weighs whether it is useful to a real user and could be taken further as a PRISM worklet (Q24). The final demo is a live walkthrough plus questions on design decisions and trade-offs (Q26).

### Settled by the FAQ (do not re-open)

- Goal format: `Follow these steps to perform this <Name> Troubleshooting.` or `... Configuration.`, **with the period** (T2-Q3, Q6).
- A results.jsonl line is `{query, query_variations, response}` (T2-Q17). Our extra `meta` is ignored by pydantic; keep it.
- auto must carry an `actionableDeeplink`, and critical is treated like manual (T2-Q7). `dummy_positive` gets our own 5–7 word description and message naming the screen (T2-Q15).
- "Any LLM API (Gemini and Mistral will have bonus points)" (T2-Q19).
- Tagging (Q19, Q20): `git tag -a PRISM_GENAI_HACKATHON_Y2026 -m "PRISM Gen AI Hackathon Y2026 Final Submission"`, then `git push origin PRISM_GENAI_HACKATHON_Y2026`. The tagged commit is what gets judged, and it must contain everything it references.
- Submission files are named `CollegeName_TeamName` (Q12, Q21).
- Key dates (Q27): top 15 announced 9 Oct 2026, final demo 15 Oct, results 24 Oct.
- FAQ v4 prints the final submission as 25 Sep 2026. That date is out of date: the submission window is later (Vishaal, 2026-09-28), so the schedule below is our own internal target, not the organisers' deadline.

### Decisions (2026-09-28, Vishaal)

1. **No hosting.** Judges clone the repo and run it locally (Docker, or Python for the API alone); `docker compose up` also serves the site on `:3000` for the walkthrough. Two consequences:
   - The README and Docker setup must work first time on a clean Windows, Linux or macOS machine.
   - A judge running it locally uses **their own** LLM keys, or none (T2-Q22: no key is provided). Without keys every answer is rules-only: valid and grounded, but weaker. The README must say this, and how to get free Mistral and Gemini keys in minutes. That is one more reason for free tiers.
2. **LLMs: free tiers, Mistral and Gemini only (final, 2026-09-29).**
   - Calls that write the answer: call B races `ministral-14b-latest` against `gemini-3.5-flash-lite`, then `ministral-8b-latest` (updated 2026-09-30), and call C runs 14B then 8B. 14B stays first: over 3 runs of kit + unseen (judge `gemini-3.5-flash-lite`) it scored kit 2.60 in every run and unseen 2.76, against flash-lite's kit 2.40 (2.25–2.60) and unseen 2.76, which is 8B's level. Flash-lite is the racer so that a Mistral outage or limit still leaves another provider, and 8B takes its place when it is skipped. `gemini-3.5-flash` is ruled out: its free tier is 20 requests a day per project.
   - The background variations call ("query understanding" in T2-Q19) runs `gemini-3.1-flash-lite`, then 8B, for the Gemini bonus. It never touches or delays an answer.
   - There is no last-resort model (`fallback_model = ""`), and no `reasoning_effort` is sent to Mistral (`fallback_reasoning = ""`). See decision 3.
   - Only the Mistral and Gemini clients ship. Groq and NVIDIA were tested and dropped, and their code was removed.
   - Never suggest GitHub Models (retired 30 Jul 2026) or gpt-4o-mini (closed and paid).
3. **The bake-off decided it** ([eval/results/bakeoff/SUMMARY.md](eval/results/bakeoff/SUMMARY.md); raw outputs stay local). The rule: adopt a new ladder only if it beats today's on kit and unseen, keeps cold p95 ≤ 7 s, stays 100% schema-valid with 0 leaks, and returns 100% non-empty answers on unseen.
   - **Groq `gpt-oss-120b`** tied Ministral on step accuracy (kit 2.53 vs 2.53) and was faster, but not better. It is capped at 8K tokens a minute, and it gives false "no match" answers on hard kit rows. Groq's Qwen also returned false "no match" answers.
   - **NVIDIA's free endpoint was unusable:** 30–40 s timeouts, 404s and 503s.
   - **Gemini Flash-Lite won variations:** half the overlap between variations and no wrong plan served.
   - **Two settings bugs were bigger wins than any model swap:**
     - `mistral-small-latest`, the old last resort, is served 0 requests a minute on the free plan.
     - `reasoning_effort` made every Ministral call fail once with a 400 and retry, halving 14B's capacity and adding ~0.3 s.
4. **The cache ships empty.** `cache.sqlite` never goes into the repo or the image (already in `.gitignore` and `.dockerignore`), so a judge's first call on a query is genuinely cold, as designed.
5. **The cold-capacity guard stays.** A judge's load test can fire many cold requests at once, and over the limit a request degrades to rules-only instead of queueing behind the others.

### Deliverables checklist

| Item | Where | Owner | Status |
| --- | --- | --- | --- |
| Source code in a public repo | `VishaalPillay/OneClick` (public) | all | done |
| README: clone-to-first-answer for Docker and Python, Python version, optional keys (free sign-ups), keyless rules-only mode, an example request, how to reproduce our numbers, links | `README.md` | Vishaal | todo |
| `requirements.txt` at the repo root, exact pins | `requirements.txt` | Vishaal | todo |
| Deck, PPTX + PDF, the organisers' 12-slide template | `docs/deck/CollegeName_TeamName_Submission.*` | Nikhil | todo |
| Demo video ≤ 5 min (YouTube or Drive) | link in README and `docs/VIDEO.md` | Nikhil | todo |
| AI disclosure | `AI_DISCLOSURE.md`, linked from README | Vishaal | todo |
| `results.jsonl` from the final engine | `results.jsonl` | Vishaal | regenerate after freeze |
| `metrics.md` at the final commit | `docs/metrics.md` | Nikhil | regenerate after freeze |
| APK/SDK | README: N/A | Vishaal | todo |
| Tag pushed, form submitted | `PRISM_GENAI_HACKATHON_Y2026` | Vishaal | todo |

### Remaining work by lane

- **Vishaal**
  - ~~Cold-capacity guard (`pipeline/capacity.py`)~~ **Done 2026-09-28.** Bounded cold runs with rules-only over capacity, `_coverage_pool` sized by `coverage_workers`, anyio thread limit raised. Its state (`capacity.state()`) should reach `/v1/metrics` readiness; `routes/metrics.py` is Karur's, so this goes through an issue.
  - ~~LLM ladders~~ **Done 2026-09-29:** `llm/openai_compat.py`, `llm/registry.py`, `llm/quota.py`, `ONECLICK_*_MODELS` overrides, and no closed model on the answer path.
  - ~~Bake-off~~ **Done 2026-09-29** (decision 3). Final models applied in `config.py`.
  - `console/lib/story.server.ts` reads `fallback_model` from `config.py` and shows its own default ("gemini-3-flash-preview") when the value is empty, which it now is. Nikhil: show "none" instead, then re-record the story.
  - README, AI disclosure, `requirements.txt`.
  - `results.jsonl`.
  - Tag.
- **Nikhil** (evals against the problem statement)
  - ~~≥ 30 cold samples, and judging unseen per domain~~ **Done in PR #29 (2026-09-29).** It also added the adversarial set to the live gate replica and classified paraphrase hits by the plan they served.
  - Gate replica + loadtest against a fresh-clone Docker run, the way a judge will, plus a mixed-load run.
  - Grow unseen to 10 per domain, and track the A4 non-empty rate.
  - More independent gold labels.
  - `metrics.md` regeneration on the final engine (after the model change of 2026-09-29).
  - Deck, video.
- **Karur** (mapping, sequencing, fast-path cache, Docker)
  - Docker: `docker compose up --build` works first time on a clean machine (Windows, Linux, macOS), and `docker compose up api` alone works; checked from a fresh clone.
  - ~~The semantic tier respects `prompt_version`.~~ **Done 2026-09-30:** plans carry the version and `semantic._search` skips other versions.
  - Bound `_key_locks`; stop `np.vstack` on every insert.
  - Recover the 5 over-cautious catalog misses.
  - Ordering check against spec §6: Settings toggles → system optimizations → device reboots.

### Schedule

| Date | Milestone |
| --- | --- |
| Tue 29 Sep | Capacity guard and LLM ladders merged (Mistral defaults) |
| Wed 30 Sep | Bake-off; README, disclosure and requirements drafts; deck draft |
| Thu 1 Oct | Adopt the winning ladder or keep today's; **engine freeze** at end of day |
| Fri 2 Oct | `results.jsonl`, story recording and `metrics.md` regenerated; video and deck final |
| Sat 3 Oct | Fresh-clone rehearsal (Docker and Python, with and without keys), tag, push, form |

After the freeze, only packaging commits land: docs, deck, video link, and results/metrics regeneration.

### Rules for this phase

- Never commit the organisers' spec PDF or FAQ. Both carry per-recipient watermarks, and this repo is public; summarise them here instead.
- Everything the tagged commit references (deck, results, metrics, video link) is committed before tagging.
- Measure A3 on a fresh-clone run, the way a judge will, not on a dev machine with a warm cache.
