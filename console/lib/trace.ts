/**
 * Shapes of the stage events on POST /v1/troubleshoot/stream.
 *
 * Only the fields the console renders are typed. Everything is optional past the
 * envelope: a stage that ships a thinner `detail` than the fixtures must degrade to a
 * summary row rather than crash the trace, because the engine is still being built.
 */

export type StageName =
  | "cache"
  | "enrich"
  | "segment"
  | "extract"
  | "ground"
  | "resolve"
  | "compile"
  | "done";

export interface StageEvent<D = unknown> {
  stage: StageName;
  ms: number;
  summary: string;
  detail?: D;
}

export interface Sentence {
  id: string;
  section: string;
  text: string;
  relevance: number;
}

export interface Section {
  id: string;
  heading: string;
  level: number;
  sentence_ids: string[];
  relevant: boolean;
}

export interface CacheDetail {
  hit: boolean;
  tier: string | null;
  similarity: number | null;
  threshold: number;
  slots: Record<string, string | null>;
  norm_query: string;
  /** Title of the SIIS article the request carried. */
  siis_title?: string;
}

export interface EnrichDetail {
  canonical_query: string;
  intents: unknown[];
  variations: string[];
  dropped_variations: unknown[];
  model: string;
  tokens_in: number;
  tokens_out: number;
}

export interface SegmentDetail {
  sections: Section[];
  sentences: Sentence[];
  relevance_floor: number;
}

export interface ExtractDetail {
  model: string;
  tokens_in: number;
  tokens_out: number;
}

export interface GroundedStep {
  text: string;
  src_ids: string[];
  grounded: boolean;
  grounding_score: number;
}

export interface GroundedAction {
  name: string;
  description: string;
  category: string;
  steps: GroundedStep[];
  screen_path: string | null;
  intent_verb: string | null;
  link: { tier?: string; id?: string; deeplink?: string } | null;
}

export interface DroppedStep {
  action: string;
  text: string;
  src_ids: string[];
  grounding_score: number;
  reason: string;
}

export interface GroundDetail {
  threshold: number;
  proposed_steps: number;
  kept_steps: number;
  coverage: number;
  dropped_steps: DroppedStep[];
  actions: GroundedAction[];
}

export interface ResolveDetail {
  counts: Record<string, number>;
  links: unknown[];
}

export interface CompileDetail {
  url_leaks: number;
  schema_valid: boolean;
  score_inputs?: Record<string, number>;
  repairs?: unknown[];
}

/** The order the trace renders in, independent of arrival order. */
export const STAGE_ORDER: StageName[] = [
  "cache",
  "enrich",
  "segment",
  "extract",
  "ground",
  "resolve",
  "compile",
  "done",
];

/** Stages that cost an LLM round trip. They are the only ones tinted accent in the waterfall. */
export const LLM_STAGES = new Set<StageName>(["enrich", "extract"]);

export const STAGE_LABEL: Record<StageName, string> = {
  cache: "cache",
  enrich: "enrich",
  segment: "segment",
  extract: "extract",
  ground: "ground",
  resolve: "resolve",
  compile: "compile",
  done: "done",
};

export function byStage(events: StageEvent[]): Partial<Record<StageName, StageEvent>> {
  return Object.fromEntries(events.map((e) => [e.stage, e])) as Partial<
    Record<StageName, StageEvent>
  >;
}

/**
 * Per-stage durations for the waterfall.
 *
 * `ms` on a stage frame is already that stage's own duration, not elapsed time since the
 * request started (data/fixtures/README.md). `done.ms` is the total, so it is excluded —
 * including it would double the axis and shrink every real segment by half.
 */
export function stageDurations(events: StageEvent[]): { stage: StageName; ms: number }[] {
  return events.filter((e) => e.stage !== "done").map((e) => ({ stage: e.stage, ms: e.ms }));
}

/** Total wall time: `done.ms` when the stream finished, else the sum of what has arrived. */
export function totalMs(events: StageEvent[]): number {
  const done = events.find((e) => e.stage === "done");
  if (done) return done.ms;
  return stageDurations(events).reduce((sum, s) => sum + s.ms, 0);
}
