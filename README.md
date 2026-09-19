# OneClick

Vague complaint in, one-tap fix out. Grounded troubleshooting plans with verified Galaxy Settings deeplinks.
Samsung PRISM GenAI Hackathon 2026, Theme 2: Smart Guided Troubleshooting.

## Quick start

```bash
cp .env.example .env      # add your API keys
docker compose up --build
curl localhost:8000/health
```

## Local development (API)

```bash
cd api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest
```

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | /health | Readiness check |
| POST | /v1/troubleshoot | Complaint + SIIS in, troubleshooting plan out |
| POST | /v1/troubleshoot/stream | Same engine, live stage events (console) |
| POST | /v1/device/apply, /v1/device/validate | Simulated device for the validation loop |
| GET | /v1/metrics, /v1/trace/{id} | Observability |

## Repository layout

| Path | What lives there |
| --- | --- |
| `api/app/pipeline/` | Normalize, slots, enrich, segment, extract, ground, categorize, order, multi-intent |
| `api/app/screengraph/` | Catalog cleaning, Screen Graph nodes, deeplink resolver |
| `api/app/retrieval/` | BM25, dense embeddings, fusion, rerank |
| `api/app/compiler/` | Goal templates, trimmer, URL scrub, final validation |
| `api/app/cache/` | Exact + semantic tiers, slot guard, no-SIIS lookup, SQLite store |
| `api/app/llm/` | Gemini + Mistral router and versioned prompts |
| `api/app/device/` | Device simulator |
| `api/app/obs/` | Logs, metrics, traces |
| `api/scripts/` | Offline builds and `results.jsonl` generation |
| `console/` | Next.js demo console |
| `eval/` | Gate replica, judge, ablation, load tests, test sets |
| `data/` | Kit files, gold labels, slot lexicon, dependency table |
| `docs/` | Architecture, metrics.md, deck, video link |

See `CONTRIBUTING.md` before opening a PR.
