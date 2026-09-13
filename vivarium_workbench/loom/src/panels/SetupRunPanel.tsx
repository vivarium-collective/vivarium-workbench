// src/panels/SetupRunPanel.tsx — merged parameter form + run lifecycle.
//
// Combines ConfigurePanel (parameter configuration form) and RunPanel (run
// controls + polling) into one scrollable panel. Layout:
//   1. Parameters section (card with one input per declared parameter)
//   2. Progress / error / completion feedback
//   3. Sticky action bar: Steps + Run CTA
//
// On run: the current form values are cast and passed directly as overrides to
// startRun — no separate "Apply" step required. A secondary "Preview wiring"
// button optionally re-resolves the composite via /api/composite-resolve so the
// Wiring tab refreshes without launching a run.
//
// On terminal `completed`: calls postRunComplete (postMessage to dashboard) AND
// props.onCompleted() so App can switch to the Results tab.
import { useEffect, useRef, useState, useCallback } from 'react';
import type { ParameterDecl } from '../api';
import {
  postRunComplete, startRun, fetchRunStatus, fetchRunTrajectory,
  type RunStatus,
} from '../api';
import { parseListString, formatListString } from '../parsers';

// ---- Form helpers (lifted from ConfigurePanel) ------------------------------

type FormValue = string | number | boolean;

// Parameter declarations from different sources spell the same type several
// ways: composites emit 'integer' / 'boolean' / 'list' / 'map', while some
// tooling uses 'int' / 'bool' / 'list[string]'. Normalize to a canonical kind
// so casting, seeding, and rendering all agree — a mismatch here sends the raw
// String() of the field to the backend (e.g. seed="0" → RandomState crash,
// config_overrides="[object Object]" → .items() crash).
type ParamKind = 'int' | 'float' | 'bool' | 'list' | 'map' | 'string';
function _normType(t: string): ParamKind {
  switch (t) {
    case 'int': case 'integer': return 'int';
    case 'float': case 'number': case 'double': return 'float';
    case 'bool': case 'boolean': return 'bool';
    case 'list': case 'list[string]': case 'array': return 'list';
    case 'map': case 'dict': case 'object': case 'json': return 'map';
    default: return 'string';
  }
}

function _initialValue(pdef: ParameterDecl, override: unknown): FormValue {
  const seed = override !== undefined ? override : pdef.default;
  switch (_normType(pdef.type)) {
    case 'list':
      return formatListString(Array.isArray(seed) ? (seed as string[]) : []);
    case 'bool':
      return Boolean(seed);
    case 'int':
    case 'float':
      return seed == null ? '' : String(seed);
    case 'map':
      // JSON text, not "[object Object]".
      if (seed == null || seed === '') return '';
      if (typeof seed === 'string') return seed;    // already-serialized override
      try { return JSON.stringify(seed); } catch { return ''; }
    default:
      return seed == null ? '' : String(seed);
  }
}

function _castFormValue(pdef: ParameterDecl, raw: FormValue): unknown {
  switch (_normType(pdef.type)) {
    case 'list':
      return parseListString(String(raw));
    case 'bool':
      return typeof raw === 'boolean' ? raw : String(raw) === 'true';
    case 'int': {
      const n = parseInt(String(raw), 10);
      return Number.isNaN(n) ? null : n;
    }
    case 'float': {
      const n = parseFloat(String(raw));
      return Number.isNaN(n) ? null : n;
    }
    case 'map': {
      // Empty or the legacy "[object Object]" coercion → empty map so iterating
      // generators don't crash; non-empty text is parsed as JSON (lenient).
      const s = String(raw).trim();
      if (s === '' || s === '[object Object]') return {};
      try { return JSON.parse(s); } catch { return {}; }
    }
    default:
      return String(raw);
  }
}

// Export helpers so tests can import them independently.
export { _initialValue, _castFormValue };

// ---- Props ------------------------------------------------------------------

