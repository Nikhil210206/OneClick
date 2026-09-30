<p align="center">
  <img src="docs/assets/oneclick-banner.webp" alt="OneClick" width="100%">
</p>

<h3 align="center">Vague complaint in, one-tap fix out.</h3>

<p align="center">
  OneClick turns a customer's loosely worded Galaxy complaint and a Samsung support article into a
  grounded, step-by-step troubleshooting plan, with verified Settings deeplinks that open the right
  screen in one tap.<br>
  <b>Every step comes from the article; nothing is invented.</b>
</p>

<p align="center">
  Samsung PRISM GenAI Hackathon 2026 · Theme 2: Smart Guided Troubleshooting · Team <b>Vibe Coders</b>
</p>

<p align="center">
  <a href="#results">Results</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#features">Features</a> ·
  <a href="#set-up">Set-up</a> ·
  <a href="#try-it-your-first-answer">Try it</a> ·
  <a href="#api">API</a> ·
  <a href="#tests-and-reproducing-the-metrics">Tests</a>
</p>

<div align="center">
  
| Resource | Link |
| --- | --- |
| 🎬 **Demo video** | [Watch on Google Drive](https://drive.google.com/file/d/1GvLDjTpm3jm9S1jTYc0IJ96J-gWKrlMP/view?usp=sharing) |
| 📊 **Presentation** | [docs/SRM_VibeCoders_submission.pdf](docs/SRM_VibeCoders_submission.pdf) |
| 🏗️ **Architecture** | [docs/architecture.md](docs/architecture.md) |
| 📈 **Metrics** | [docs/metrics.md](docs/metrics.md) |
| 🤖 **AI disclosure** | [docs/LangAI3.0_AI_Disclosure - Vibe Coders.pdf](docs/LangAI3.0_AI_Disclosure%20-%20Vibe%20Coders.pdf) |

</div>

---

## Results

Measured on the final engine with free-tier models; full detail in [docs/metrics.md](docs/metrics.md).

| Measure | Target | Result |
| --- | --- | --- |
| Gates G2–G5 (health, coverage, schema, zero URLs) | all pass | **all pass** · 60/60 on our replica of the scorer |
| Schema-valid responses | ≥ 90% | **100%** |
| URL leaks | 0 | **0** |
| Step accuracy (LLM judge, 0–3) | – | **2.65** (kit 2.53 · unseen Battery / Camera / Performance 2.80) |
| Deeplink precision@1 (123 hand-labelled steps) | – | **90.8%** · relevance 1.84 / 2 |
| Repeat query, cache hit | p95 ≤ 300 ms | **17.6 ms** · 100% hits |
| Unseen paraphrase, cache hit | ≥ 80% hits | **94%** · p95 19.3 ms |
| Look-alike complaints wrongly served from cache | ≤ 2% | **0 of 60** |
| Cold query, full pipeline | p95 ≤ 8 s | **6.6 s** |
| Cost per query | tracked | **$0** on free tiers |

---

## How it works

<p align="center">
  <img src="docs/assets/how-it-works.webp" width="100%" alt="OneClick request flow. A complaint and a support article are cleaned up, then checked against the cache: an already-answered request returns a validated plan in about 20 ms. Otherwise the full pipeline runs: an LLM understands the complaint, the article is split into numbered sentences, one LLM picks the sentence ids that fix it while another finds skipped steps and judges whether the article fits the complaint (a wrong article gets an empty plan with the reason), a grounding check drops any step that cannot be traced to a sentence, each step is matched to a Settings deeplink from the catalog (catalog link, placeholder link or manual step), and the plan is ordered, built by rule and checked for schema validity and zero URLs. A request with no article reuses a cached plan, a remembered article, or returns empty. If models are slow, rate-limited or down, or too many cold requests arrive at once, a rules-only answer built from the article's own instructions is returned. The response is always HTTP 200.">
</p>

- **The LLM chooses, code writes.** The model picks article sentences by id from a schema enum, so it
  cannot invent a step. Every graded field (goal, title, description, category, score) is built by rule.
- **Deeplinks are retrieved, never generated.** Hybrid keyword + embedding search over the catalog,
  grouped into Settings screens, picks the exact screen and the right on/off entry, and copies its URI
  unchanged.
- **Repeats and paraphrases come from cache** with no LLM call, guarded so a look-alike complaint
  ("screen is cracked" vs "screen is black") never gets the wrong plan.
- **It never fails.** Every request gets HTTP 200 and a schema-valid body; when something breaks
  inside, the answer degrades (another model, or the article's own instructions) and says why.

Full design, diagrams and decisions: [docs/architecture.md](docs/architecture.md).

---

## Features

### Answers you can trust

- **Grounded by construction.** The model never writes a step. It picks the article's own sentences by
  id from a fixed list, and a grounding check drops any step it cannot trace back to a cited sentence.
  If nothing survives, the answer is empty rather than invented.
- **Verified Settings deeplinks.** Links come from the official catalog through a Screen Graph (577
  entries grouped into 413 screens) and are copied unchanged, together with the catalog's own validation
  link. Where the catalog has no entry (Safe mode, software update...), the step becomes a placeholder
  link or stays manual; a link is never guessed.
- **Complete, well-ordered plans.** A second model call checks which of the article's paragraphs the
  first one skipped and adds them, including the general fixes (restart, Safe mode, update, reset,
  support). Steps are ordered from least to most disruptive, with restarts and resets last.
- **Two problems, two plans.** A complaint that names two separate problems gets one goal for each,
  instead of one muddled plan.

### When the input is wrong or missing

- **Wrong article.** A complaint paired with an article about something else ("wifi keeps
  disconnecting" against a touchscreen article) gets an empty plan with `reason: article_mismatch`, not
  a confident wrong answer. The model's "unrelated" verdict must be confirmed by a deterministic word
  and component check, because neither alone is safe: together they never turned away a kit, unseen or
  paraphrased complaint we measured (0 of 200). Without an API key only the component check can act, so
  detection is weaker.
- **No article.** A missing, `null`, empty or title-only article is never guessed at. OneClick first
  looks for the same question solved before (a cached plan), then for a remembered article that matches
  (the 11 kit articles and every article the API has received), and runs the full pipeline on it.
  Otherwise the answer is empty with `reason: no_article`. All three carry `fallback: no_siis_context`.
- **Hostile or messy input.** URLs and emails are scrubbed from the complaint and the article before any
  model sees them, and from every output. Instructions planted in an article are just text, since the
  model can only return sentence ids. An adversarial set of 20 cases (typos, Hinglish, prompt injection,
  markdown and HTML, three problems in one, an 18,000-character article, empty and nonsense complaints)
  all returned what they should.

### Fast, and never down

- **A cache that understands the complaint.** Exact and semantic matching, with guards on the part, the
  symptom, the intent, the add/remove direction and the article itself, so "add a floating circle" is
  never served "remove it". Typos are corrected before matching ("screne stays blnak" still hits).
- **Never fails, always explains.** Every request gets HTTP 200 with a schema-valid body. If a model is
  slow or rate-limited the next one takes over (Ministral 14B raced against Gemini Flash-Lite, then 8B),
  and with no model at all the article's own instructions are returned. At most 8 cold requests run at
  once; extra ones get that rules-only answer immediately instead of queueing, so cache hits are never
  stuck behind a burst. Every empty or unusual answer carries a `meta.reason` and a plain-language
  `meta.message`.
- **Free and local.** Mistral and Gemini free tiers only ($0 per query); embeddings run on the CPU, so
  there is no paid service anywhere. The engine also runs with no keys at all.
- **See inside every answer.** The streaming endpoint and the site show each pipeline stage as it
  happens, and every request leaves a trace.

---

## Set-up

Pick one path. Both end with the same check: `/health` returns `{"status":"ok"}`.

| | Docker (recommended) | Python, without Docker |
| --- | --- | --- |
| You need | Docker with Compose v2.24 or newer | Python 3.11 or newer (Node 20.9+ only for the site) |
| You get | API on port 8000 and the site on port 3000 | API on port 8000; the site is an extra step |
| First start | a few minutes: the image build | one-off ~130 MB model download, then ~10 seconds |

LLM API keys are optional on both paths: see [Set-up: LLM API keys](#set-up-llm-api-keys-optional-free).

### Set-up with Docker (recommended)

Needs [Docker](https://docs.docker.com/get-docker/) with Compose v2.24 or newer (check with
`docker compose version`) and ports 8000 and 3000 free.

1. **Clone the repository.**

   ```bash
   git clone https://github.com/VishaalPillay/OneClick.git
   cd OneClick
   ```

2. **Optional: add API keys.** Skip this to run without keys.

   ```bash
   cp .env.example .env          # Windows cmd: copy .env.example .env
   ```

   Then open `.env` and fill in the keys (see [Set-up: LLM API keys](#set-up-llm-api-keys-optional-free)).

3. **Build and start.** Leave this terminal running and use a second one for the next steps (add `-d`
   to run in the background).

   ```bash
   docker compose up --build
   ```

   The first build takes a few minutes: it installs dependencies, bakes in the embedding model and builds
   the search indexes. Later starts take seconds.

4. **Check that it is ready.** It answers `503` while loading, then `200` with the body below.

   ```bash
   curl http://localhost:8000/health        # Windows PowerShell: curl.exe http://localhost:8000/health
   # {"status":"ok"}
   ```

5. **Open the services.**

   | Service | URL |
   | --- | --- |
   | API | <http://localhost:8000> |
   | Site (walkthrough + live demo) | <http://localhost:3000> |

Then send your first request: [Try it](#try-it-your-first-answer).

**Useful variations**

- API only, no site: `docker compose up --build api`.
- Port 3000 is taken: `ONECLICK_CONSOLE_PORT=3100 docker compose up --build`
  (PowerShell: `$env:ONECLICK_CONSOLE_PORT=3100; docker compose up --build`).
- Stop everything: `docker compose down`. This also throws the cache away, so the next start is fully
  cold, which is what you want before measuring latency.
- Changed the keys after starting? Run `docker compose up -d` again so the container picks them up.

**If something goes wrong**

| Symptom | Fix |
| --- | --- |
| `docker compose` is unknown, or complains about `required` in `env_file` | Docker Compose is older than v2.24: update Docker |
| `port is already allocated` | Free port 8000, or move the site with `ONECLICK_CONSOLE_PORT` (the API needs 8000) |
| `/health` keeps answering `503` | It is still loading; check `docker compose logs api` |
| The site badge reads *Engine offline* | The API is not up yet, or port 8000 is blocked: the page calls it from your browser at `http://127.0.0.1:8000` |

### Set-up: LLM API keys (optional, free)

| Key | Used for | Get one (free, no card) |
| --- | --- | --- |
| `MISTRAL_API_KEY` | Writing the plan (Ministral 14B and 8B) | [console.mistral.ai](https://console.mistral.ai) → API Keys |
| `GEMINI_API_KEY` | Query variations; backup model | [aistudio.google.com](https://aistudio.google.com/apikey) |

Put them in a file named `.env` in the repository root (one `NAME=value` per line, no quotes). Both
the Docker and the Python path read it from there.

**Without keys the engine still runs:** every answer is built from the article's own instructions without
a language model. Plans stay grounded and schema-valid but are less complete, and `meta.reason` says
`rules_only`. The best results need at least the Mistral key. To confirm your keys are in use, send a
request and look at `meta.model`: it reads `ministral-14b-latest` (or similar) instead of `null`.

Free tiers are rate-limited, so when running many requests in a row the engine may step down to a smaller
model or to the rules-only answer; that is expected and still valid.

### Set-up without Docker (Python)

Needs **Python 3.11 or newer**. Run these from the repository root:

```bash
git clone https://github.com/VishaalPillay/OneClick.git
cd OneClick
python -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd api
uvicorn app.main:app --port 8000
```

- On Windows, if PowerShell refuses to run `Activate.ps1`, skip activation and use the environment's own
  Python instead: `.venv\Scripts\python -m pip install -r requirements.txt`, then from `api/`:
  `..\.venv\Scripts\python -m uvicorn app.main:app --port 8000`. On Linux or macOS use `python3` if
  `python` is not found.
- Start the server from the `api/` folder. The `.env` in the repository root is still found.
- The first start downloads the embedding model (about 130 MB, once) and builds the search indexes in
  memory; `/health` turns `ok` when it is done, about 10 seconds after the download.
- Keep port 8000: the site calls the API there.
- A `cache.sqlite` file appears in `api/`. Delete it to start from an empty cache.

Check it from a second terminal with `curl http://localhost:8000/health` (PowerShell: `curl.exe`), then go
to [Try it](#try-it-your-first-answer).

### Set-up: the site without Docker (optional)

With Docker the site is already running on port 3000. Without Docker it needs **Node 20.9 or newer**, in a
second terminal, while the API from the step above keeps running:

```bash
cd console
npm install
npm run dev                          # http://localhost:3000
```

The page calls the API from your browser at `http://127.0.0.1:8000`. To point it somewhere else, set
`NEXT_PUBLIC_API_URL` before `npm run dev` (with Docker, `ONECLICK_PUBLIC_API_URL` before
`docker compose up --build`).

---

## Try it: your first answer

Send a complaint with its support article. A ready-made request is in the repo; run the command from the
repository root:

```bash
curl -X POST http://localhost:8000/v1/troubleshoot \
  -H "Content-Type: application/json" \
  -d @data/fixtures/touch_lag/request.json
```

On Windows PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/troubleshoot `
  -ContentType "application/json" -InFile data/fixtures/touch_lag/request.json | ConvertTo-Json -Depth 12
```

The request body is `{"query": "...", "siis_response": {"title": "...", "content": "..."}}`; the article
may also be a plain string, or omitted. The answer (abridged, from a run with a Mistral key; without keys
the wording differs and `meta.reason` is `rules_only`):

```json
{
  "contexts": [
    {
      "goal": "Follow these steps to perform this Touchscreen Responsiveness Troubleshooting.",
      "title": "Touchscreen lag issues",
      "score": 0.86,
      "actions": [
        {
          "actionName": "Enable Touch Sensitivity",
          "description": "It will enhance touch responsiveness",
          "category": "auto",
          "stepGroups": [
            {
              "steps": ["Go to Settings.", "Tap Display.", "Tap the switch next to Touch sensitivity."],
              "actionableDeeplink": { "deeplink": "bixby://masked/act/14eb42b895", "message": "Enable Touch sensitivity", "…": "…" },
              "validationDeeplink": { "deeplink": "bixby://masked/val/6451858b28", "key": "Touch sensitivity", "…": "…" }
            }
          ]
        }
      ]
    }
  ],
  "meta": { "latency_ms": 5150.9, "cache_hit": false, "model": "ministral-14b-latest", "fallback": null, "…": "…" }
}
```

Send the same request again: the second answer comes from the cache in milliseconds (`meta.cache_hit` is
`true`, and so is the `X-Cache-Hit` response header). Try a complaint with no `siis_response` to see the
no-article path, or one paired with an unrelated article to see the mismatch answer.

---

## Test it in the browser

The site walks through one real engine run, then hands over to **Try it live**, which sends your own
complaint to the running API and streams every stage as it happens. It needs the site to be set up (with
Docker it already is; otherwise see [Set-up: the site without Docker](#set-up-the-site-without-docker-optional)).

1. Open <http://localhost:3000> and click **Try it live** at the top right (or scroll to section 08).
   The badge should read *Engine ready*. It shows *Engine waking up* while the API loads its indexes,
   and *Engine offline* if the API is not running.
2. The showcase complaint and its article are already filled in. Pick another case under **Or pick one
   of ours** (*Ask it again* for a cache hit, *Reworded* for a paraphrase, *Cracked screen* for a
   look-alike complaint that must not reuse the plan, *Two problems*, *No article*, *Wrong article*,
   *Prompt injection*), or write your own: a complaint in box 1 and the support article in box 2, as
   plain text or as `{"title": "...", "content": "..."}`.
3. Click **Find the fix**. The stages stream in live, then the plan appears with its steps and
   deeplinks. An empty answer shows why (for example, an article that does not match the complaint).
4. Click it again: the same request now comes back from the cache in milliseconds.

Live runs use your LLM keys like any other request; without keys the answers come from the rules-only
path.

---

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/troubleshoot` | Complaint + article → plan. The scored endpoint |
| `POST` | `/v1/troubleshoot/stream` | Same engine, one Server-Sent Event per stage |
| `GET` | `/health` | `{"status":"ok"}` once loaded; `503` before |
| `GET` | `/v1/metrics` · `/v1/traces` · `/v1/trace/{id}` | Latency and cache counters; recent request traces |

No authentication. Responses follow the organisers' `ContextDeeplinkResponse` schema, plus a `meta`
block (latency, cache tier, model, cost, and a `reason` / `message` whenever a plan is empty or
unusual). The same values are sent as `X-Latency-Ms`, `X-Cache-Hit`, `X-Cache-Tier` and `X-Cost-Usd`
headers. Details: [architecture §8](docs/architecture.md#8-api-contract).

---

## Tests and reproducing the metrics

Set-up first (Python path, from the repository root with the virtual environment active):

```bash
# unit and integration tests (no keys needed, no LLM quota spent)
cd api && pytest && cd ..
pytest eval/tests
python eval/sets/validate_sets.py
```

With the API running on port 8000 and keys in `.env` (the step-accuracy judge uses `GEMINI_API_KEY`):

```bash
cd api && python scripts/make_results.py --pause 8 && cd ..   # results.jsonl: 20 kit queries, each cold
python eval/gate_replica.py --api http://localhost:8000 --results results.jsonl   # gates G2–G5, blocks A1–A5
python eval/judge.py                                          # step accuracy on the kit plans
python eval/judge.py --api http://localhost:8000 --sets unseen --out eval/results/judge_unseen.json
python eval/loadtest.py --mode api --api http://localhost:8000   # latency, cache hit rates
python eval/report.py                                         # regenerates docs/metrics.md
```

The first command rewrites the committed `results.jsonl`; `git checkout results.jsonl` restores it.

Start the API on an empty cache before measuring, so first calls are genuinely cold: with Docker,
`docker compose down` then `docker compose up`; without Docker, delete any `cache.sqlite` in the folder
you start it from.

---

## Submission

| Item | Where |
| --- | --- |
| Source code | This repository, tag `PRISM_GENAI_HACKATHON_Y2026` |
| Set-up | This README ([Docker](#set-up-with-docker-recommended) or [Python](#set-up-without-docker-python)), `requirements.txt`, `docker-compose.yml`, `api/Dockerfile` |
| Results file | [`results.jsonl`](results.jsonl): the 20 kit queries with 8–10 variations each |
| Demo video | [Google Drive](https://drive.google.com/file/d/1GvLDjTpm3jm9S1jTYc0IJ96J-gWKrlMP/view?usp=sharing) |
| Presentation | [docs/SRM_VibeCoders_submission.pdf](docs/SRM_VibeCoders_submission.pdf) |
| AI disclosure | [docs/LangAI3.0_AI_Disclosure - Vibe Coders.pdf](docs/LangAI3.0_AI_Disclosure%20-%20Vibe%20Coders.pdf) |
| APK / SDK | Not applicable: OneClick is a web API |

## License

[MIT](LICENSE)
