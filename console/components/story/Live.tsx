"use client";

import { useEffect, useRef, useState } from "react";
import { Galaxy } from "@/components/story/Galaxy";
import { Mark } from "@/components/story/Logo";
import { gsap, useGSAP } from "@/lib/gsap";
import type { PlanContext } from "@/lib/plan";
import type { Preset, StoryData } from "@/lib/story";
import { API_URL, checkHealth, type Health, streamTroubleshoot } from "@/lib/stream";
import { LLM_STAGES, type StageEvent } from "@/lib/trace";

/**
 * Try it live: the one part of the page that is not a recording.
 *
 * It streams POST /v1/troubleshoot/stream and renders every stage frame as it arrives, then puts
 * the real answer on the phone. The badge names the model that answered (`meta.model`: a Ministral
 * model, or `rules` when the engine ran without an LLM) or the cache tier that did. If the API is
 * switched to its mock replay (`settings.stream_mock`), every frame carries `detail.mock` and the
 * section says so rather than passing a replay off as a live run.
 */

type Frame = StageEvent<Record<string, unknown>>;
type Status = "idle" | "running" | "done" | "offline" | "timeout";

// A hosted API may be asleep or still loading its indexes: /health wakes it and says when it can
// answer. Poll until it is ready, then stop; give up after a few minutes of nothing.
const HEALTH_POLL_MS = 4000;
// A cold answer takes up to ~8 s. Past SLOW_S the page says it is still waiting; past GIVE_UP_S it stops.
const SLOW_S = 10;
const GIVE_UP_S = 45;
const HEALTH_GIVE_UP_MS = 180_000;
const LOCAL_API = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(API_URL);

/** A preset's article as the two form fields; no article is two empty fields. */
function articleFields(siis: unknown): { title: string; text: string } {
  if (typeof siis === "string") return { title: "", text: siis };
  if (siis && typeof siis === "object") {
    const o = siis as { title?: unknown; content?: unknown };
    return { title: typeof o.title === "string" ? o.title : "", text: typeof o.content === "string" ? o.content : "" };
  }
  return { title: "", text: "" };
}

/** A complaint as the kit writes it, reduced for matching: numbering, quotes, case and spacing. */
const bare = (q: string) =>
  q
    .toLowerCase()
    .replace(/(^|\s)\d+[.)]\s+/g, " ")
    .replace(/["“”']/g, "")
    .replace(/\s+/g, " ")
    .trim();

interface ReadArticle {
  siis: unknown;
  /** How the box was read, shown under it. */
  note: string;
  /** A complaint that came with the paste (a kit entry's original_query), if any. */
  query?: string;
  bad?: boolean;
}

/**
 * The article the engine gets from the form, and how it was read. Empty is no article. JSON is
 * taken in every shape the evaluation kit uses: one article ({"title", "content"}), one entry of
 * siis_responses.json ({"id", "original_query", "siis_response"}), a whole request, or the whole
 * file, from which the entry whose original_query is the complaint is used. Anything else is the
 * article's text under the title typed above it.
 */
function readArticle(title: string, text: string, complaint: string): ReadArticle {
  const body = text.trim();
  if (!body && !title.trim()) return { siis: null, note: "empty: no article" };
  if (body.startsWith("{") || body.startsWith('"')) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(body);
    } catch {
      parsed = undefined;
    }
    if (typeof parsed === "string") return { siis: { title: title.trim(), content: parsed }, note: "read as text" };
    if (parsed && typeof parsed === "object") {
      const o = parsed as Record<string, unknown>;
      if (Array.isArray(o.responses)) {
        const rows = o.responses as { id?: string; original_query?: string; siis_response?: unknown }[];
        const want = bare(complaint);
        const row = rows.find((r) => bare(r.original_query ?? "") === want);
        return row
          ? { siis: row.siis_response, note: `kit file: using ${row.id ?? "the matching row"}` }
          : {
              siis: null,
              note: "kit file: no row has this complaint; paste one row or use its exact words",
              bad: true,
            };
      }
      if ("siis_response" in o) {
        const q = typeof o.original_query === "string" ? o.original_query : typeof o.query === "string" ? o.query : undefined;
        return { siis: o.siis_response, note: `kit entry${o.id ? ` ${o.id}` : ""}: its article`, query: q };
      }
      if ("content" in o || "title" in o) return { siis: o, note: "kit JSON: one article" };
      return { siis: o, note: "JSON without title or content: sent as it is", bad: true };
    }
  }
  // Text copied out of the JSON file keeps its escapes; turn them back into line breaks.
  const plain = !text.includes("\n") && text.includes("\\n") ? text.replace(/\\n/g, "\n") : text;
  return { siis: { title: title.trim(), content: plain }, note: "read as text" };
}

