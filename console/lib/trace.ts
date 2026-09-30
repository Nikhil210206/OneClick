/**
 * Shapes of the stage events on POST /v1/troubleshoot/stream.
 *
 * Only the envelope is typed. `detail` stays optional and untyped here, so a stage that ships a
 * thinner `detail` than the fixtures degrades to its summary rather than crashing the page.
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

/** Stages that cost an LLM round trip: the only time tinted Samsung blue, and where the live badge reads the model. */
export const LLM_STAGES = new Set<StageName>(["enrich", "extract"]);
