# OneClick — Team Work Split

Who owns what, in detail. Read your own section fully and skim the others: you need to know what your neighbours provide and expect from you.

Source of truth for design: `docs/ARCHITECTURE.md` (exported from the team design doc). Component numbers below (C1, C2, …) match its "Component deep dive" section.

| Member | Lane | One-line mission |
| --- | --- | --- |
| **Vishaal** (lead) | Engine & integration | Turn a complaint + SIIS article into a correct, grounded, schema-valid plan |
| **Karur** | Mapping, cache & infra | Right deeplink every time, sub-50 ms repeats, a server that never sleeps |
| **Nikhil** | Console & evaluation | Make the engine visible for the video, and measure it honestly |

---

## Ground rules (everyone)

1. **Stay in your lane.** Only edit files in the directories you own. Need a change elsewhere? Open a GitHub issue and tag the owner.
2. **Never edit `api/app/schema.py`.** It is the official file.
3. **`api/app/models.py` is the shared contract.** Only Vishaal merges changes to it, after the team agrees.
4. **`api/app/config.py` is shared but sectioned.** Add settings only under your own section header. No thresholds hard-coded in modules.
5. **Branch → PR → green CI → merge.** Branch names: `feat/<area>-<thing>`, `fix/<area>-<thing>`. No direct pushes to `main`.
6. **Never invent steps, never emit URLs.** Every step comes from SIIS text; zero URLs anywhere in output.
7. **Never commit `.env` or API keys.**
8. **Gates stay green.** After M1, no PR may merge if it breaks any of G2–G5 on the gate replica.

---

## Vishaal — Engine & integration

### Owns