/** The plan's categories as the rest of the site names them. */
const CATEGORY: Record<string, string> = { auto: "Auto", manual: "Manual", critical: "Critical" };

const fmt = (ms: number) => (ms >= 100 ? Math.round(ms).toLocaleString("en-US") : ms.toFixed(1));

const str = (v: unknown) => (typeof v === "string" && v.trim() ? v : null);
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** The one line under a stage worth reading live: what the cache matched, how the model worked. */
function stageNote(f: Frame): string | null {
  const d = f.detail ?? {};
  if (f.stage === "cache" && d.hit === true) {
    const matched = str(d.matched_query);
    const sim = num(d.similarity);
    const thr = num(d.threshold);
    if (d.tier === "exact" || !matched) return matched ? `same request as “${matched}”` : null;
    return `matched “${matched}”` + (sim !== null && thr !== null ? ` · similarity ${sim.toFixed(2)} ≥ ${thr.toFixed(2)}` : "");
  }
  if (f.stage === "enrich" && d.variations_pending === true) {
    return "8–10 rewordings are being written in the background; the answer does not wait for them";
  }
  if (f.stage === "extract") {
    const model = str(d.model);
    const intents = Array.isArray(d.intents)
      ? d.intents.map((i) => str((i as { title?: unknown })?.title)).filter(Boolean)
      : [];
    const parts = [
      d.mode === "select" ? "picked article sentences by id, wrote none" : null,
      model,
      intents.length ? `${intents.length === 1 ? "problem" : "problems"}: ${intents.join(" · ")}` : null,
      d.source === "rules" ? "no model answered: the article's own instructions" : null,
    ];
    return parts.filter(Boolean).join(" · ") || null;
  }
  return null;
}

