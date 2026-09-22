/**
 * The official response shape (`ContextDeeplinkResponse` in api/app/schema.py), as far as the
 * story page reads it. Only the compiler's output ever takes this shape.
 */

export interface Deeplink {
  deeplink: string;
  description: string;
  message: string;
  originalType: string;
}

export interface Validation {
  deeplink: string;
  key: string;
  resultType?: string;
  condition?: string;
  value?: string;
}

export interface StepGroup {
  steps: string[];
  actionableDeeplink: Deeplink | null;
  validationDeeplink: Validation | null;
}

export interface Action {
  actionName: string;
  description: string;
  category: string;
  stepGroups: StepGroup[];
}

export interface PlanContext {
  goal: string;
  title: string;
  score: number;
  actions: Action[];
}

export interface ResponseBody {
  contexts: PlanContext[];
  meta?: Record<string, unknown>;
}
