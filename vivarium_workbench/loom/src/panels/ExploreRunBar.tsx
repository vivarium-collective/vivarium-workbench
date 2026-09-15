// src/panels/ExploreRunBar.tsx — slim run bar pinned along the bottom of Explore.
//
// Lets you run the composite without leaving the graph. Runs with the config
// last Applied in the Config sidebar (props.overrides). Full parameter editing +
// richer run feedback live in the full-window Setup & Run tab; this bar is the
// discrete quick-run. Shares run semantics with SetupRunPanel via useCompositeRun.
import { useState } from 'react';
import { useCompositeRun, phaseLabel } from '../hooks/useCompositeRun';
import { TopoTransport } from './TopoTransport';
import { SavePointsMenu } from './SavePointsMenu';
import { exportSeries, type SeriesPanel } from '../snapshotSeries';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const fmtTime = (t: number | undefined) =>
  t == null ? '' : (Number.isInteger(t) ? String(t) : t.toFixed(2).replace(/\.?0+$/, ''));

/** Evenly spread `n` frame indices across [0, count-1] (inclusive endpoints). */
function evenFrames(count: number, n: number): number[] {
  if (count <= 0) return [];
  const k = Math.max(1, Math.min(n, count));
  if (k === 1) return [count - 1];
  const idx = Array.from({ length: k }, (_, i) => Math.round((i * (count - 1)) / (k - 1)));
  return [...new Set(idx)];
}

export interface ExploreRunBarProps {
  compositeId: string | null;
  overrides: Record<string, unknown>;
  emitSet: Set<string>;
  runContext?: string;
  defaultSteps?: number;
  runKind?: 'temporal' | 'workflow';
  readOnly?: boolean;
  onTrajectory?: (rows: Array<{ step: number; time?: number; state: Record<string, unknown> }>) => void;
  onVizHtml?: (vizHtml: Record<string, { html: string }> | null) => void;
  onCompleted?: () => void;
  onRunState?: (s: { runId: string | null; downloadable: boolean }) => void;
  // Playback transport — folded INTO this bar so run + step are one control.
  // Present (frameIdx != null) only once a run has produced a steppable trajectory.
  transport?: {
    frameIdx: number;
    frameCount: number;
    frameTime?: number;
    frameTimes?: (number | undefined)[];
    playing: boolean;
    onPrev: () => void;
    onNext: () => void;
    onToggle: () => void;
    onScrub: (i: number) => void;
    onExit: () => void;
  } | null;
  /** Top-level stores the run can record; the emit chip toggles them. */
  emitCandidates?: string[];
  onToggleEmit?: (key: string) => void;
  // Save-points: capture the current frame's state, and load a saved state back.
  captureState?: () => Record<string, unknown> | null;
  onViewState?: (state: Record<string, unknown>) => void;
  /** When the graph is collapsed, the graph-only controls (playback transport,
   *  snapshot export, Save/History) hide — they belong to the graph — leaving
   *  only the persistent strip (mode · Duration/Steps · Run · emit · status) so
   *  the run bar reads identically whether the graph is open or collapsed. */
  graphCollapsed?: boolean;
}