| Path | What |
| --- | --- |
| `api/app/pipeline/` | normalize, slots (calls Karur's lexicon), enrich, segment, extract, ground, categorize, order, multi_intent, run |
| `api/app/compiler/` | templates, trimmer, scrub, compile, validate |
| `api/app/llm/` | router, gemini, mistral, `prompts/` |
| `api/app/main.py`, `routes/troubleshoot.py`, `routes/stream.py` | App wiring, scored endpoint, SSE stream |
| `api/app/models.py` | Shared contract (merge authority) |
| `api/scripts/make_results.py` | Generates `results.jsonl` |
| `data/dependencies.json` | Prerequisite edges for ordering |
| `data/fixtures/` | Sample models for other lanes to test against |

### Builds

**C1 — Normalizer & scrubber.** Fix whitespace, strip list numbering and quotes, lowercase a key copy. Remove URLs, emails and domains from SIIS text before any LLM sees it (the kit contains `kidshome.pin@samsung.com`). Output `norm_query`, `siis_clean`, `siis_hash`. Slot extraction itself uses Karur's lexicon.

**C3 — Stage 1 Enrichment (LLM call A).** Returns canonical query, 1–3 intents, domain, a proposed 2–3 word title, and 12 candidate variations across five registers (formal, casual, keyword-only, frustrated, typo-inclusive). Filter for lexical diversity: drop any variation with token Jaccard ≥ 0.6 against the original or an already-kept one; drop any with embedding cosine < 0.6 to the original (off-meaning). Keep 8–10.

**C4 — SIIS segmenter & relevance ranker.** Split on `#` headers, then numbered sentences `S1…Sn`. Score each section per intent; below a floor = irrelevant (greyed in the demo).

**C5 — Stage 2 Extraction (LLM call B).** Per intent: topic, actions. Each action: `steps[]` with `src: [sentence ids]`, `screen_path`, `intent_verb`, draft `description`, category hint. JSON-schema forced output. Same-screen steps grouped into one action.

**C6 — Grounding verifier.** Step level: embedding similarity to cited sentences (start τ = 0.75) **and** at least one shared content term (screen, setting or key noun). Action level: the action's screen/setting must appear in its cited section. Drop failing steps; drop empty actions. Report grounding coverage.

**C8 — Categorizer.** Keyword overrides: restart, force restart, safe mode, software update, factory reset → `critical`. Physical and external steps → `manual`. `auto` only via Karur's tiered link decision.

**C9 — Orderer.** Disruption rank → dependency topological sort → SIIS order as tiebreak.

| Rank | Kind | Examples |
| --- | --- | --- |
| 0 | Quick physical checks, settings screens | Charge 30 min, inspect for damage, toggle a setting |
| 1 | App-level fixes | Clear cache, update or reinstall app |
| 2 | Connectivity & account | Reset network settings, re-sign in |
| 3 | Diagnostics | Samsung Members diagnostics |
| 4 | External help | Service centre, contact support |
| 5 | Critical | Restart < safe mode < software update < factory reset |

Dependencies (`data/dependencies.json`): backup → factory reset; safe mode → uninstall in safe mode; charge → force restart on a dead screen.

**C10 — Compiler.** Builds every scored string by rule.

| Field | Rule |
| --- | --- |
| goal | Strip trailing "Troubleshooting"/"Configuration" from topic, then `Follow these steps to perform this {Topic} Troubleshooting.` (or `Configuration.`) |
| title | LLM-proposed 2–3 words, validated, trimmed only if too long, sentence case |
| actionName | Title Case, deduplicated, same-screen actions merged |
| description | LLM draft; trimmer enforces "It will" + 5–7 words **total including "It will"** |
| steps | Imperative, trailing period, no numbering |
| score | 0.4 section relevance + 0.3 grounding coverage + 0.3 link coverage (catalog = 1.0, dummy = 0.5, manual excluded), clamped 0–1 |

Final pass on every response (including cache hits): URL regex scrub → official schema validation → one repair cycle → drop offending action.

**C11 — Multi-intent.** One Goal per intent. A duplicate action (same screen + verb, or near-identical steps) stays only in its most relevant Goal. Empty Goals dropped.

**LLM router (ADR-002).** Gemini Flash-class primary, Mistral fallback on 3 s timeout or error, temperature 0, versioned prompts (prompt version is part of the cache key). Records tokens and cost per call.

**Degrade paths.** Enrichment fails → normalized query as sole intent, template variations. Extraction fails → rules-only steps from top sections, score capped at 0.5. No step survives grounding → empty `contexts`, `fallback: no_match`. Stage budgets: enrich 2.5 s, extract 3.5 s.

**Orchestrator (`pipeline/run.py`).** normalize → cache lookup (Karur) → enrich → segment → extract → ground → resolve (Karur) → categorize → order → multi-intent → compile → validate → cache write (Karur).

**Endpoints.** `POST /v1/troubleshoot` (always 200, schema-valid, `meta` behind the `include_meta` flag, `X-Latency-Ms` / `X-Cache-Hit` / `X-Cost-Usd` headers). `POST /v1/troubleshoot/stream` emitting `cache → enrich → segment → extract → ground → resolve → compile → done`, each with `{stage, ms, summary, detail}`.

**`results.jsonl`.** One line per kit query: `query`, `query_variations`, `response`, with `meta` beside `response`.

### Provides to others
- `models.py` frozen at M0, plus `data/fixtures/` (sample `DraftAction`s, a full plan) so Karur and Nikhil can build without waiting.
- A working `/v1/troubleshoot/stream` for Nikhil's console.

### Needs from others
- Karur: `extract_slots()`, cache `lookup()`/`put()`, `resolve(action) → LinkDecision`, `dense.embed()` for grounding and segmenting.
- Nikhil: gate replica and judge results to tune prompts and thresholds.

### Done when
All gates pass on the live URL with real LLM output · grounding coverage 100% · schema-valid 100% · 0 URL leaks · cold p95 ≤ 4 s · step accuracy (judge) ≥ 2.5 / 3.

### Video scenes
Cold run, grounding hover, multi-intent.

---

## Karur — Mapping, cache & infra

### Owns

| Path | What |
| --- | --- |
| `api/app/screengraph/` | clean, build, resolver |
| `api/app/retrieval/` | bm25, dense, fuse, rerank |
| `api/app/cache/` | exact, semantic, slot_guard, lookup, store |
| `api/app/device/` | Device simulator |
| `api/app/obs/` | Logging, metrics, traces |
| `api/app/pipeline/slots.py` | Lexicon slot extractor (only this file in `pipeline/`) |
| `routes/device.py`, `routes/metrics.py` | Simulator and observability endpoints |
| `api/scripts/` (except `make_results.py`) | build_screengraph, build_index, build_lookup |
| `api/Dockerfile`, `docker-compose.yml`, hosting | Deployment |
| `data/slot_lexicon.json`, `data/appliance_exclusions.json` | Cache guard and catalog cleaning data |

### Builds

**C1 (part) — Slot extractor.** Regex/lexicon only, no LLM. `component` (screen, battery, camera, app, network…) and `symptom` (black, cracked, flicker, drain, overheating, slow…). Grow the lexicon from the kit and near-miss set.

**C7 — Screen Graph (offline).**
- Strip description boilerplate ("Opens the … page in device Settings on the device") before indexing.
- Remove true appliance entries only after a manual check; list them in `appliance_exclusions.json`. **Keep** TV / Smart View entries (kit covers screen mirroring) and the Diagnose entries DL-0474, DL-0475, DL-0476.
- Polarity from `originalType`; when null, from `message` (DL-0294 "Offurl", DL-0295 "Onurl"). `DL-DUMMY` is the catalog's placeholder entry.
- Cluster entries into screen nodes (shared validation deeplink, or near-identical cleaned description). Each node: path, synonyms, entries by polarity, each entry's **own** validation object.
- Index nodes: BM25 (weight `message` and `qna_description` above `description`) + dense embeddings + 5 LLM-written user phrasings per node.

**C7 — Resolver (runtime).** `screen_path + verb` → BM25 + dense over nodes → RRF → cross-encoder rerank → node confidence → entry chosen by polarity (`enable` → onURL/onClickURL, `disable` → offURL/onClickURL, `set` → updateURL/onClickURL, `open` → onClickURL). Mid confidence → LLM picks from top 3 ids (via Vishaal's router). Budget 500 ms; on timeout mark manual.

| Situation | Category | Deeplink |
| --- | --- | --- |
| Node match above threshold | auto | Catalog URI of the verb-matching entry; its own validation copied verbatim |
| Clearly one Settings screen, no node | auto | `bixby://dummy_positive`, description and message naming the exact screen (5–7 words) |
| Multi-screen, app-internal or vague | manual | None |
| Physical step | manual | None |

**C2 — Semantic cache (hot path, no LLM).**
- Tier 0: exact key = `norm_query + siis_hash`.
- Tier 1: embed the normalized **raw query**; ANN over the stored original + all 8–10 variations of every solved plan.
- Hit only if similarity ≥ τ **and** slots agree **and** SIIS hash matches.
- SIIS cache starts **empty** (scorer's first call must be genuinely cold). Only the no-SIIS lookup table is pre-warmed from the kit.
- Single-flight lock per key; SQLite persistence; every hit still goes through Vishaal's final scrub/validate.

**C12 — Device simulator.** State from catalog validation keys. 138 entries with key + condition + value → apply sets value, validate evaluates → "Verified". Others → pass = "screen opened", labelled as such.

**Embeddings (ADR-005).** Local ONNX small model (fastembed) + in-memory index. `dense.embed()` is shared with Vishaal's grounding and segmenting.

**Observability.** One JSON log line per request; rolling window (last 1,000) behind `/v1/metrics` (p50/p95, hit rate, cost); traces for last 200 behind `/v1/trace/{id}`.

**Deployment (ADR-007).** One image with model weights, Screen Graph, indexes and no-SIIS table baked in. `/health` returns 503 until all are loaded. Always-on VM or Cloud Run min-instances 1, 2 vCPU / 4 GB, uvicorn 2 workers. Uptime check pinging `/health` every minute during judging.

### Provides to others
- `extract_slots(norm_query) → Slots`
- `cache.lookup(...)`, `cache.put(CacheEntry)`, `lookup_no_siis(...)`
- `resolve(DraftAction) → LinkDecision`
- `dense.embed(texts)`
- Device, metrics and trace endpoints for Nikhil's console
- The live public URL from M1 onwards

### Needs from others
- Vishaal: `models.py` + fixtures at M0; the LLM router for mid-confidence tie-breaks and node phrasings.
- Nikhil: gold labels, paraphrase set and near-miss set to tune τ and thresholds.

### Done when
Deeplink precision@1 ≥ 85% on gold · catalog validity 100% · auto actions with a link 100% · dummy rate reported · repeat hit p95 ≤ 50 ms server-side · paraphrase hit ≥ 90% · false hits ≤ 2% · boot to healthy < 20 s · zero downtime in the judging window.

### Video scenes
Cache hit and near-miss block, Screen Graph explorer data.

---

## Nikhil — Console & evaluation

### Owns

| Path | What |
| --- | --- |
| `console/` | Next.js console (all of it) |
| `eval/` | gate_replica, judge, ablation, loadtest, report, `sets/`, `results/` |
| `docs/metrics.md` | Generated from eval runs |
| `docs/deck/`, `docs/VIDEO.md` | Deck PDF, video link |

### Builds

**Console (ADR-008).** Next.js (App Router), TypeScript, Tailwind, Framer Motion, shadcn/ui, Recharts. Deployed separately (e.g. Vercel), API URL from `NEXT_PUBLIC_API_URL`. One UI-inspired: soft neutral background, one accent colour, large rounded cards, light and dark mode, no Samsung logos.

| Route | Shows | Video scene |
| --- | --- | --- |
| `/` Console | Left: complaint box, preset chips, SIIS viewer. Centre: live pipeline trace (one row per SSE stage, real ms, expandable detail). Right: phone mockup with goal header, action cards, category badges, one-tap buttons, "Verified" tick. JSON toggle with schema-valid / 0-leak / cache-tier badges. Grounding hover highlights source sentences; irrelevant sections greyed. | Hook, cold run, cache, grounding, multi-intent, unseen |
| `/metrics` | Gates, p50/p95, hit and false-hit rate, precision@1, grounding coverage, dummy rate, cost, ablation chart | Under the hood |
| `/architecture` | Animated system diagram, Screen Graph explorer | Scale / worklet |

Frontend state: Idle → Streaming → Rendered → Applying → Verified/Failed → Rendered. Build against mock SSE and `sample_output.json` until M2.

**Evaluation harness.**

| Set (`eval/sets/`) | Size | Measures |
| --- | --- | --- |
| Kit scenarios | 20 | Gates, format, coverage |
| `paraphrases.jsonl` | ~200 | Paraphrase hit rate (never used to warm cache) |
| `near_miss.jsonl` | ~60 | False-hit rate, τ tuning |
| `unseen.jsonl` | ~15 | Battery, Camera, Performance generalization |
| `adversarial.jsonl` | ~15 | Typos, Hinglish, three-in-one, URLs in articles |

| Script | Does |
| --- | --- |
| `gate_replica.py` | G2–G5 + A1–A5 from the FAQ; each query sent twice on an empty cache |
| `judge.py` | Step accuracy 0–3 (LLM rubric, 20% hand spot-check); deeplink relevance 0–2 on gold |
| `loadtest.py` | N ≥ 30 per path (repeat hit, paraphrase hit, cold); p50/p95 |
| `ablation.py` | LLM mapping vs raw retrieval vs Screen Graph vs rules-only |
| `report.py` | Regenerates `docs/metrics.md` in the Appendix C format |

Also tracked: grounding coverage, dummy rate by catalog gap, variation diversity (mean pairwise token Jaccard), cost per query. CI runs the gate replica on every PR from M1.

**Deck.** `CollegeName_TeamName_Submission_ppt`, ~12 slides, same design tokens as the console. Theme ID, title, team · problem · insight · architecture · grounding · deeplinks · cache · validation loop · results · tech stack · limitations + Screen Graph roadmap · thanks.

**Video.** ≤ 5 min, storyboard from the planning notes, recorded against the live API, 1440p+, scripted voiceover (~650 words), captions and zooms.

### Provides to others
- Gate replica from M1, so every PR is checked.
- Test sets and gold-label template early, so Vishaal and Karur can tune.
- Honest numbers for `metrics.md`, deck and video.

### Needs from others
- Vishaal: `/v1/troubleshoot/stream` event format, fixtures.
- Karur: `/v1/metrics`, `/v1/trace/{id}`, device endpoints, Screen Graph export for the explorer, live URL.

### Done when
Every storyboard scene runs live end to end · gate replica in CI · `metrics.md` fully generated from real runs · deck and video in the tagged commit.

### Video scenes
Hook, all UI, final edit. Owns the video overall.

---

## Shared work

| Task | Split |
| --- | --- |
| Step → deeplink gold labels (`data/gold/deeplink_gold.jsonl`, ~100) | ~33 each |
| Near-miss and paraphrase writing | Nikhil owns the sets; everyone contributes ~20 |
| Voiceover for your own scenes | Each person |
| Final review of the tagged commit | All three |

---

## Milestones (in order, no fixed dates)

| Milestone | Vishaal | Karur | Nikhil | Exit check |
| --- | --- | --- | --- | --- |
| **M0 Contract** | Freeze `models.py`, add `data/fixtures/` | Review contract | Review contract | Everyone can code against fixtures |
| **M1 Walking skeleton** | Rules-only extractor + compiler + orchestrator | Keyword resolver, exact cache, **live always-on deploy** | Gate replica in CI, console on mock data | Live URL passes G2–G5 |
| **M2 Real engine** | LLM stages, grounding, ordering, multi-intent, SSE | Screen Graph, hybrid retrieval, semantic cache + slot guard, simulator | Console on live SSE, test sets, judge | All components real; gates still green |
| **M3 Quality loop** | Prompt + threshold tuning | τ, retrieval and latency tuning | Ablation, load tests, `metrics.md` | Internal targets met or documented |
| **M4 Freeze** | `results.jsonl` final | Uptime watch | Video, deck, docs | Tag `PRISM_GENAI_HACKATHON_Y2026` pushed |

## Open questions (emailed to prism@samsung.com)
- Does the goal string end with a period? (Following the FAQ regex until answered.)
- Is an extra `meta` key allowed in the API body? (Behind a flag until answered.)
- Service-centre steps before or after critical actions? (Critical last until answered.)