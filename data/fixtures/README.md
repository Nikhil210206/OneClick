# Fixtures

Sample data so Karur and Nikhil can build against the real shapes before the pipeline exists. Every article
sentence and catalog link here is copied from `data/kit/` (nothing retyped), and `api/tests/test_fixtures.py`
keeps it that way: official schema, zero URLs, verbatim catalog links, every step traced to a real article
sentence, the score formula, the variation-diversity rule.

```
data/fixtures/
  scenarios.json               manifest (query, article title, goals, folder) used by the mock stream and tests
  resolver_cases.json          Karur: pre-resolve DraftActions + the LinkDecision the resolver should return
  <scenario>/
    request.json               POST body: {query, siis_response} (article copied verbatim from the kit)
    plan.json                  the full response body: {contexts, meta} (validates against app/schema.py)
    draft_actions.json         Vishaal: compiler input (grounded, resolved, categorized, ordered, deduped)
    stream.json                the 8 SSE events of a cold run, in order (Nikhil)
    cache_events.json          the cache frame + meta for an exact and a semantic hit (Nikhil)
```

| Scenario | Why it exists |
| --- | --- |
| `touch_lag` | Kit row 21, an on-topic pair. Catalog toggles (enable and disable Touch sensitivity), an open-screen link, manual physical steps, critical actions, backup before factory reset. The headline run. |
| `email_not_responding` | Kit row 1, a partly mismatched pair. All three link tiers (catalog, dummy, manual), greyed-out irrelevant sections, a lower score, a same-screen merge, and a safe-mode dependency that moves an action after a critical one. |
| `touch_multi_intent` | **Authored** two-intent query on the touch article (no kit pair grounds two goals). Two Goals, duplicates removed from the less relevant one. |

## The mock stream (Nikhil)

`POST /v1/troubleshoot/stream` with a `request.json` body. While the pipeline is a skeleton
(`settings.stream_mock = True`) it replays these fixtures at their recorded timings. It is honest about it:
response header `X-Mock: true`, `detail.mock: true` on every frame, `meta.mock: true` in `done`. Show a mock
badge when you see them. Match is by normalized query (list numbering and quotes are ignored), then by article
title. A query with no fixture gets `cache` then an empty `done` (never an invented plan).

| Query param | Result |
| --- | --- |
| *(none)* / `?mock=cold` | all 8 events |
| `?mock=exact` | `cache` (Tier 0 hit) then `done` |
| `?mock=semantic` | `cache` (Tier 1 hit on a reworded query) then `done` |

The route is `POST`, so the browser `EventSource` cannot be used. Read the body as a stream:

