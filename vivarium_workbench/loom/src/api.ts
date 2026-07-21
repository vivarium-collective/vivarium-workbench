// src/api.ts — postMessage protocol with the embedding dashboard.

/** One parameter declared by a composite (spec or generator). Mirrors the
 *  Python decorator's parameters shape. */
export interface ParameterDecl {
  type: 'string' | 'int' | 'float' | 'bool' | 'list[string]' | string;
  default?: unknown;
  description?: string;
  /** Optional enum: when present (a list of allowed string values), the
   *  Configure form renders a dropdown instead of a free-text input. */
  choices?: string[];
}

export type CompositeLoadMsg = {
  type: 'composite:load';
  state: any;
  parameters?: Record<string, ParameterDecl>;
  overrides?: Record<string, unknown>;
  default_n_steps?: number;
  metadata?: { name?: string; library?: string; context?: string; id?: string };
};

export type ExploreReadyMsg = { type: 'explore:ready' };

export type ExploreInspectMsg = {
  type: 'explore:inspect';
  path: string[];
  kind: 'store' | 'process';
  details: Record<string, unknown>;
};

export type ExploreEmitChangedMsg = {
  type: 'explore:emit-changed';
  paths: string[];  // explicit-emit path strings, joined by '/'
};

export type ExploreRunCompleteMsg = {
  type: 'explore:run-complete';
  simulation_id: string;
  composite_id: string;
};

/** Pick the right postMessage target for the embedding context.
 *
 * - Embedded iframe: messages go to `window.parent` (the embedding page).
 * - Pop-out window: `window.parent === window` (no parent frame); the dashboard
 *   that opened us is at `window.opener`. Without this branch the popup posts
 *   to itself and the dashboard never sees `explore:ready` → no state arrives.
 */
function _embeddingTarget(): WindowProxy | null {
  if (window.opener && window.opener !== window) return window.opener;
  if (window.parent && window.parent !== window) return window.parent;
  return null;
}

export function postReady() {
  const target = _embeddingTarget();
  if (target) target.postMessage({ type: 'explore:ready' } as ExploreReadyMsg, '*');
}

export function postInspect(payload: Omit<ExploreInspectMsg, 'type'>) {
  const target = _embeddingTarget();
  if (target) target.postMessage({ type: 'explore:inspect', ...payload }, '*');
}

export function postEmitChanged(paths: string[]) {
  const target = _embeddingTarget();
  if (target) target.postMessage(
    { type: 'explore:emit-changed', paths } as ExploreEmitChangedMsg,
    '*',
  );
}

export function postRunComplete(simulation_id: string, composite_id: string) {
  const target = _embeddingTarget();
  if (target) target.postMessage(
    { type: 'explore:run-complete', simulation_id, composite_id } as ExploreRunCompleteMsg,
    '*',
  );
}

export function onCompositeLoad(handler: (msg: CompositeLoadMsg) => void) {
  const listener = (ev: MessageEvent) => {
    if (ev.data?.type === 'composite:load') handler(ev.data as CompositeLoadMsg);
  };
  window.addEventListener('message', listener);
  return () => window.removeEventListener('message', listener);
}

/** Decode an optional URL-param composite (?composite=<base64-json>). */
export function decodeUrlComposite(): any | null {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get('composite');
  if (!raw) return null;
  try {
    return JSON.parse(atob(raw));
  } catch {
    return null;
  }
}

// --- Run lifecycle (start-then-poll) -------------------------------------

export type RunStatusValue = 'running' | 'completed' | 'failed' | 'orphaned';

export interface StartRunArgs {
  id: string;
  steps: number;
  emit_paths: string[];
  overrides?: Record<string, unknown>;
  label?: string;
}

export interface StartRunResponse {
  run_id: string;
  status: RunStatusValue;
}

export interface RunStatus {
  run_id: string;
  status: RunStatusValue;
  progress_step: number;
  n_steps: number | null;
  heartbeat_at: number | null;
  error?: string;
  log_path?: string;
  viz_html?: Record<string, { html: string }>;
  has_analyses?: boolean;
  has_report?: boolean;
  downloadable?: boolean;
}

export function runDownloadUrl(runId: string): string {
  return `/api/composite-run/${runId}/download`;
}

export interface RunTrajectory {
  run_id: string;
  trajectory: Array<{ step: number; time?: number; state: Record<string, unknown> }>;
}

/** Start a detached composite run. Resolves with {run_id}; rejects on non-2xx
 *  (notably 429 when the concurrency cap is hit) with the server's error text. */
export async function startRun(args: StartRunArgs): Promise<StartRunResponse> {
  const r = await fetch('/api/composite-test-run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body as StartRunResponse;
}

/** Poll one run's status. Cheap single-row read; safe to call on an interval. */
export async function fetchRunStatus(runId: string): Promise<RunStatus> {
  const r = await fetch(`/api/composite-run/${runId}/status`);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body as RunStatus;
}

/** Fetch a run's trajectory. Works mid-run (partial) and after completion. */
export async function fetchRunTrajectory(runId: string): Promise<RunTrajectory> {
  const r = await fetch(`/api/composite-run/${runId}`);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body as RunTrajectory;
}
