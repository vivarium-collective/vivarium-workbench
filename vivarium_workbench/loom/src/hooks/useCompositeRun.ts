// src/hooks/useCompositeRun.ts — composite run lifecycle (start + poll + progress).
//
// Extracted so more than one surface can drive a run of the same composite: the
// full-window Setup & Run tab (SetupRunPanel) keeps its own copy, while the
// Explore tab's bottom run bar (ExploreRunBar) uses this hook. Both share the
// same detached-run + polling semantics: a run outlives the tab, and a dropped
// poll just retries on the next tick.
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  postRunComplete, startRun, stopRun, fetchRunStatus, fetchRunTrajectory,
  type RunStatus,
} from '../api';

type TrajectoryRow = { step: number; time?: number; state: Record<string, unknown> };

const ACTIVE_RUN_KEY = 'bigraph-loom:active-run';
const POLL_MS = 1500;
// Backoff while the link to the server is degraded: 1.5s → 3s → 5s. Reset to
// POLL_MS the instant a poll succeeds. Keeps a flaky/dead link from hammering
// the server (or the cloud proxy) at 1.5s/tick.
const POLL_MS_FLAKY = 3000;
const POLL_MS_DOWN = 5000;
// After this many consecutive missed polls (~1 min at the 5s down-interval) we
// stop polling entirely and surface a Resume affordance, rather than spin
// forever against a server that is not coming back on its own.
const MAX_MISSES = 40;
// Do not resume a run this old on mount — a stale ACTIVE_RUN_KEY from a
// long-past session must not re-attach the bar to a run the server forgot.
const RESUME_MAX_AGE_MS = 24 * 60 * 60 * 1000;

/** Health of the poll link to the server, surfaced so the run bar can show
 *  "reconnecting…"/"cloud link down" instead of a frozen progress %. */
export type RunLink = 'ok' | 'flaky' | 'down';
// Cheap /status is polled every POLL_MS. The full trajectory (whole snapshot
// history) is a MUCH heavier read — seconds to minutes for a large composite —
// so while a run is live we refresh it at most this often, and never overlap a
// prior load. Fetching it every tick was what made a long run look "stuck": the
// reads piled up, starved the /status poll, and the progress/label never
// advanced even after the run had completed.
const LIVE_TRAJ_MS = 15000;

/** Human label for a run phase (backend emits lowercase stage names). */
export function phaseLabel(phase: string): string {
  return phase.charAt(0).toUpperCase() + phase.slice(1);
}

export interface UseCompositeRunArgs {
  compositeId: string | null;
  emitSet: Set<string>;
  runContext?: string;
  defaultSteps?: number;
  runKind?: 'temporal' | 'workflow';
  readOnly?: boolean;
  onTrajectory?: (rows: TrajectoryRow[]) => void;
  onVizHtml?: (vizHtml: Record<string, { html: string }> | null) => void;
  onCompleted?: () => void;
  onRunState?: (s: { runId: string | null; downloadable: boolean }) => void;
  /** Returns the config overrides to run with — evaluated at click time so the
   *  caller can hand over the latest applied/edited values. */
  buildOverrides?: () => Record<string, unknown>;
}