```ts
async function streamTroubleshoot(body: object, onEvent: (ev: StageEvent) => void) {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/v1/troubleshoot/stream`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    for (let i; (i = buf.indexOf("\n\n")) >= 0; ) {
      const frame = buf.slice(0, i); buf = buf.slice(i + 2);
      onEvent(JSON.parse(frame.split("\n").find((l) => l.startsWith("data: "))!.slice(6)));
    }
  }
}
```

Wire format: `event: <stage>\ndata: {"stage","ms","summary","detail"}\n\n`. `ms` is that stage's own duration;
`done.ms` is the total. An `error` frame ends the stream early (state machine: Streaming to Error).

### `detail` per stage

| Stage | `detail` keys |
| --- | --- |
| `cache` | `hit`, `tier` (`exact`/`semantic`/null), `similarity`, `threshold`, `norm_query`, `slots{component,symptom}`, `key`, `siis_hash`. Hits add `query`, `matched_query`, `siis_hash_match`, `slots_match`. |
| `enrich` | `canonical_query`, `intents[]` (Intent), `variations[]` (8-10 kept), `dropped_variations[{text,reason,jaccard}]`, `candidates`, `model`, `tokens_in`, `tokens_out` |
| `segment` | `sections[{id,heading,level,sentence_ids,relevance[per intent],relevant}]`, `sentences[{id,section,text,relevance}]`, `relevance_floor`, `intent_titles`. Grey out `relevant: false`. Grounding hover: a step's `src_ids` index `sentences`. |
| `extract` | `topics[]`, `actions[]` (DraftAction, `grounded: false`, includes steps that will be dropped), `model`, tokens |
| `ground` | `threshold`, `proposed_steps`, `kept_steps`, `coverage`, `dropped_steps[{action,text,src_ids,grounding_score,reason,shared_terms}]`, `actions[]` (grounded) |
| `resolve` | `links[{action,screen_path,intent_verb,tier,node_id,entry_id,confidence,candidates[{node_id,path,score}]}]`, `counts` |
| `compile` | `goals`, `actions`, `score_inputs[]`, `score_formula`, `deduped[]`, `dropped_intents[]`, `url_leaks`, `schema_valid`, `repairs`, `dropped_actions` |
| `done` | the full response body: `{contexts, meta}` |
| `error` | `{error}` (type name only) |

## Karur

`resolver_cases.json` lists 12 pre-resolve `DraftAction`s with the expected `LinkDecision`. `must_not_match`
names real catalog entries that look right and are wrong screens (`DL-0022 View Reset Options` is the *auto*
factory reset; five entries are called `View WiFi Settings`, only `DL-0313` is the Wi-Fi screen; the
`View Update Settings` entries are not Software update). `basis` is `design-explicit` when the design doc says
so and `interpretation` when it is our reading of the tiered link table.

Catalog quirk the fixtures respect: all 138 `onURL` (enable) entries carry a full validation object (key,
condition, value); every `offURL`, `onClickURL` and `updateURL` entry is key-only. Only enable toggles can show
"Verified" (the disable twin and every open-screen link can only mean "screen opened"). Copy each entry's own
object unchanged. `draft_actions.json` has `link` filled in with node ids; **node ids are placeholders**
until your Screen Graph build defines them.

## Vishaal

Compiler tests: feed `draft_actions.json` to `compile_goals` and compare to `plan.json`. When `segment.py` exists
it should reproduce the `segment` event's `sentences` from the article: drop everything before the first `#`,
split on `#` headers, split each remaining line into sentences on `[.!?]` followed by whitespace and a capital,
number them `S1..Sn` across the article. If you choose a different rule, change the fixtures, not just the code.

## Illustrative, not measured

Stage latencies, token counts and cost (priced at an assumed $0.10 / $0.40 per million tokens), grounding
scores (derived from term overlap, mapped to 0.75-0.95), retrieval candidate scores, section relevance,
similarity values and node ids. Everything else is real kit data. The dropped step in each `ground` event is a
made-up example of a rejected hallucination, not a step from any article.

## Assumptions baked in (confirm or change)

1. Only `auto` actions carry `actionableDeeplink`. Critical actions ship without a link even when the resolver
   returns one (factory reset resolves to a dummy, the plan shows none).
2. Same-screen actions with **opposite polarity are not merged** (Enable and Disable Touch sensitivity stay two
   actions); merging would emit contradictory steps under one link.
3. Dummy links copy the catalog placeholder: `originalType: "placeholder"`, no validation object, our own
   5-7 word `description` and `message`. `LinkDecision` has no field for that text, so the compiler must write it.
4. The goal string ends with a period (the FAQ regex), the open question with the organisers.
5. Score terms per Goal: `relevance` = mean relevance of the sections its kept steps cite; `grounding_coverage`
   = kept steps / proposed steps; `link_coverage` over `auto` actions only (catalog 1.0, dummy 0.5, none = 1.0).
6. Order = disruption rank, then critical sub-order (restart, safe mode, software update, factory reset), then
   SIIS order, then `data/dependencies.json` edges. External help (rank 4) sits before critical (rank 5).
7. `depends_on` holds prerequisite kinds in the vocabulary of `data/dependencies.json` (`backup`, `safe mode`).
8. `DraftAction.name` is new: the LLM-proposed action name the compiler Title-Cases and dedupes.

## Not covered yet

The slot-guard near-miss block (needs a stored plan with a contradicting symptom, which the real cache will
produce), the degrade paths (rules-only extraction, `no_match` after a full run), and the unseen domains
(battery, camera, performance), which `eval/sets/unseen.jsonl` should hold.

The Docker image does not contain `data/`, so the mock stream only works when the API runs from the repo
(`uvicorn` in `api/`) or `settings.stream_fixtures_dir` points at a copy.
