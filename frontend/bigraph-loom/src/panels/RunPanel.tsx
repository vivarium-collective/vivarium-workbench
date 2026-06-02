import { useState, useEffect, useRef, useCallback } from 'react';
import type React from 'react';
import {
  postRunComplete, startRun, fetchRunStatus, fetchRunTrajectory,
  type RunStatus,
} from '../api';

type TrajectoryRow = { step: number; time?: number; state: Record<string, unknown> };

export interface RunPanelProps {
  compositeId: string | null;
  emitSet: Set<string>;
  overrides?: Record<string, unknown>;
  runContext?: string;
  /** Default number of steps; comes from the composite's
   *  ``@composite_generator(default_n_steps=...)`` declaration via the
   *  ``composite:load`` postMessage. Falls back to 5 if not provided. */
  defaultSteps?: number;
  /** Called with the latest trajectory rows as they arrive. The ResultsPanel
   *  is responsible for rendering them. */
  onTrajectory?: (rows: TrajectoryRow[]) => void;
  /** Called with the viz-html map when a run completes. The
   *  VisualizationsPanel renders each entry in an iframe. */
  onVizHtml?: (vizHtml: Record<string, { html: string }> | null) => void;
}

const ACTIVE_RUN_KEY = 'loom-explore:active-run';
const POLL_MS = 1500;

export function RunPanel(props: RunPanelProps) {
  const [steps, setSteps] = useState(props.defaultSteps ?? 5);
  // When a new composite loads with a different defaultSteps, re-seed the
  // input so the user sees the composite's recommended run length without
  // having to manually clear an old value. We deliberately key on
  // compositeId so manual edits inside one composite aren't clobbered.
  useEffect(() => {
    if (props.defaultSteps != null) setSteps(props.defaultSteps);
  }, [props.compositeId, props.defaultSteps]);
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const inInvestigation = !!(props.runContext && props.runContext.startsWith('investigation:'));
  const canRun = !!props.compositeId && !inInvestigation;
  const isRunning = status?.status === 'running' || (!!runId && !status);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const onTrajectoryRef = useRef(props.onTrajectory);
  const onVizHtmlRef = useRef(props.onVizHtml);
  useEffect(() => { onTrajectoryRef.current = props.onTrajectory; }, [props.onTrajectory]);
  useEffect(() => { onVizHtmlRef.current = props.onVizHtml; }, [props.onVizHtml]);

  const loadTrajectory = useCallback(async (id: string) => {
    try {
      const traj = await fetchRunTrajectory(id);
      onTrajectoryRef.current?.(traj.trajectory);
    } catch {
      /* trajectory not ready yet — ignore, next poll retries */
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
      if (s.viz_html) onVizHtmlRef.current?.(s.viz_html);
      if (s.status === 'running') {
        void loadTrajectory(id);
      } else {
        stopPolling();
        void loadTrajectory(id);
        sessionStorage.removeItem(ACTIVE_RUN_KEY);
        if (s.status === 'completed' && props.compositeId) {
          postRunComplete(id, props.compositeId);
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

  async function handleRun() {
    if (!props.compositeId) {
      setStartError('No composite id — pop-out windows need ?id=<dotted-ref> in the URL.');
      return;
    }
    setStartError(null);
    setStatus(null);
    onTrajectoryRef.current?.([]);   // clear previous results
    onVizHtmlRef.current?.(null);
    try {
      const res = await startRun({
        id: props.compositeId,
        steps,
        emit_paths: Array.from(props.emitSet),
        overrides: props.overrides,
      });
      setRunId(res.run_id);
      sessionStorage.setItem(ACTIVE_RUN_KEY, JSON.stringify({
        run_id: res.run_id, composite_id: props.compositeId,
      }));
      beginPolling(res.run_id);
    } catch (e: any) {
      setStartError(String(e?.message || e));
    }
  }

  const wrapStyle: React.CSSProperties = { padding: 16, fontFamily: 'system-ui, sans-serif' };

  if (inInvestigation) {
    return (
      <div style={wrapStyle}>
        <h3 style={{ marginTop: 0 }}>Run</h3>
        <p style={{ color: '#6b7280' }}>
          Use the Study&apos;s Run controls to run with this investigation&apos;s emitters.
        </p>
      </div>
    );
  }

  const pct = status && status.n_steps
    ? Math.round((status.progress_step / status.n_steps) * 100)
    : 0;

  return (
    <div style={wrapStyle}>
      <h3 style={{ marginTop: 0 }}>Run</h3>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label>
          Steps{' '}
          <input type="number" min={1} max={10000} value={steps}
                 onChange={(e) => setSteps(parseInt(e.target.value) || 1)}
                 style={{ width: 70 }} disabled={isRunning} />
        </label>
        <button onClick={handleRun} disabled={isRunning || !canRun}>
          {isRunning ? 'Running…' : 'Run'}
        </button>
        <small style={{ color: '#666' }}>
          Emit selections:{' '}
          {props.emitSet.size === 0
            ? <em>none — pick stores in the View tab</em>
            : Array.from(props.emitSet).join(', ')}
        </small>
      </div>

      {startError && (
        <div style={{ color: '#c00', marginTop: 8 }}>
          <strong>Could not start run:</strong> {startError}
        </div>
      )}

      {isRunning && status && (
        <div style={{ margin: '8px 0' }}>
          <div style={{ background: '#e5e7eb', borderRadius: 4, height: 10, overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, background: '#3b82f6', height: '100%' }} />
          </div>
          <small style={{ color: '#6b7280' }}>
            Step {status.progress_step} of {status.n_steps ?? '?'} — running detached;
            safe to reload this tab.
          </small>
        </div>
      )}
      {isRunning && !status && (
        <p style={{ color: '#6b7280' }}>Starting run…</p>
      )}

      {status && (status.status === 'failed' || status.status === 'orphaned') && (
        <div style={{ color: '#c00', marginTop: 8 }}>
          <p style={{ margin: 0 }}>
            <strong>Run {status.status}.</strong>{' '}
            {status.log_path && <span>See log: <code>{status.log_path}</code></span>}
          </p>
          {status.error && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ cursor: 'pointer', color: '#7f1d1d' }}>Show log excerpt</summary>
              <pre style={{ background: '#fef2f2', border: '1px solid #fecaca', padding: 10,
                            fontSize: 11, lineHeight: 1.4, overflow: 'auto', maxHeight: 320,
                            marginTop: 6, whiteSpace: 'pre-wrap' }}>
                {status.error.trim()}
              </pre>
            </details>
          )}
        </div>
      )}

      {status?.status === 'completed' && (
        <p style={{ color: '#6b7280', fontSize: 13, margin: '4px 0 10px' }}>
          Run complete — <strong>{status.n_steps ?? 0}</strong> steps. See the{' '}
          <strong>Results</strong> tab for emitter trajectories and the{' '}
          <strong>Visualizations</strong> tab for rendered viz output.
        </p>
      )}

      {!runId && !startError && (
        <p style={{ color: '#888' }}>
          Click <strong>Run</strong> to execute the composite for the chosen number of steps.
        </p>
      )}
    </div>
  );
}