export function ExploreRunBar(props: ExploreRunBarProps) {
  const run = useCompositeRun({
    compositeId: props.compositeId,
    emitSet: props.emitSet,
    runContext: props.runContext,
    defaultSteps: props.defaultSteps,
    runKind: props.runKind,
    readOnly: props.readOnly,
    onTrajectory: props.onTrajectory,
    onVizHtml: props.onVizHtml,
    onCompleted: props.onCompleted,
    onRunState: props.onRunState,
    buildOverrides: () => props.overrides,
  });

  const hasTransport = !!props.transport;
  const isSteps = run.stepMode === 'steps';
  const [whyOpen, setWhyOpen] = useState(false);
  const [emitOpen, setEmitOpen] = useState(false);
  const [snapN, setSnapN] = useState(3);
  const [snapFresh, setSnapFresh] = useState(true);
  const [snapBusy, setSnapBusy] = useState<string | null>(null);

  // Export N evenly-spaced snapshots across the run as ONE side-by-side series.
  // Scrubs the transport to each chosen frame, grabs the loom's headless
  // SVG/PNG, and stitches them (labelled with each frame's TIME). Restores the
  // frame you were on when done.
  const exportSnapshots = async () => {
    const t = props.transport;
    const w = window as unknown as {
      __loomExportSvg?: () => Promise<string | null>;
      __loomExportPng?: () => Promise<string | null>;
      __loomSetFreshLayout?: (b: boolean) => void;
    };
    if (!t || !w.__loomExportSvg) return;
    const frames = evenFrames(t.frameCount, snapN);
    const original = t.frameIdx;
    const panels: SeriesPanel[] = [];
    // "clean" mode: lay out each frame fresh (a tidy tree) instead of the
    // accumulated on-screen playback arrangement.
    if (snapFresh) w.__loomSetFreshLayout?.(true);
    try {
      for (let j = 0; j < frames.length; j++) {
        const i = frames[j];
        setSnapBusy(`Rendering ${j + 1}/${frames.length}…`);
        t.onScrub(i);
        await sleep(650);                 // let the frame render + settle
        const svg = await w.__loomExportSvg();
        const png = w.__loomExportPng ? await w.__loomExportPng() : null;
        const time = t.frameTimes?.[i];
        panels.push({
          svg, png,
          label: time != null ? `t = ${fmtTime(time)}` : `frame ${i}`,
          sub: `frame ${i}/${t.frameCount - 1}`,
        });
      }
      setSnapBusy('Composing…');
      const base = (props.compositeId?.split('.').pop() || 'composite') + '-snapshots';
      await exportSeries(panels, new Set(['png', 'svg', 'zip']), base);
    } catch { /* best-effort */ } finally {
      w.__loomSetFreshLayout?.(false);
      t.onScrub(original);
      setSnapBusy(null);
    }
  };
  const emitLabel = (k: string) => k.split('.').pop() || k;
  const failText = (run.status?.status === 'failed' || run.status?.status === 'orphaned')
    ? (run.status?.error?.trim() || (run.status?.log_path ? 'See log: ' + run.status.log_path : ''))
    : '';
  const whyText = run.startError || failText;
  return (
    <div className="explore-runbar">
      {/* Kind: a step network advances in discrete integer steps (steppable one
          at a time); a temporal composite runs over continuous time. */}
      <span className={'explore-runbar-kind' + (isSteps ? ' is-steps' : ' is-time')}
        title={isSteps
          ? 'Step network — advances in discrete integer steps you can step through one at a time.'
          : 'Temporal — runs its processes over continuous time.'}>
        {isSteps ? '◇ steps' : '◷ time'}
      </span>
      <label className="explore-runbar-dur"
        title={isSteps
          ? 'Number of discrete steps to advance — set 1 to step one at a time, then use ▶❘.'
          : 'How long to advance the composite, in continuous time units.'}>
        {isSteps ? 'Steps' : 'Duration'}{' '}
        <input
          type="number"
          min={isSteps ? 1 : 0} max={10000}
          step={isSteps ? 1 : 'any'}
          value={run.steps}
          onChange={(e) => { const v = parseFloat(e.target.value); run.setSteps(Number.isFinite(v) && v > 0 ? v : 1); }}
          disabled={run.isRunning}
        />
      </label>
      <button className="sr-run-btn explore-run-btn" onClick={run.handleRun} disabled={run.isRunning || !run.canRun}>
        {run.isRunning ? 'Running…' : hasTransport ? '↻ Re-run' : (isSteps ? '▶ Run steps' : '▶ Run')}
      </button>
      {/* Stop a live run and keep whatever it has already computed (the partial
          trajectory is preserved; the run is marked "stopped"). */}
      {run.isRunning && (
        <button type="button" className="sr-stop-btn explore-stop-btn"
          onClick={run.handleStop} disabled={run.stopping}
          title="Stop the run now and keep the results computed so far">
          {run.stopping ? 'Stopping…' : '■ Stop'}
        </button>
      )}

      {/* Run + step are ONE control: once a run yields a steppable trajectory the
          playback transport lives right here in the run bar — but only while the
          graph is open (it scrubs the graph); when collapsed it hides so the
          persistent strip stays identical. */}
      {props.transport && !props.graphCollapsed && (
        <>
          <span className="explore-runbar-div" aria-hidden="true" />
          <TopoTransport {...props.transport} />
          {!props.readOnly && (
            <span className="explore-runbar-snap"
              title="Export N evenly-spaced snapshots across the run as one side-by-side series (PNG + SVG + zip), each labelled with its time">
              <input type="number" min={1} max={props.transport.frameCount}
                value={snapN} disabled={!!snapBusy}
                onChange={(e) => { const v = parseInt(e.target.value, 10); setSnapN(Number.isFinite(v) && v > 0 ? v : 1); }} />
              <label className="explore-runbar-snap-clean"
                title="Lay out each snapshot fresh (a tidy tree) instead of the on-screen playback arrangement">
                <input type="checkbox" checked={snapFresh} disabled={!!snapBusy}
                  onChange={(e) => setSnapFresh(e.target.checked)} />
                clean
              </label>
              <button type="button" onClick={() => void exportSnapshots()} disabled={!!snapBusy}>
                {snapBusy || '⤓ snapshots'}
              </button>
            </span>
          )}
        </>
      )}

      {/* Save-points: capture the current frame, browse history, view or fork.
          A graph-only control (captures the graph's current frame state), so it
          hides with the graph when collapsed. */}
      {!props.readOnly && props.captureState && !props.graphCollapsed && (
        <>
          <span className="explore-runbar-div" aria-hidden="true" />
          <SavePointsMenu
            compositeId={props.compositeId}
            captureState={props.captureState}
            currentFrame={props.transport ? props.transport.frameIdx : null}
            frameCount={props.transport ? props.transport.frameCount : null}
            onView={(s) => props.onViewState?.(s)}
            onRerun={(s) => { void run.runFromState(s); }}
            disabled={run.isRunning || !run.canRun}
          />
        </>
      )}

      {/* Inline status: progress bar while simulating, phase/label otherwise. */}
      {run.isRunning && run.status && (
        run.status.phase && run.status.phase !== 'simulating' ? (
          <span className="explore-runbar-status">
            <span className="sr-phase-dot" /> {phaseLabel(run.status.phase)}…
          </span>
        ) : (
          <span className="explore-runbar-progress" title={run.status.remote ? 'Running on the cloud — progress is reported as a phase, not per-step' : `step ${run.status.progress_step} of ${run.status.n_steps ?? '?'}`}>
            <span className="explore-runbar-track"><span className="explore-runbar-fill" style={{ width: `${run.pct}%` }} /></span>
            <span className="explore-runbar-status">
              {run.isWorkflow ? 'Running'
                : run.status.remote ? (run.status.raw_status === 'queued' ? 'Queued on cloud…' : 'Running on cloud…')
                : `step ${run.status.progress_step}/${run.status.n_steps ?? '?'}`}
            </span>
          </span>
        )
      )}
      {run.isRunning && !run.status && <span className="explore-runbar-status">Starting…</span>}
      {/* Poll-link health (f1): a degraded/dead link reads "reconnecting…" (or
          "cloud link down" for a remote run whose status proxy is unreachable)
          over the last-known progress, so the bar never looks frozen at a stale
          %. When polling gives up entirely, offer Resume. */}
      {run.isRunning && run.link !== 'ok' && !run.linkStalled && (
        <span className="explore-runbar-reconnect" title="Lost contact with the server — retrying with backoff."
          style={{ fontSize: 12, color: '#b45309', marginLeft: 4 }}>
          {run.status?.remote ? '⚠ cloud link down' : '⟳ reconnecting…'}
        </span>
      )}
      {run.linkStalled && (
        <span className="explore-runbar-reconnect" title="No response from the server for about a minute."
          style={{ fontSize: 12, color: '#b45309', marginLeft: 4 }}>
          ⚠ lost contact with the server
          <button type="button" className="explore-runbar-why" onClick={run.resumePolling}
            style={{ marginLeft: 6 }}>Resume</button>
        </span>
      )}
      {run.status?.status === 'completed' && <span className="explore-runbar-done">✓ complete</span>}
      {run.status?.status === 'cancelled' && (
        <span className="explore-runbar-stopped" title="Run stopped — the results computed up to this point are kept below.">
          ■ stopped — partial results kept
        </span>
      )}
      {(run.status?.status === 'failed' || run.status?.status === 'orphaned') && (
        <span className="explore-runbar-fail">
          Run {run.status.status}
          {(run.status.error || run.status.log_path) && (
            <button type="button" className="explore-runbar-why"
              onClick={() => setWhyOpen((o) => !o)}>{whyOpen ? 'hide' : 'why?'}</button>
          )}
        </span>
      )}
      {run.startError && <span className="explore-runbar-fail">Could not start run
        <button type="button" className="explore-runbar-why"
          onClick={() => setWhyOpen((o) => !o)}>{whyOpen ? 'hide' : 'why?'}</button>
      </span>}
      {whyOpen && whyText && (
        <div className="explore-runbar-why-detail">{whyText}</div>
      )}
      {run.startWarning && (
        <span
          title={run.startWarning}
          style={{
            fontSize: 12, color: '#b45309', background: '#fffbeb',
            border: '1px solid #f6d98a', borderRadius: 6, padding: '2px 8px',
            maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}
        >⚠ {run.startWarning}</span>
      )}

      {!run.isRunning && !run.startError && (
        run.inInvestigation ? (
          <span className="explore-runbar-emit">Running is managed by the Study controls.</span>
        ) : props.readOnly ? (
          <span className="explore-runbar-emit">Read-only preview — running requires a live dashboard.</span>
        ) : (
          <div className="explore-runbar-emit-wrap">
            <button type="button" className="explore-runbar-emit-btn"
              onClick={() => setEmitOpen((o) => !o)}
              disabled={!props.emitCandidates || props.emitCandidates.length === 0}
              title="Choose which stores to record (emit) when this composite runs">
              emit: {props.emitSet.size === 0 ? 'none' : Array.from(props.emitSet).map(emitLabel).join(', ')} ▾
            </button>
            {emitOpen && props.emitCandidates && (
              <div className="explore-runbar-emit-pop">
                <div className="explore-runbar-emit-pop-h">Record on run</div>
                {props.emitCandidates.length === 0
                  ? <div className="explore-runbar-emit-empty">No stores to record.</div>
                  : props.emitCandidates.map((k) => (
                    <label className="explore-runbar-emit-opt" key={k}>
                      <input type="checkbox" checked={props.emitSet.has(k)}
                        onChange={() => props.onToggleEmit?.(k)} />
                      {emitLabel(k)}
                    </label>
                  ))}
              </div>
            )}
          </div>
        )
      )}
    </div>
  );
}