type TrajectoryRow = { step: number; time?: number; state: Record<string, unknown> };

export interface SetupRunPanelProps {
  compositeId: string | null;
  parameters: Record<string, ParameterDecl>;
  overrides: Record<string, unknown>;
  emitSet: Set<string>;
  runContext?: string;
  /** Default number of steps; comes from the composite's
   *  ``@composite_generator(default_n_steps=...)`` declaration via the
   *  ``composite:load`` postMessage. Falls back to 5 if not provided. */
  defaultSteps?: number;
  /** Called when the user clicks "Preview wiring" — re-resolves the composite
   *  via /api/composite-resolve and passes (newOverrides, newState) back to App
   *  so the Wiring tab refreshes. */
  onApplied: (overrides: Record<string, unknown>, state: unknown) => void;
  /** Called with the latest trajectory rows as they arrive. */
  onTrajectory?: (rows: TrajectoryRow[]) => void;
  /** Called with the viz-html map when a run completes. */
  onVizHtml?: (vizHtml: Record<string, { html: string }> | null) => void;
  /** Called when the run reaches terminal `completed` status so App can switch
   *  to the Results tab. */
  onCompleted: () => void;
  /** Called on every status poll tick with the latest run id + downloadable
   *  flag so App can pass them through to the Results tab. */
  onRunState?: (s: { runId: string | null; downloadable: boolean }) => void;
  /** Read-only posture (static/snapshot mode): render the parameter form but
   *  disable Run + Preview wiring, since no live dashboard backend exists. */
  readOnly?: boolean;
  /** How this composite runs: 'temporal' composites advance over time (offer a
   *  Duration); 'workflow' composites are a Step network that just "Run"s once
   *  (no duration). Detected upstream from the node inventory. Default temporal. */
  runKind?: 'temporal' | 'workflow';
}

const ACTIVE_RUN_KEY = 'bigraph-loom:active-run';
const POLL_MS = 1500;
// See useCompositeRun.ts: the full-trajectory read is heavy, so refresh it live
// at most this often and never overlapping — driving progress off the cheap
// /status poll instead. Fetching it every tick made long runs look "stuck".
const LIVE_TRAJ_MS = 15000;

/** Human label for a run phase (backend emits lowercase stage names). */
function _phaseLabel(phase: string): string {
  return phase.charAt(0).toUpperCase() + phase.slice(1);
}

