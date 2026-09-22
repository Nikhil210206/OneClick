/**
 * Build-time reads for the story page: the LLM prompt files and the eval sets.
 *
 * Server only — it touches the filesystem. `next build` runs from `console/`, so the repo root
 * is one level up. Every value here is read or counted from a file in the repo; the two that
 * need Python to recompute are constants with the command that produced them.
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import type { CatalogEntry } from "@/lib/checks";
import type { MultiIntent, Preset, PromptFile, Proof, StoryInputs } from "@/lib/story";

// The page is prerendered, so these reads only ever run during `next build`. The ignore keeps
// the bundler from tracing the whole repo into the server output on account of them.
const REPO = path.join(/* turbopackIgnore: true */ process.cwd(), "..");
const PROMPTS = path.join(REPO, "api/app/llm/prompts");

/**
 * The newest version of a prompt, e.g. `extract.v2.md` over `extract.v1.md`.
 *
 * Prompts are versioned rather than edited in place (the version is part of the cache key), so
 * the page always shows whatever the engine would send today. A file that is still the
 * `TODO(A)` stub comes back with `body: null` and the page says so instead of inventing one.
 */
function latestPrompt(stem: "enrich" | "extract"): PromptFile {
  const pattern = new RegExp(`^${stem}\\.v(\\d+)\\.md$`);
  const versions = existsSync(PROMPTS)
    ? readdirSync(PROMPTS)
        .map((f) => ({ f, v: Number(pattern.exec(f)?.[1] ?? NaN) }))
        .filter((x) => Number.isFinite(x.v))
        .sort((a, b) => b.v - a.v)
    : [];
  const file = versions[0]?.f ?? `${stem}.v1.md`;
  const raw = versions[0] ? readFileSync(path.join(PROMPTS, file), "utf8") : "";

  // Drop the title line; what is left is the instruction text.
  const body = raw
    .split("\n")
    .filter((line, i) => !(i === 0 && line.startsWith("#")))
    .join("\n")
    .trim();
  const stub = body === "" || /^TODO\b/.test(body);
  return { file, body: stub ? null : body };
}

function jsonl(rel: string): Record<string, unknown>[] {
  const p = path.join(REPO, rel);
  if (!existsSync(p)) return [];
  return readFileSync(p, "utf8")
    .split("\n")
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l) as Record<string, unknown>);
}

function json<T>(rel: string): T | null {
  const p = path.join(REPO, rel);
  return existsSync(p) ? (JSON.parse(readFileSync(p, "utf8")) as T) : null;
}

function readCatalog(): CatalogEntry[] {
  return json<{ deeplinks: CatalogEntry[] }>("data/kit/deeplinks.json")?.deeplinks ?? [];
}

function readProof(catalog: CatalogEntry[]): Proof {
  const gold = jsonl("data/gold/deeplink_gold.jsonl");
  const tier = (t: string) => gold.filter((g) => g.tier === t).length;
  const owners: Record<string, number> = {};
  for (const g of gold) owners[String(g.owner)] = (owners[String(g.owner)] ?? 0) + 1;

  const nearMiss = jsonl("eval/sets/near_miss.jsonl");

  return {
    sets: {
      paraphrases: jsonl("eval/sets/paraphrases.jsonl").length,
      nearMiss: nearMiss.length,
      unseen: jsonl("eval/sets/unseen.jsonl").length,
      adversarial: jsonl("eval/sets/adversarial.jsonl").length,
    },
    gold: {
      labelled: gold.length,
      target: 100,
      catalog: tier("catalog"),
      dummy: tier("dummy"),
      manual: tier("manual"),
      owners,
    },
    catalog: {
      entries: catalog.length,
      // Only enable toggles carry a full validation object (key, condition, value).
      verifiable: catalog.filter((d) => d.originalType === "onURL").length,
    },
    // `python eval/sets/validate_sets.py` — "mean overlap with the row query".
    overlap: { paraphrase: 0.3, nearMiss: 0.49 },
    // The resolver's precision@1 and wrong-link rate on the mapping lane's 63 catalog-tier gold
    // steps (docs/MAPPING_CACHE.md on main, 2026-09-21). bm25Top1 is eval/evalkit/bm25.py on the
    // same 63 steps, exact entry only (recomputed 2026-09-22). These labels were written by the
    // lane that built the resolver; the independent check on the eval lane's 24 is still pending.
    resolver: { n: 63, top1: 59, bm25Top1: 47, wrongLink: 1 },
    // docs/MAPPING_CACHE.md: repeat and paraphrase hit rates at a 0.80 similarity threshold.
    cache: { repeatHit: 1, repeatMs: 0.6, paraphraseHit: 0.9, paraphraseMs: 20, falseHits: 0 },
    // The touch-lag row's near misses: the same article, a different problem.
    nearMisses: nearMiss
      .filter((n) => n.row_id === "row_21")
      .map((n) => ({
        query: n.query as string,
        differsIn: n.differs_in as string,
        slots: n.expected_slots as Record<string, string | null>,
      })),
  };
}