export function useCompositeRun(args: UseCompositeRunArgs) {
  // Step networks default to a single discrete step (advance one at a time);
  // temporal composites default to a short continuous run.
  const kindDefault = args.runKind === 'workflow' ? 1 : 5;
  const [steps, setSteps] = useState(args.defaultSteps ?? kindDefault);
  useEffect(() => {
    setSteps(args.defaultSteps ?? kindDefault);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [args.compositeId, args.defaultSteps, args.runKind]);

  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  // Non-blocking backend heads-up (e.g. a long/heavy run) — shown, not thrown.
  const [startWarning, setStartWarning] = useState<string | null>(null);
  // True from the moment Stop is clicked until the poll observes a terminal
  // status — lets the button read "Stopping…" without a spurious extra state.
  const [stopping, setStopping] = useState(false);
  // Poll-link health. 'ok' → normal 1.5s poll; 'flaky'/'down' → the bar reads
  // "reconnecting…" (or "cloud link down") over the last-known progress instead
  // of a frozen %. `linkStalled` = polling gave up after MAX_MISSES; a Resume
  // affordance restarts it.
  const [link, setLink] = useState<RunLink>('ok');
  const [linkStalled, setLinkStalled] = useState(false);
  // Self-rescheduling setTimeout (not setInterval) so the poll interval can back
  // off while the link is degraded.
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const missesRef = useRef(0);

  const inInvestigation = !!(args.runContext && args.runContext.startsWith('investigation:'));
  const canRun = !!args.compositeId && !inInvestigation && !args.readOnly;
  const isRunning = status?.status === 'running' || (!!runId && !status);
  const isWorkflow = args.runKind === 'workflow';

  // Refs so the polling closure always sees the latest callbacks without being
  // recreated (same pattern as SetupRunPanel).
  const onTrajectoryRef = useRef(args.onTrajectory);
  const onVizHtmlRef = useRef(args.onVizHtml);
  const onCompletedRef = useRef(args.onCompleted);
  const onRunStateRef = useRef(args.onRunState);
  const buildOverridesRef = useRef(args.buildOverrides);
  useEffect(() => { onTrajectoryRef.current = args.onTrajectory; }, [args.onTrajectory]);
  useEffect(() => { onVizHtmlRef.current = args.onVizHtml; }, [args.onVizHtml]);
  useEffect(() => { onCompletedRef.current = args.onCompleted; }, [args.onCompleted]);
  useEffect(() => { onRunStateRef.current = args.onRunState; }, [args.onRunState]);
  useEffect(() => { buildOverridesRef.current = args.buildOverrides; }, [args.buildOverrides]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // Guards so live trajectory refreshes never overlap or run too often. Without
  // these a slow full-history read fired every poll tick and saturated the server.
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
      /* trajectory not ready yet — next poll retries */
    } finally {
      trajInFlightRef.current = false;
    }
  }, []);

  const beginPolling = useCallback((id: string) => {
    stopPolling();
    missesRef.current = 0;
    setLink('ok');
    setLinkStalled(false);

    // Reschedule the next tick with the interval that matches current link
    // health: 1.5s while healthy, backing off to 3s/5s while degraded.
    const schedule = (l: RunLink) => {
      const delay = l === 'ok' ? POLL_MS : l === 'flaky' ? POLL_MS_FLAKY : POLL_MS_DOWN;
      pollRef.current = setTimeout(tick, delay);
    };

    const tick = async () => {
      let s: RunStatus;
      try {
        s = await fetchRunStatus(id);
      } catch (e: any) {
        // 404 → the run no longer exists (cleaned up, or the server restarted
        // and lost it). This is TERMINAL, not transient: stop polling, forget
        // the active run, and tell the user — otherwise the bar shows "running"
        // with no progress forever.
        if (e?.status === 404) {
          stopPolling();
          try { sessionStorage.removeItem(ACTIVE_RUN_KEY); } catch { /* ignore */ }
          setRunId(null);
          setStatus(null);
          setLink('ok');
          setStartWarning('This run is no longer tracked by the server (it was cleaned up or the server was restarted) — check the Runs tab.');
          return;
        }
        // Everything else is a link problem, not a run problem: keep the run id
        // and the last-known status, and back off. A cloud (remote-sim) run
        // whose status proxy returns 502 {phase:"unreachable"} is specifically a
        // cloud-link outage → jump straight to 'down' ("cloud link down") and
        // keep the run alive.
        const cloudDown = e?.status === 502 && e?.phase === 'unreachable';
        missesRef.current += 1;
        const next: RunLink = (cloudDown || missesRef.current >= 3) ? 'down' : 'flaky';
        setLink(next);
        if (missesRef.current >= MAX_MISSES) {
          // ~1 min of backoff with no recovery — stop hammering and offer Resume.
          stopPolling();
          setLink('down');
          setLinkStalled(true);
          return;
        }
        schedule(next);
        return;
      }
      // Success → the link is healthy again; reset backoff.
      missesRef.current = 0;
      setLink('ok');
      setLinkStalled(false);
      setStatus(s);
      onRunStateRef.current?.({ runId: id, downloadable: s.downloadable ?? false });
      if (s.viz_html) onVizHtmlRef.current?.(s.viz_html);
      if (s.status === 'running') {
        // Live-scrub refresh only — throttled + non-overlapping. Progress and
        // completion are driven by the cheap /status above, so the run never
        // looks stuck even when this heavy read is slow.
        void loadTrajectory(id, { throttleMs: LIVE_TRAJ_MS });
        schedule('ok');
      } else {
        stopPolling();
        setStopping(false);
        void loadTrajectory(id);  // final result — once, unthrottled
        try { sessionStorage.removeItem(ACTIVE_RUN_KEY); } catch { /* ignore */ }
        // The run is over: publish the viz result unconditionally so the panel
        // shows "no visualizations" instead of spinning on "Loading…" forever
        // when a composite declares none (or its viz step produced nothing).
        onVizHtmlRef.current?.(s.viz_html ?? {});
        if (s.status === 'completed' && args.compositeId) {
          postRunComplete(id, args.compositeId);
          onCompletedRef.current?.();
        }
      }
    };
    void tick();
  }, [stopPolling, loadTrajectory, args.compositeId]);

  // Re-attach to an in-flight run after an iframe reload / network blip.
  useEffect(() => {
    const raw = sessionStorage.getItem(ACTIVE_RUN_KEY);
    if (!raw) return;
    try {
      const saved = JSON.parse(raw) as { run_id: string; composite_id: string; started_at?: number };
      // Don't resume an ancient run: a stale ACTIVE_RUN_KEY (>24h old) is not a
      // live run the server still tracks — drop it silently so the bar starts
      // clean instead of polling a run that is long gone. (Entries written
      // before started_at existed have no timestamp → treated as not-ancient.)
      if (saved.started_at && Date.now() - saved.started_at > RESUME_MAX_AGE_MS) {
        sessionStorage.removeItem(ACTIVE_RUN_KEY);
        return;
      }
      if (saved.composite_id === args.compositeId && saved.run_id) {
        setRunId(saved.run_id);
        beginPolling(saved.run_id);
      }
    } catch {
      sessionStorage.removeItem(ACTIVE_RUN_KEY);
    }
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [args.compositeId]);

  // Core run launcher. `seedState` (save-point fork) starts the run FROM a
  // captured frame's state instead of the generator's initial state.
  const runWith = useCallback(async (seedState?: Record<string, unknown>) => {
    if (!args.compositeId) {
      setStartError('No composite id — pop-out windows need ?id=<dotted-ref> in the URL.');
      return;
    }
    setStartError(null);
    setStartWarning(null);
    setStatus(null);
    setStopping(false);
    onTrajectoryRef.current?.([]);
    onVizHtmlRef.current?.(null);
    const overrides = buildOverridesRef.current?.() ?? {};
    try {
      const res = await startRun({
        id: args.compositeId,
        // Step network → an integer number of discrete steps (steppable one at a
        // time); temporal → the continuous run length. Both ride the `steps`
        // field; a step network just constrains it to a whole number.
        steps: isWorkflow ? Math.max(1, Math.floor(steps)) : steps,
        emit_paths: Array.from(args.emitSet),
        overrides: Object.keys(overrides).length > 0 ? overrides : undefined,
        seed_state: seedState && Object.keys(seedState).length > 0 ? seedState : undefined,
      });
      setRunId(res.run_id);
      if (res.warning) setStartWarning(res.warning);
      sessionStorage.setItem(ACTIVE_RUN_KEY, JSON.stringify({
        run_id: res.run_id, composite_id: args.compositeId, started_at: Date.now(),
      }));
      beginPolling(res.run_id);
    } catch (e: unknown) {
      setStartError(String(e instanceof Error ? e.message : e));
    }
  }, [args.compositeId, args.emitSet, isWorkflow, steps, beginPolling]);

  // Param-less so it binds directly to a button onClick (whose MouseEvent arg
  // must NOT be mistaken for a seed state).
  const handleRun = useCallback(() => { void runWith(); }, [runWith]);
  const runFromState = useCallback(
    (seedState: Record<string, unknown>) => runWith(seedState), [runWith]);

  // Stop the live run and keep the results computed so far. The worker is
  // SIGTERM'd and marked `cancelled`; the next poll routes through the terminal
  // branch (stops polling + loads the final partial trajectory). Idempotent, so
  // a lost/failed request just gets reconciled by the poll loop.
  const handleStop = useCallback(() => {
    if (!runId) return;
    // A cloud (image-backed) run executes on GovCloud and can't be signalled from
    // here — sms-api exposes no simulation cancel. "Stop" DETACHES: stop watching
    // and clear the run bar; the run keeps running on the cloud and appears in the
    // Runs tab. (Honest — we never fake a 'cancelled' state for a job still running.)
    if (status?.remote) {
      stopPolling();
      setStopping(false);
      setStatus(null);
      setRunId(null);
      setLink('ok');
      setLinkStalled(false);
      sessionStorage.removeItem(ACTIVE_RUN_KEY);
      setStartWarning('Cloud runs continue on GovCloud — track this run in the Runs tab.');
      return;
    }
    setStopping(true);
    void (async () => {
      try { await stopRun(runId); }
      catch { /* already terminal or transient — the poll loop reconciles */ }
    })();
  }, [runId, status, stopPolling]);

  // Restart polling after the link stalled (MAX_MISSES with no recovery). Bound
  // to a Resume affordance so a bar that gave up on a flaky link can reattach on
  // demand instead of only on remount.
  const resumePolling = useCallback(() => {
    if (runId) beginPolling(runId);
  }, [runId, beginPolling]);

  const pct = status && status.n_steps
    ? Math.min(100, Math.round((status.progress_step / status.n_steps) * 100))
    : 0;

  return {
    steps, setSteps, runId, status, startError, startWarning, stopping,
    isRunning, isWorkflow, canRun, inInvestigation, pct, handleRun, handleStop, runFromState,
    // Poll-link health for the run bar: 'ok' | 'flaky' | 'down'. `linkStalled`
    // means polling gave up (Resume via resumePolling).
    link, linkStalled, resumePolling,
    // 'steps' = discrete step network (integer, steppable); 'duration' = temporal.
    stepMode: (isWorkflow ? 'steps' : 'duration') as 'steps' | 'duration',
  };
}
