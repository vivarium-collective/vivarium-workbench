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
  description?: string;
  metadata?: { name?: string; description?: string; library?: string; context?: string; id?: string };
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

export type ExploreAutoHeightMsg = {
  type: 'explore:autoheight';
  height: number;  // the surface's natural content height, in CSS px
};

/** Report the loom surface's natural content height so an embedding card can
 *  size its iframe to fit — the surface then grows/shrinks downward with the
 *  graph instead of scrolling inside a fixed-height frame. */
export function postAutoHeight(height: number) {
  const target = _embeddingTarget();
  if (target) target.postMessage(
    { type: 'explore:autoheight', height } as ExploreAutoHeightMsg,
    '*',
  );
}

export type ExploreCollapseCardMsg = { type: 'explore:collapse-card' };

/** Ask the embedding card to fully collapse this loom back to its pre-mount
 *  strip (header + the single open bar). Used by the surface's bottom bar. */
export function postCollapseCard() {
  const target = _embeddingTarget();
  if (target) target.postMessage({ type: 'explore:collapse-card' } as ExploreCollapseCardMsg, '*');
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

/** Drill into a Composite Process: fetch the loom state of the inner composite
 *  embedded at `hops` under root generator `rootId`. `hops` is the accumulated
 *  list of node paths (one per drill level, each a bigraph path array). Returns
 *  the same `{state}` shape as /api/composite-state, plus `crumbs`. Rejects on
 *  non-2xx (bad hop / not a composite process / worker unavailable). */
export interface InnerCompositeResponse {
  state: any;
  crumbs?: string[];
  error?: string;
}

/** Deterministic, filesystem-safe key for a (rootId, hops) inner-composite
 *  target. MUST match the Python side (composite_inner_states.inner_state_key):
 *  base64url (no padding) of `rootId + '::' + JSON.stringify(hops)`. Used only
 *  in static (?static=1) mode to name the pre-built inner-state file. Input is
 *  ASCII (dotted ids + bigraph path segments), so btoa is safe. */
export function innerCompositeKey(rootId: string, hops: string[][]): string {
  const raw = rootId + '::' + JSON.stringify(hops);
  return btoa(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export async function fetchInnerComposite(
  rootId: string,
  hops: string[][],
  overrides?: Record<string, unknown>,
): Promise<InnerCompositeResponse> {
  // Static (read-only bundle) mode: no live endpoint. Fetch the pre-built inner
  // state committed by publish under api/composite-inner-state/<key>.json.
  // `apiBase` prefixes the bundle's subpath (e.g. GitHub Pages project sites).
  const params = new URLSearchParams(window.location.search);
  // ``static=1`` alone means a published snapshot (no live server) → read the
  // pre-built inner-state file. But the dashboard's in-card VIEW-ONLY loom also
  // sets static=1 (to load the top-level from ?stateUrl= and skip the heavy live
  // param re-resolve) while the server IS present — it signals that with
  // ``live=1`` so inner-composite drill-in uses the live endpoint below.
  if (params.get('static') === '1' && params.get('live') !== '1') {
    const apiBase = params.get('apiBase') || '';
    const url =
      apiBase + '/api/composite-inner-state/' + innerCompositeKey(rootId, hops) + '.json';
    const r = await fetch(url);
    if (!r.ok) {
      throw new Error(
        `inner composite not available offline (HTTP ${r.status}) — this ` +
          `read-only bundle did not pre-build it`,
      );
    }
    return (await r.json()) as InnerCompositeResponse;
  }
  const q = new URLSearchParams({ ref: rootId, hops: JSON.stringify(hops) });
  // Forward the config overrides so the drill rebuilds the ROOT config-applied
  // (e.g. n_generations>1 → the batch's `batch_runner` exists to navigate into),
  // matching the graph the user is drilling from.
  if (overrides && Object.keys(overrides).length) {
    q.set('overrides', JSON.stringify(overrides));
  }
  // The inner build is ParCa-heavy and can wedge (a stuck env-worker build never
  // returns), which otherwise leaves the preview on "building inner model…"
  // forever. Cap it so the caller falls to a retryable error instead of hanging.
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 60000);
  let r: Response;
  try {
    r = await fetch('/api/composite-inner-state?' + q.toString(), { signal: ctrl.signal });
  } catch (e: any) {
    throw new Error(
      e?.name === 'AbortError' ? 'inner composite build timed out' : (e?.message || String(e)),
    );
  } finally {
    clearTimeout(timer);
  }
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body as InnerCompositeResponse;
}

// --- Run lifecycle (start-then-poll) -------------------------------------

export type RunStatusValue = 'running' | 'completed' | 'failed' | 'orphaned' | 'cancelled';

export interface StartRunArgs {
  id: string;
  steps: number;
  emit_paths: string[];
  overrides?: Record<string, unknown>;
  label?: string;
  /** Save-point fork: a captured frame state to START this run from (the
   *  backend overlays it onto the freshly-built composite). Omitted = a normal
   *  run from the generator's initial state. */
  seed_state?: Record<string, unknown>;
}

export interface StartRunResponse {
  run_id: string;
  status: RunStatusValue;
  /** Non-blocking heads-up from the backend (e.g. a heavy/long run). The run
   *  still starts; the loom surfaces this next to the run controls. */
  warning?: string;
}

export interface RunStatus {
  run_id: string;
  status: RunStatusValue;
  progress_step: number;
  n_steps: number | null;
  heartbeat_at: number | null;
  /** Image-backed Cloud run (plan B): runs on GovCloud via run_simulation, so
   *  progress is a coarse phase (queued/running/done), not a per-step count.
   *  The run bar shows "Running on cloud…" instead of "step null/?". */
  remote?: boolean;
  raw_status?: string;
  /** Sub-status while status==='running' (simulate → rendering visualizations →
   *  analysis flush) so the UI can announce the current stage. */
  phase?: string | null;
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

/** Resolved composite config — the parameter form the Setup & Run tab renders.
 *  /api/composite-state carries the wiring but NOT the config, so the Explorer
 *  fetches this separately so EVERY composite shows the same Setup & Run form. */
export interface ResolveResponse {
  parameters?: Record<string, ParameterDecl>;
  overrides?: Record<string, unknown>;
  default_n_steps?: number;
  name?: string;
  description?: string;
  library?: string;
  id?: string;
  error?: string;
  /** The resolved composite state (bigraph). Present on /api/composite-resolve —
   *  the Explore Config panel re-renders the graph from this after Apply. */
  state?: unknown;
  /** Visualizations the composite generator declares up front (from
   *  `spec.visualizations`). The Outputs → Visualizations panel renders these as
   *  expected cards before the run produces them. */
  visualizations?: Array<{ name?: string; config?: { title?: string } | null }>;
}

export async function resolveComposite(
  id: string,
  overrides?: Record<string, unknown>,
): Promise<ResolveResponse> {
  const q = new URLSearchParams({ id, overrides: JSON.stringify(overrides ?? {}) });
  const r = await fetch('/api/composite-resolve?' + q.toString());
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body as ResolveResponse;
}

export interface TranslateConfigResponse {
  params: Record<string, unknown>;
  unmatched: string[];
  error?: string;
}

/** Item 86: match an arbitrary JSON document's keys onto a composite's own
 *  declared parameters — the "external config" input mode alongside the
 *  per-field Configure form. Non-mutating. */
export async function translateExternalConfig(
  compositeId: string,
  configJson: Record<string, unknown>,
): Promise<TranslateConfigResponse> {
  const r = await fetch('/api/composite-config-translate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ composite_id: compositeId, config_json: configJson }),
  });
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body as TranslateConfigResponse;
}

/** Parse a `?overrides=<json>` query value into an overrides object. A study
 *  deep-links its composite with the study's real config (conditions.baseline.
 *  params) here, so the Configure panel shows the study config rather than the
 *  composite's bare defaults. Absent, non-object, or invalid JSON → `{}`. */
export function parseUrlOverrides(search: string): Record<string, unknown> {
  try {
    const raw = new URLSearchParams(search).get('overrides');
    if (!raw) return {};
    const o = JSON.parse(raw) as unknown;
    return (o && typeof o === 'object' && !Array.isArray(o))
      ? (o as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** Remote-dispatch confirm gate. On a remote-PINNED workspace, composite-test-run
 *  dispatches to AWS Batch (resolve_run_target → 'deployment'), so require explicit
 *  confirmation before firing — mirrors walkthrough.js's _confirmRemoteDispatchThen
 *  (fetch fresh /api/remote-run-config; show repo/branch/commit/simulator_id; block
 *  on cancel). A local (unpinned) workspace, or an unreachable config, does not gate. */
async function _confirmRemoteDispatch(): Promise<void> {
  let cfg: Record<string, unknown> = {};
  try {
    const r = await fetch('/api/remote-run-config');
    if (r.ok) cfg = (await r.json()) as Record<string, unknown>;
  } catch { cfg = {}; }
  if (!cfg || !cfg.pinned) return;
  const s = (v: unknown) => (v == null ? '(unknown)' : String(v));
  const msg = 'Dispatch to AWS Batch:\n\n'
    + '  repo:    ' + s(cfg.repo_url) + '\n'
    + '  branch:  ' + s(cfg.branch) + '\n'
    + '  commit:  ' + s(cfg.commit).slice(0, 12) + '\n'
    + '  simulator id: ' + s(cfg.simulator_id) + '\n\n'
    + 'Proceed?';
  if (!window.confirm(msg)) {
    throw new Error('Run cancelled — remote dispatch not confirmed.');
  }
}

/** Dynamic run-target, set on the embed URL by loom-embed.js when the workbench
 *  Environment scope is Cloud. `run_target=deployment` + build → route this Run to
 *  the cloud against that build's committed code; `run_target=deployment` with no
 *  build is the Q3 case (Cloud active, nothing selected) — the caller blocks. */
function _dynamicRunTarget():
  | { run_target: 'deployment'; build?: { simulator_id: number; repo_url: string; commit: string } }
  | null {
  const q = new URLSearchParams(window.location.search);
  if (q.get('run_target') !== 'deployment') return null;
  const sim = q.get('build_sim');
  if (sim == null || sim === '') return { run_target: 'deployment' };
  return {
    run_target: 'deployment',
    build: { simulator_id: Number(sim), repo_url: q.get('build_repo') || '', commit: q.get('build_commit') || '' },
  };
}

/** Start a detached composite run. Resolves with {run_id}; rejects on non-2xx
 *  (notably 429 when the concurrency cap is hit) with the server's error text.
 *  When the Environment scope is Cloud (dynamic run-target), dispatches against the
 *  selected build's committed code and skips the git-push confirm gate (the backend
 *  runs git+repo@commit — no local push). Otherwise passes through the gate. */
export async function startRun(args: StartRunArgs): Promise<StartRunResponse> {
  const rt = _dynamicRunTarget();
  let body: Record<string, unknown> = { ...(args as unknown as Record<string, unknown>) };
  if (rt) {
    if (!rt.build) {
      throw new Error('Cloud is active but no build is selected — pick or build one, or switch to Local.');
    }
    // Explicit-build Cloud run: the backend dispatches against git+repo@commit and
    // skips the git-ready/push preflight, so we skip the confirm gate too.
    body = { ...body, run_target: rt.run_target, build: rt.build };
  } else {
    await _confirmRemoteDispatch();
  }
  const r = await fetch('/api/composite-test-run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const respBody = await r.json();
  if (!r.ok) throw new Error(respBody.error || `HTTP ${r.status}`);
  return respBody as StartRunResponse;
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

export interface StopRunResponse {
  run_id: string;
  outcome: string;   // signalled | already_terminal | no_pid | dead | not_found
  status?: string;   // 'cancelled' once stopped
}

/** Stop an in-flight run: SIGTERMs the detached worker's process group and marks
 *  it `cancelled`. Whatever the run emitted up to the stop stays readable via
 *  fetchRunTrajectory — so the caller keeps the results computed so far.
 *  Idempotent: stopping an already-finished run is a 200 no-op. */
export async function stopRun(runId: string): Promise<StopRunResponse> {
  const r = await fetch(`/api/composite-run/${runId}/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body as StopRunResponse;
}