export function Live({ data }: { data: StoryData }) {
  const root = useRef<HTMLElement>(null);
  const abort = useRef<AbortController | null>(null);
  const [preset, setPreset] = useState<Preset>(data.presets[0]);
  const [query, setQuery] = useState(data.presets[0].query);
  const [title, setTitle] = useState(articleFields(data.presets[0].siis).title);
  const [text, setText] = useState(articleFields(data.presets[0].siis).text);
  const [frames, setFrames] = useState<Frame[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [health, setHealth] = useState<Health>("unknown");
  const trace = useRef<HTMLOListElement>(null);

  // The trace has a fixed height on a desktop; keep the newest stage in view as frames arrive.
  useEffect(() => {
    const list = trace.current;
    if (list) list.scrollTo({ top: list.scrollHeight, behavior: "smooth" });
  }, [frames.length]);

  useEffect(() => {
    const ctrl = new AbortController();
    const started = Date.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      const h = await checkHealth(ctrl.signal);
      if (ctrl.signal.aborted) return;
      setHealth(h);
      if (h !== "ready" && Date.now() - started < HEALTH_GIVE_UP_MS) timer = setTimeout(poll, HEALTH_POLL_MS);
    };
    void poll();
    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, []);

  // The form still holds the chosen preset exactly: only then is it that preset (and its mock hint).
  const fields = articleFields(preset.siis);
  const asPreset = query === preset.query && title === fields.title && text === fields.text;

  const run = async (q: string, siis: unknown, mockHint?: Preset["mock"]) => {
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    setFrames([]);
    setStatus("running");
    setElapsed(0);
    // Count the wait, and give up rather than spin for ever when the engine never answers.
    let seconds = 0;
    let timedOut = false;
    const tick = setInterval(() => setElapsed(++seconds), 1000);
    const limit = setTimeout(() => {
      timedOut = true;
      ctrl.abort();
    }, GIVE_UP_S * 1000);
    try {
      await streamTroubleshoot({ query: q, siis_response: siis }, (ev) => setFrames((f) => [...f, ev]), {
        mock: mockHint,
        signal: ctrl.signal,
      });
      if (!ctrl.signal.aborted) {
        setStatus("done");
        setHealth("ready");
      }
    } catch {
      if (timedOut) setStatus("timeout");
      else if (!ctrl.signal.aborted) setStatus("offline");
    } finally {
      clearInterval(tick);
      clearTimeout(limit);
    }
  };

  const read = readArticle(title, text, query);
  const submit = () =>
    void run(query, asPreset ? preset.siis : read.siis, asPreset ? preset.mock : undefined);

  // A pasted kit entry brings its own complaint: take it when box 1 is empty or still the preset's words.
  const onArticle = (value: string) => {
    setText(value);
    const q = readArticle(title, value, query).query;
    if (q && (!query.trim() || query === preset.query)) setQuery(q);
  };

  const pick = (p: Preset) => {
    const f = articleFields(p.siis);
    setPreset(p);
    setQuery(p.query);
    setTitle(f.title);
    setText(f.text);
    void run(p.query, p.siis, p.mock);
  };

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const $ = gsap.utils.selector(root);
        gsap
          .timeline({
            defaults: { ease: "expo.out" },
            scrollTrigger: { trigger: root.current, start: "top 70%" },
          })
          .from($(".lv-card"), { y: 120, scale: 0.96, autoAlpha: 0, duration: 1.3 })
          .from($(".lv-left > *"), { y: 40, autoAlpha: 0, stagger: 0.08, duration: 1 }, 0.3)
          .from($(".lv-phone"), { y: 160, rotate: 6, autoAlpha: 0, duration: 1.4 }, 0.35);
      });
    },
    { scope: root },
  );

  const done = frames.find((f) => f.stage === "done");
  const mock = frames.some((f) => f.detail?.mock === true);
  const contexts = (done?.detail?.contexts as PlanContext[] | undefined) ?? [];
  const meta =
    (done?.detail?.meta as {
      fallback?: string | null;
      trace_id?: string;
      latency_ms?: number;
      model?: string | null;
      cache_tier?: string | null;
      source?: string | null;
    }) ?? {};
  const noScenario = mock && meta.trace_id === "t_mock_none";
  const stages = frames.filter((f) => f.stage !== "done");

  const badge =
    status === "offline"
      ? { cls: "off", text: "Engine offline" }
      : status === "timeout"
        ? { cls: "off", text: `No answer in ${GIVE_UP_S} s` }
        : status === "running"
          ? {
              cls: elapsed >= SLOW_S ? "mock" : "ready",
              text:
                elapsed >= SLOW_S ? `Still waiting for the engine… ${elapsed} s` : `Engine working… ${elapsed} s`,
            }
      : mock
        ? { cls: "mock", text: "Mock replay of a recorded run" }
        : status === "done"
          ? { cls: "live", text: `Live engine · ${answeredBy(meta)}` }
          : health === "ready"
            ? { cls: "ready", text: `Engine ready · ${API_URL.replace(/^https?:\/\//, "")}` }
            : health === "starting"
              ? { cls: "mock", text: "Engine waking up…" }
              : health === "offline"
                ? { cls: "off", text: "Engine offline" }
                : { cls: "idle", text: API_URL.replace(/^https?:\/\//, "") };

  return (
    <section className="lv fit" id="live" data-nav="light" ref={root}>
      <div className="lv-inner">
        <div className="lv-card">
          <div className="lv-left">
            <p className="st-eyebrow">08 · Try it live</p>
            <h2 className="st-h2">Ask it anything.</h2>
            <p className="st-lead lv-lead">
              Everything above was one recorded run. This part calls the engine: bring your own complaint and
              article, or pick one of ours below.
            </p>

            <div className="lv-form">
              <label className="lv-field lv-field-q">
                <span className="lv-field-label">
                  <i>1</i> The complaint
                  <small>✎ type or paste</small>
                </span>
                <textarea
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  rows={4}
                  placeholder="Describe what is wrong with the phone"
                />
              </label>
              <div className="lv-field lv-field-a">
                <span className="lv-field-label">
                  <i>2</i> The support article
                  <small className={read.bad ? "bad" : undefined}>
                    {asPreset ? "✎ paste text or the kit's JSON" : read.note}
                  </small>
                </span>
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  aria-label="Article title"
                  placeholder="Article title"
                />
                <textarea
                  value={text}
                  onChange={(e) => onArticle(e.target.value)}
                  rows={3}
                  aria-label="Article text"
                  placeholder={'Paste the article, or its JSON: {"title": "...", "content": "..."}'}
                />
              </div>
              <button
                className="pill pill-lime lv-run"
                onClick={submit}
                disabled={!query.trim() || (!asPreset && read.bad === true && read.siis === null)}
              >
                {status === "running" ? "Running…" : "Find the fix"}
              </button>
            </div>

            <div className="lv-trace">
              <div className="lv-trace-head">
                <span className={`lv-badge ${badge.cls}`}>
                  <i /> {badge.text}
                </span>
                {done && <b className="lv-total">{fmt(done.ms)} ms</b>}
              </div>
              <ol className="lv-stages" ref={trace}>
                {stages.map((f, i) => {
                  const hit = f.stage === "cache" && f.detail?.hit === true;
                  const note = stageNote(f);
                  return (
                    <li
                      key={`${f.stage}-${i}`}
                      className={`lv-stage${usedModel(f) ? " is-llm" : ""}${hit ? " hit" : ""}`}
                    >
                      <span className="lv-dot" />
                      <b>{f.stage}</b>
                      <span className="lv-sum">{f.summary}</span>
                      <small>{fmt(f.ms)} ms</small>
                      {note && <span className="lv-sub">{note}</span>}
                    </li>
                  );
                })}
                {status === "idle" && health !== "offline" && (
                  <li className="lv-empty">
                    {health === "starting"
                      ? "The engine is loading its indexes. It answers in a moment."
                      : "Press Find the fix, or pick one of ours below."}
                  </li>
                )}
                {(status === "offline" || (status === "idle" && health === "offline")) && (
                  <li className="lv-empty">
                    No engine at <code>{API_URL}</code>.{" "}
                    {LOCAL_API ? (
                      <>
                        Start it with <code>docker compose up</code> from the repo root, or{" "}
                        <code>uvicorn app.main:app</code> from <code>api/</code>.
                      </>
                    ) : (
                      "It may be asleep; this page keeps knocking and will light up when it answers."
                    )}
                  </li>
                )}
              </ol>
            </div>

            <div className="lv-presets">
              <span className="lv-presets-label">Or pick one of ours</span>
              <div className="lv-chips" role="list">
                {data.presets.map((p) => (
                  <button
                    key={p.id}
                    role="listitem"
                    className={`lv-chip${asPreset && p.id === preset.id ? " on" : ""}`}
                    onClick={() => pick(p)}
                  >
                    <b>{p.label}</b>
                    <small>{p.hint}</small>
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="lv-phone">
            <Galaxy>
              <LiveScreen
                key={`${status}-${meta.trace_id ?? ""}-${frames.length}`}
                status={status}
                contexts={contexts}
                fallback={meta.fallback ?? null}
                source={meta.source ?? null}
                noScenario={noScenario}
              />
            </Galaxy>
          </div>
        </div>
        <p className="st-fine lv-fine">
          Every preset is the showcase complaint or a held-out variant of it, each showing one thing the engine
          does. Your own complaint and article go to the same engine, with no preset attached.
        </p>
      </div>
    </section>
  );
}

/** Model colour only when a model really ran: on the free tier enrich is rules-only on the answer's path. */
const usedModel = (f: Frame) => LLM_STAGES.has(f.stage) && Boolean(str(f.detail?.model));

/** Who produced the answer: the cache tier on a hit, else the model (or the no-LLM rules path). */
function answeredBy(meta: {
  model?: string | null;
  cache_tier?: string | null;
  fallback?: string | null;
}): string {
  if (meta.fallback === "no_siis_context") return "no article sent, answered from memory";
  if (meta.cache_tier) return `${meta.cache_tier} cache hit, no model call`;
  if (!meta.model) return "answered";
  return meta.model === "rules" ? "rules only, no model" : meta.model;
}

function LiveScreen({
  status,
  contexts,
  fallback,
  source,
  noScenario,
}: {
  status: Status;
  contexts: PlanContext[];
  fallback: string | null;
  source: string | null;
  noScenario: boolean;
}) {
  const [goal, setGoal] = useState(0);
  if (status === "done" && contexts.length > 0) {
    const shown = contexts[Math.min(goal, contexts.length - 1)];
    return (
      <div className="sc sc-live">
        <div className="sc-app">
          <Mark className="sc-mark" />
          OneClick
        </div>
        {/* No article came with the question: say where the plan came from instead of posing as a fresh fix. */}
        {fallback === "no_siis_context" && (
          <p className="lv-memory">
            <b>No article was sent.</b>{" "}
            {source === "retrieved_article"
              ? "OneClick answered from a support article it has read before."
              : "This is a plan OneClick solved before, for a question that means the same."}
          </p>
        )}
        {/* One tab per problem, so a second goal is never hidden under the first one's cards. */}
        {contexts.length > 1 && (
          <div className="lv-goal-tabs" role="tablist">
            {contexts.map((c, i) => (
              <button
                key={c.title}
                role="tab"
                aria-selected={c === shown}
                className={c === shown ? "on" : undefined}
                onClick={() => setGoal(i)}
              >
                <b>{c.title}</b>
                <small>
                  {c.actions.length} {c.actions.length === 1 ? "fix" : "fixes"}
                </small>
              </button>
            ))}
          </div>
        )}
        {[shown].map((c) => (
          <div className="lv-goal" key={c.title}>
            <span className="plan-kicker">
              {fallback === "no_siis_context" ? "From memory" : "Your fix"} · {c.score.toFixed(2)}
            </span>
            <h3 className="sc-large">{c.title}</h3>
            {c.actions.map((a) => {
              const link = a.stepGroups[0]?.actionableDeeplink;
              return (
                <div className="plan-card" key={a.actionName}>
                  <div className="plan-card-top">
                    <span className="plan-card-name">{a.actionName}</span>
                    <span className={`sc-chip sc-chip-${a.category}`}>{CATEGORY[a.category] ?? a.category}</span>
                  </div>
                  <p className="plan-card-desc">{a.description}</p>
                  <ol className="plan-steps">
                    {a.stepGroups.flatMap((g) => g.steps).map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ol>
                  {link && <span className="plan-open">{link.message}</span>}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    );
  }

  const [title, text] =
    status === "running"
      ? ["Finding a fix…", "Reading the article and checking every step against it."]
      : status === "offline"
        ? ["Engine offline", "The page is fine; the engine is not running."]
        : status === "timeout"
          ? ["No answer yet", `The engine did not answer in ${GIVE_UP_S} s. It may be busy: press Find the fix again.`]
        : status === "done" && noScenario
          ? ["No recorded run", "The mock only replays recorded runs. The live engine answers this one."]
          : status === "done"
            ? [
                fallback === "no_siis_context" ? "No article" : "No grounded fix",
                "Nothing in the article supports a fix, so OneClick returns no steps rather than invent them." +
                  (fallback ? ` (${fallback})` : ""),
              ]
            : ["What's wrong?", "Write a complaint and press Find the fix."];

  return (
    <div className="sc sc-live sc-live-empty">
      <span className={`lv-orb${status === "running" ? " busy" : ""}`}>
        <Mark />
      </span>
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