interface Fixture {
  request: { query: string; siis_response: { title?: string } | null };
  plan: { contexts: { title: string; score: number; actions: unknown[] }[] };
  stream: { stage: string; detail?: Record<string, unknown> }[];
}

function fixture(dir: string): Fixture {
  const base = `data/fixtures/${dir}`;
  return {
    request: json(`${base}/request.json`)!,
    plan: json(`${base}/plan.json`)!,
    stream: json(`${base}/stream.json`)!,
  };
}

function readMultiIntent(): MultiIntent {
  const f = fixture("touch_multi_intent");
  const titles = f.plan.contexts.map((c) => c.title);
  const compile = f.stream.find((e) => e.stage === "compile")?.detail ?? {};
  const deduped = (compile.deduped as { action: string; kept_in_intent: number; removed_from_intents: number[] }[]) ?? [];
  return {
    query: f.request.query,
    goals: f.plan.contexts.map((c) => ({ title: c.title, score: c.score, actions: c.actions.length })),
    deduped: deduped.map((d) => ({
      action: d.action,
      keptIn: titles[d.kept_in_intent] ?? "",
      removedFrom: d.removed_from_intents.map((i) => titles[i] ?? ""),
    })),
  };
}

/**
 * The live section's presets. Every one is a real request: the recorded fixtures, or a row from
 * the held-out eval sets, which the engine is never tuned on.
 */
function readPresets(): Preset[] {
  const touch = fixture("touch_lag").request;
  const multi = fixture("touch_multi_intent").request;
  const email = fixture("email_not_responding").request;
  const titleOf = (siis: unknown) =>
    siis && typeof siis === "object" && "title" in siis ? String((siis as { title: string }).title) : "no article";
  const row = (set: string, id: string) => jsonl(`eval/sets/${set}.jsonl`).find((r) => r.id === id);
  const firstOf = (set: string, key: string, value: string) =>
    jsonl(`eval/sets/${set}.jsonl`).find((r) => r[key] === value);

  const reworded = row("paraphrases", "para_21_02");
  const nearMiss = row("near_miss", "nm_21_1");
  const unseen = firstOf("unseen", "domain", "Battery");
  const injection = row("adversarial", "adv_08");
  const offTopic = row("adversarial", "adv_12");

  const make = (
    id: string,
    label: string,
    hint: string,
    req: { query?: unknown; siis_response?: unknown } | undefined,
    siis: unknown = req?.siis_response,
    mock?: Preset["mock"],
  ): Preset | null =>
    req ? { id, label, hint, query: String(req.query), siis, articleTitle: titleOf(siis), mock } : null;

  const presets = [
    make("cold", "The complaint", "full cold run", touch),
    make("exact", "Ask it again", "exact cache hit", touch, touch.siis_response, "exact"),
    make("semantic", "Reworded", "held-out paraphrase", reworded, touch.siis_response, "semantic"),
    make("near", "Cracked screen", "near miss, must not reuse", nearMiss, touch.siis_response),
    make("multi", "Two problems", "multi-intent", multi),
    make("email", "Mismatched article", "all three link tiers", email),
    make("unseen", "Battery drain", "unseen domain", unseen),
    make("inject", "Prompt injection", "instructions inside the article", injection),
    make("offtopic", "Wrong article", "must return no_match", offTopic),
  ];
  return presets.filter((p): p is Preset => Boolean(p));
}

export function readStoryInputs(): StoryInputs {
  const catalog = readCatalog();
  return {
    prompts: { enrich: latestPrompt("enrich"), extract: latestPrompt("extract") },
    proof: readProof(catalog),
    catalog,
    multiIntent: readMultiIntent(),
    presets: readPresets(),
  };
}