export function SetupRunPanel(props: SetupRunPanelProps) {
  // ---- Parameter form state (from ConfigurePanel) --------------------------

  const [values, setValues] = useState<Record<string, FormValue>>(() =>
    Object.fromEntries(
      Object.entries(props.parameters).map(([k, pdef]) => [
        k, _initialValue(pdef, props.overrides[k]),
      ])
    )
  );
  // Reset form whenever the composite changes (new parameters or overrides).
  useEffect(() => {
    setValues(Object.fromEntries(
      Object.entries(props.parameters).map(([k, pdef]) => [
        k, _initialValue(pdef, props.overrides[k]),
      ])
    ));
  }, [props.parameters, props.overrides]);

  // ---- Run lifecycle state (from RunPanel) ---------------------------------

  const [steps, setSteps] = useState(props.defaultSteps ?? 5);
  // When a new composite loads with a different defaultSteps, re-seed the
  // input so the user sees the composite's recommended run length without
  // having to manually clear an old value.
  useEffect(() => {
    if (props.defaultSteps != null) setSteps(props.defaultSteps);
  }, [props.compositeId, props.defaultSteps]);

  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const inInvestigation = !!(props.runContext && props.runContext.startsWith('investigation:'));
  const canRun = !!props.compositeId && !inInvestigation && !props.readOnly;
  const isRunning = status?.status === 'running' || (!!runId && !status);
  const isWorkflow = props.runKind === 'workflow';

  // Use refs for callbacks so the polling closure always sees the latest
  // version without needing to be recreated (same pattern as RunPanel).
  const onTrajectoryRef = useRef(props.onTrajectory);
  const onVizHtmlRef = useRef(props.onVizHtml);
  const onCompletedRef = useRef(props.onCompleted);
  const onRunStateRef = useRef(props.onRunState);
  useEffect(() => { onTrajectoryRef.current = props.onTrajectory; }, [props.onTrajectory]);
  useEffect(() => { onVizHtmlRef.current = props.onVizHtml; }, [props.onVizHtml]);
  useEffect(() => { onCompletedRef.current = props.onCompleted; }, [props.onCompleted]);
  useEffect(() => { onRunStateRef.current = props.onRunState; }, [props.onRunState]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const trajInFlightRef = useRef(false);
  const lastTrajAtRef = useRef(0);
  const loadTrajectory = useCallback(async (id: string, opts?: { throttleMs?: number }) => {
    const throttleMs = opts?.throttleMs ?? 0;
    if (trajInFlightRef.current) return;                                  // never overlap
    if (throttleMs && Date.now() - lastTrajAtRef.current < throttleMs) return;
    trajInFlightRef.current = true;
    try {
      const traj = await fetchRunTrajectory(id);
      lastTrajAtRef.current = Date.now();
      onTrajectoryRef.current?.(traj.trajectory);
    } catch {
      /* trajectory not ready yet — ignore, next poll retries */
    } finally {
      trajInFlightRef.current = false;
    }
  }, []);

  // Poll one run until terminal. Independent cheap requests: a dropped poll
  // simply retries on the next tick.
  const beginPolling = useCallback((id: string) => {
    stopPolling();
    const tick = async () => {
      let s: RunStatus;
      try {
        s = await fetchRunStatus(id);
      } catch {
        return; // transient — try again next tick
      }
      setStatus(s);
      onRunStateRef.current?.({ runId: id, downloadable: s.downloadable ?? false });
      if (s.viz_html) onVizHtmlRef.current?.(s.viz_html);
      if (s.status === 'running') {
        void loadTrajectory(id, { throttleMs: LIVE_TRAJ_MS });  // throttled live-scrub
      } else {
        stopPolling();
        void loadTrajectory(id);  // final result — once, unthrottled
        sessionStorage.removeItem(ACTIVE_RUN_KEY);
        if (s.status === 'completed' && props.compositeId) {
          postRunComplete(id, props.compositeId);
          onCompletedRef.current();
        }
      }
    };
    void tick();
    pollRef.current = setInterval(tick, POLL_MS);
  }, [stopPolling, loadTrajectory, props.compositeId]);

  // Re-attach to an in-flight run after an iframe reload / network blip.
  useEffect(() => {
    const raw = sessionStorage.getItem(ACTIVE_RUN_KEY);
    if (!raw) return;
    try {
      const saved = JSON.parse(raw) as { run_id: string; composite_id: string };
      if (saved.composite_id === props.compositeId && saved.run_id) {
        setRunId(saved.run_id);
        beginPolling(saved.run_id);
      }
    } catch {
      sessionStorage.removeItem(ACTIVE_RUN_KEY);
    }
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.compositeId]);

  // ---- Handlers -----------------------------------------------------------

  /** Run: cast current form values to overrides and start the run directly.
   *  No separate Apply step is required — Run applies the parameters. */
  async function handleRun() {
    if (!props.compositeId) {
      setStartError('No composite id — pop-out windows need ?id=<dotted-ref> in the URL.');
      return;
    }
    setStartError(null);
    setStatus(null);
    onTrajectoryRef.current?.([]);   // clear previous results
    onVizHtmlRef.current?.(null);

    // Cast current form values to overrides; merge with externally-provided
    // overrides (form values take precedence for any declared parameter).
    const formOverrides: Record<string, unknown> = {};
    for (const [k, pdef] of Object.entries(props.parameters)) {
      formOverrides[k] = _castFormValue(pdef, values[k]);
    }
    const runOverrides = { ...props.overrides, ...formOverrides };

    try {
      const res = await startRun({
        id: props.compositeId,
        // A workflow (Step network) runs its DAG once — a single composite tick
        // fires every ready Step; no duration applies. A temporal composite runs
        // for the chosen Duration.
        steps: props.runKind === 'workflow' ? 1 : steps,
        emit_paths: Array.from(props.emitSet),
        overrides: Object.keys(runOverrides).length > 0 ? runOverrides : undefined,
      });
      setRunId(res.run_id);
      sessionStorage.setItem(ACTIVE_RUN_KEY, JSON.stringify({
        run_id: res.run_id, composite_id: props.compositeId,
      }));
      beginPolling(res.run_id);
    } catch (e: unknown) {
      setStartError(String(e instanceof Error ? e.message : e));
    }
  }

  // ---- Render -------------------------------------------------------------

  const paramKeys = Object.keys(props.parameters);

  // Investigation context: running is managed by the Study controls.
  if (inInvestigation) {
    return (
      <div className="sr-panel">
        <h3 style={{ marginTop: 0 }}>Setup &amp; Run</h3>
        <p style={{ color: '#6b7280' }}>
          Use the Study&apos;s Run controls to run with this investigation&apos;s emitters.
        </p>
      </div>
    );
  }

  const pct = status && status.n_steps
    ? Math.min(100, Math.round((status.progress_step / status.n_steps) * 100))
    : 0;

  return (
    <div className="sr-panel">
      {props.readOnly && (
        <p style={{
          margin: '0 0 12px', padding: '8px 10px', fontSize: 13,
          background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 6,
          color: '#475569',
        }}>
          Read-only preview — running requires a live dashboard.
        </p>
      )}
      {/* ---- Parameters card -------------------------------------------- */}
      {paramKeys.length > 0 && (
        <section className="sr-section">
          <h3>Parameters</h3>
          <div>
            {paramKeys.map((k) => {
              const pdef = props.parameters[k];
              const id = `cfg-${k}`;
              const val = values[k];
              const onChange = (v: FormValue) => setValues((prev) => ({ ...prev, [k]: v }));
              return (
                <div key={k} className="sr-field">
                  <label htmlFor={id}>
                    <code style={{ fontWeight: 600 }}>{k}</code>
                    <span style={{ color: '#666', marginLeft: 8, fontSize: 12 }}>
                      ({pdef.type})
                    </span>
                  </label>
                  {pdef.description && (
                    <div style={{ color: '#666', fontSize: 12, marginBottom: 4 }}>
                      {pdef.description}
                    </div>
                  )}
                  {Array.isArray(pdef.choices) && pdef.choices.length > 0 ? (
                    <select
                      id={id}
                      value={String(val)}
                      onChange={(e) => onChange(e.target.value)}
                      className="sr-input"
                    >
                      {pdef.choices.map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                  ) : _normType(pdef.type) === 'list' ? (
                    <textarea
                      id={id}
                      rows={Math.max(3, String(val).split('\n').length + 1)}
                      value={String(val)}
                      onChange={(e) => onChange(e.target.value)}
                      className="sr-input"
                      placeholder="one item per line"
                    />
                  ) : _normType(pdef.type) === 'bool' ? (
                    <select
                      id={id}
                      value={String(val)}
                      onChange={(e) => onChange(e.target.value === 'true')}
                      className="sr-input"
                    >
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </select>
                  ) : (
                    <input
                      id={id}
                      type={_normType(pdef.type) === 'int' || _normType(pdef.type) === 'float' ? 'number' : 'text'}
                      step={_normType(pdef.type) === 'float' ? 'any' : _normType(pdef.type) === 'int' ? '1' : undefined}
                      value={String(val)}
                      onChange={(e) => onChange(e.target.value)}
                      className="sr-input"
                      style={{ minWidth: 240 }}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* ---- Run feedback area ------------------------------------------ */}
      {startError && (
        <div style={{ color: '#c00', marginBottom: 8 }}>
          <strong>Could not start run:</strong> {startError}
        </div>
      )}

      {isRunning && status && (
        <div style={{ margin: '8px 0' }}>
          {status.phase && status.phase !== 'simulating' ? (
            // Post-simulation stages (rendering visualizations / analysis flush):
            // the sim bar is done; announce the current stage instead.
            <small style={{ color: '#6b7280', display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className="sr-phase-dot" />
              {_phaseLabel(status.phase)}… — running detached; safe to reload this tab.
            </small>
          ) : (
            <>
              <div style={{ background: '#e5e7eb', borderRadius: 4, height: 10, overflow: 'hidden' }}>
                <div style={{ width: `${pct}%`, background: '#3b82f6', height: '100%' }} />
              </div>
              <small style={{ color: '#6b7280' }}>
                {isWorkflow
                  ? 'Running workflow'
                  : `Simulating — step ${status.progress_step} of ${status.n_steps ?? '?'}`}
                {' '}— running detached; safe to reload this tab.
              </small>
            </>
          )}
        </div>
      )}
      {isRunning && !status && (
        <p style={{ color: '#6b7280' }}>Starting run…</p>
      )}

      {status && (status.status === 'failed' || status.status === 'orphaned') && (
        <div style={{ color: '#c00', marginBottom: 8 }}>
          <p style={{ margin: 0 }}>
            <strong>Run {status.status}.</strong>{' '}
            {status.log_path && <span>See log: <code>{status.log_path}</code></span>}
          </p>
          {status.error && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ cursor: 'pointer', color: '#7f1d1d' }}>Show log excerpt</summary>
              <pre style={{
                background: '#fef2f2', border: '1px solid #fecaca', padding: 10,
                fontSize: 11, lineHeight: 1.4, overflow: 'auto', maxHeight: 320,
                marginTop: 6, whiteSpace: 'pre-wrap',
              }}>
                {status.error.trim()}
              </pre>
            </details>
          )}
        </div>
      )}

      {status?.status === 'completed' && (
        <p style={{ color: '#6b7280', fontSize: 13, margin: '4px 0 10px' }}>
          {isWorkflow
            ? <>Workflow complete. Switching to the <strong>Results</strong> tab…</>
            : <>Run complete — <strong>{status.n_steps ?? 0}</strong> steps. Switching to the <strong>Results</strong> tab…</>}
        </p>
      )}

      {!runId && !startError && (
        <p style={{ color: '#888' }}>
          {isWorkflow
            ? <>Click <strong>Run</strong> to execute this composite&apos;s Step network once.</>
            : <>Click <strong>Run</strong> to advance this composite for the chosen duration.</>}
          {paramKeys.length > 0 && ' Current parameter values will be applied automatically.'}
        </p>
      )}

      {/* ---- Sticky action bar --------------------------------------------- */}
      {/* Temporal composites offer a Duration; workflow (Step-network) composites
          just Run once — their run length is not a user choice. */}
      <div className="sr-actionbar">
        {!isWorkflow && (
          <label title="How long to advance the composite, in time units (fractional allowed for temporal composites)">
            Duration{' '}
            <input
              type="number" min={0} max={10000} step="any" value={steps}
              onChange={(e) => { const v = parseFloat(e.target.value); setSteps(Number.isFinite(v) && v > 0 ? v : 1); }}
              style={{ width: 70 }} disabled={isRunning}
            />
          </label>
        )}
        <button
          onClick={handleRun}
          disabled={isRunning || !canRun}
          className="sr-run-btn"
        >
          {isRunning ? (isWorkflow ? 'Running workflow…' : 'Running…') : 'Run'}
        </button>
        <small style={{ color: '#666' }}>
          Emit selections:{' '}
          {props.emitSet.size === 0
            ? <em>none — pick stores in the Wiring tab</em>
            : Array.from(props.emitSet).join(', ')}
        </small>
      </div>
    </div>
  );
}
