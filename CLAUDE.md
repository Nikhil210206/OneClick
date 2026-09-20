# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository. Shared by the whole team — keep it accurate, and update it in the same PR that changes the behaviour it describes.

## What this is

OneClick is a Smart Guided Troubleshooting engine for the Samsung PRISM GenAI Hackathon 2026 (Theme 2). A vague user complaint plus a SIIS knowledge article go in; a grounded, schema-valid troubleshooting plan with verified Galaxy Settings deeplinks comes out.

The repo is currently a **skeleton**: package layout, shared contracts and docstrings are in place, and almost every function body is `raise NotImplementedError`. Each stub's docstring states which design component it implements (C1–C12) — treat it as the spec for that file and implement against it rather than inventing a new design.

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

# Full stack
cp .env.example .env                                 # GEMINI_API_KEY, MISTRAL_API_KEY
docker compose up --build && curl localhost:8000/health

# Offline builds (run from api/, in this order, before the API can resolve links)
python scripts/build_screengraph.py   # clean catalog -> data/build/screengraph.json
python scripts/build_index.py         # BM25 + vector index over Screen Graph nodes
python scripts/build_lookup.py        # pre-warmed no-SIIS lookup table
python scripts/make_results.py        # cold run over data/kit -> results.jsonl

# Evaluation (run from repo root, against a running API)
python eval/gate_replica.py   # G2-G5 + A1-A5, each query twice on an empty cache
python eval/judge.py          # step accuracy 0-3, deeplink relevance 0-2
python eval/loadtest.py       # p50/p95 for repeat-hit, paraphrase-hit, cold paths
python eval/report.py         # regenerates docs/metrics.md

# Console stream: replays data/fixtures while the pipeline is a skeleton (run from repo root)
curl -N -X POST localhost:8000/v1/troubleshoot/stream -H "Content-Type: application/json" \
     -d @data/fixtures/touch_lag/request.json          # ?mock=exact | ?mock=semantic for cache hits

# Console (Next.js, not bootstrapped yet — see console/README.md)
cd console && npm run dev     # needs NEXT_PUBLIC_API_URL
```

`test_all_modules_import` in [api/tests/test_api.py](api/tests/test_api.py) walks every package: a syntax error or bad import anywhere in `app/` fails the suite, even in code nobody calls yet.

## Architecture

One request through [api/app/pipeline/run.py](api/app/pipeline/run.py):

```
normalize -> cache lookup -> enrich (LLM A) -> segment -> extract (LLM B)
          -> ground -> resolve -> categorize -> order -> multi-intent
          -> compile -> validate -> cache write
```

- **normalize / slots** — whitespace and numbering fixes, URL/email scrub of the SIIS text *before any LLM sees it*, `siis_hash`; slots come from `data/slot_lexicon.json`, never from an LLM.
- **cache** — Tier 0 exact (`norm_query + siis_hash`), Tier 1 semantic (ANN over each solved plan's original query *and* its 8–10 variations). A hit requires similarity ≥ τ **and** compatible slots **and** a matching SIIS hash. The SIIS cache ships empty; only the no-SIIS lookup table is pre-warmed.
- **enrich** — canonical query, 1–3 intents, domain, 2–3 word title, 12 candidate variations filtered down to 8–10 (drop token Jaccard ≥ 0.6, drop embedding cosine < 0.6).
- **segment / extract** — SIIS split into sections and numbered sentences `S1…Sn`; the LLM returns actions whose every step cites sentence ids.
- **ground** — a step survives only if it clears the embedding threshold against its cited sentences **and** shares a content term with them. Failing steps are dropped; actions left empty are dropped.
- **resolve** — `screen_path + verb` → BM25 + dense → RRF → rerank → Screen Graph node → entry chosen by polarity. Tiered outcome: catalog link, `bixby://dummy_positive`, or manual.
- **compile / validate** — builds the official `ContextDeeplinkResponse`, then URL scrub → schema validation → one repair cycle → drop the offending action.

The endpoints are listed in [README.md](README.md); the full design lives in `docs/Smart Guided Troubleshooting Engine - Architecture & System Design.pdf` and should be exported into [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Hard rules

1. **Never edit [api/app/schema.py](api/app/schema.py).** It is the organisers' official file; ruff is configured to skip it. Every response must validate against it.
2. **Never invent a step.** Every step traces back to SIIS sentence ids. If nothing survives grounding, return empty `contexts` with `fallback: "no_match"` — an empty answer beats a fabricated one.
3. **Zero URLs in output.** No `http(s)`, `www.`, bare domains, emails or markdown links in any string. The catalog `bixby://` URIs in deeplink fields are the only exception. Run the scrub on cache hits too.
4. **Always 200 from `/v1/troubleshoot`,** with a schema-valid body, whatever fails internally. Degrade, never error.
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
- LLM calls go through [api/app/llm/router.py](api/app/llm/router.py) (Gemini primary, Mistral fallback on a 3 s timeout, temperature 0), never a client directly. Prompts are versioned files in `api/app/llm/prompts/` — add a new `*.v2.md` rather than mutating a shipped version, since the prompt version is part of the cache key.

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
- The docker build context is `./api`, so repo-root `data/` is **not** in the image today; the deploy story (ADR-007 bakes weights, Screen Graph, indexes and the lookup table in) still needs wiring.
- `/health` currently returns 200 unconditionally; per ADR-007 it must return 503 until the Screen Graph, indexes, lookup table and LLM clients are loaded.
- `cache.sqlite` (default `sqlite_path`) is written into whatever directory the API is started from and is not gitignored — do not commit it.
- README links a `CONTRIBUTING.md` that does not exist yet.
- `results.jsonl` at the repo root is generated by `scripts/make_results.py`; it is the submission artefact, not a scratch file.
- `POST /v1/troubleshoot/stream` replays [data/fixtures/](data/fixtures/README.md) while `settings.stream_mock` is `True`. Mock frames are marked (`X-Mock: true`, `detail.mock`). Once `pipeline.run.run_stream` is implemented, set `stream_mock = False`. `data/` is not in the Docker image, so the mock only works when the API runs from the repo.
- `data/fixtures/` is a contract for Karur and Nikhil, guarded by [api/tests/test_fixtures.py](api/tests/test_fixtures.py): official schema, zero URLs, verbatim catalog links, every step traced to a real article sentence, the score formula. If you change a fixture, keep those tests green and tell the other lanes.
- Catalog quirk the fixtures and the simulator must respect: all 138 `onURL` (enable) entries carry a full validation object (key, condition, value) and every `offURL`, `onClickURL` and `updateURL` entry is key-only. So the design's "138 fully validatable entries" are exactly the enable toggles, and only those can show "Verified". `DL-0022 View Reset Options` is the *auto* factory reset, not Factory data reset.

## Open questions with the organisers

Answers may change compiler behaviour, so check before "fixing" these: does the goal string end with a period (currently following the FAQ regex); is an extra `meta` key allowed in the body (behind `settings.include_meta`); do service-centre steps come before or after critical actions (critical last for now).
