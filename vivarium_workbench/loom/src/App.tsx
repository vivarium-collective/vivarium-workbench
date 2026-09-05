import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow, Background, Controls, ReactFlowProvider,
  useNodesState, useEdgesState, getNodesBounds, getViewportForBounds,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { toPng, toSvg, getFontEmbedCSS } from 'html-to-image';

// ProcessNode and StoreNode are default exports from the loom node modules
import ProcessNode from './nodes/ProcessNode';
import StoreNode from './nodes/StoreNode';
import FloatingStoreEdge from './edges/FloatingStoreEdge';
import LightWireEdge from './edges/LightWireEdge';
import PlaceEdge from './edges/PlaceEdge';
import BoundaryLabels from './edges/BoundaryLabels';
import { useLayoutMode } from './hooks/useLayoutMode';
import { useFocus } from './hooks/useFocus';
import { getMode } from './layouts/registry';
import { egoLayout } from './layouts/egoLayout';
import { pickDrawnEdges } from './layouts/pickDrawnEdges';
import { tierForZoom } from './layouts/processColumn';
import type { ZoomTierId } from './layouts/types';
import type { ProcessNodeData } from './types';
import { readersAndWriters, hubStoreIds } from './storeFacts';
import { deriveContract } from './contract';
import { portType } from './portInfo';
import {
  loadLayout, saveLayout, clearLayout,
  applySavedPositions, positionsFromNodes, debounce,
} from './layoutStore';
import { stateToReactFlow, defaultCollapsedIds, defaultHiddenIds, initialEmitSet } from './convert';
import { placeNewNodesNoOverlap } from './layouts/incremental';
import {
  collapseRedundantProcesses, collapseRedundantStores, processesToHyperedges, relaxHyperedgePositions,
} from './collapseRedundant';
import { prefetchInner } from './nodes/InnerCompositePreview';
import { isHiddenByAncestor, retargetEdgesToVisible, hiddenNodeIds } from './panels/filterHidden';
import ViewsMenu from './panels/ViewsMenu';
import LayoutMenu from './panels/LayoutMenu';
import DetailMenu from './panels/DetailMenu';
import { getDefaultView, decodeView, fetchView, normalizeView, shareableUrl, type View } from './viewStore';
import { isPlaybackStep } from './topoPlayback';
import { DockContainer, type DockPanelSpec } from './panels/DockContainer';
import { ProcessPanel } from './panels/ProcessPanel';
import { NodesPanel } from './panels/NodesPanel';
import { InspectorPanel } from './panels/InspectorPanel';
import { ConfigPanel } from './panels/ConfigPanel';
import { ExploreRunBar } from './panels/ExploreRunBar';
import { CompositeInfo } from './panels/CompositeInfo';
import { TopoTransport } from './panels/TopoTransport';
import { OutputsPanel } from './panels/OutputsPanel';
import { EmitContext } from './EmitContext';
import {
  postReady, postInspect, postEmitChanged, onCompositeLoad, decodeUrlComposite,
  resolveComposite, fetchInnerComposite, parseUrlOverrides,
} from './api';
import type { ExploreInspectMsg, ParameterDecl } from './api';

// Layout runs go through the mode registry (see hooks/useLayoutMode): a mode's
// run() returns { nodes, bands? }, so call sites destructure `nodes`.
//
// PERSISTENT PLACEMENT: the layout is ALWAYS computed at the largest (`full`)
// tier's card footprint, regardless of the live zoom tier. Bigger cards → there
// is always room, so smaller tiers render smaller cards in the SAME positions.
// Node POSITIONS therefore never depend on `tier` (the layout effect no longer
// lists `tier` as a dep, and does not clear the saved layout on a tier change),
// so zooming reveals card content but does NOT move nodes.
const LAYOUT_TIER: ZoomTierId = 'full';
// Per-feature Detail overrides (the Detail menu). Each feature is 'auto' (follow
// the zoom-driven tier) or forced; ports has a 3rd state for showing types. They
// layer on top of the tier-derived `show` inside the nodes.
export type PortsDetail = 'auto' | 'none' | 'plain' | 'types';
export type StoresDetail = 'auto' | 'name' | 'value' | 'type';
export type TriDetail = 'auto' | 'on' | 'off';
export type ContractDetail = 'auto' | 'on' | 'off' | 'full';
export type DetailOverrides = {
  ports: PortsDetail; stores: StoresDetail; config: TriDetail; contract: ContractDetail;
  // A per-node illustrative figure (data-URI / inline SVG on the spec's `_figure`).
  // 'auto' = show when a node carries one; 'on'/'off' force it.
  figures: TriDetail;
};
export const DETAIL_AUTO: DetailOverrides = { ports: 'auto', stores: 'auto', config: 'auto', contract: 'auto', figures: 'auto' };
const NODE_TYPES = { process: ProcessNode, store: StoreNode };
// `light` is the cheap default wire (straight, no floating anchors / labels);
// `floating` is the rich labelled edge, used only for FOCUSED wires. Non-wire
// place edges keep React Flow's default renderer.
const EDGE_TYPES = { floating: FloatingStoreEdge, light: LightWireEdge, place: PlaceEdge };

/** Bounding rect of laid-out nodes, using known node sizes (process 140×60,
 *  store 80×80) so we can frame the graph without waiting for DOM measurement. */
type TabId = 'setup' | 'outputs' | 'wiring' | 'document';

type TrajectoryRow = { step: number; time?: number; state: Record<string, unknown> };

export default function App() {
  const [state, setState] = useState<any | null>(decodeUrlComposite());
  const [selection, setSelection] = useState<Omit<ExploreInspectMsg, 'type'> | null>(null);
  // Bumped on every plain node click so the Inspector dock panel un-collapses
  // (a locked process must have a visible detail panel). See DockContainer's
  // expandRequest.
  const [inspectorReveal, setInspectorReveal] = useState(0);
  // Collapsed group-node ids — children of these nodes are filtered out of the graph.
  const [collapsed, setCollapsed] = useState<Set<string>>(
    () => defaultCollapsedIds(decodeUrlComposite()),
  );
  // Explicitly hidden node ids (via the sidebar Processes/Nodes toggles). Seeded
  // with the noisy bookkeeping processes (unique_update*/allocator_*/*listener*)
  // so the default view is clean; re-show any via the Processes sidebar.
  const [hidden, setHidden] = useState<Set<string>>(
    () => {
      // ?only=processes → a process-contract view: hide every store so only the
      // process card(s) remain (Fig 4b / Fig 7). Headless render + interactive.
      let onlyProc = false;
      try { onlyProc = new URLSearchParams(window.location.search).get('only') === 'processes'; } catch { /* no-op */ }
      return defaultHiddenIds(decodeUrlComposite(), onlyProc);
    },
  );
  // Mirror `hidden` in a ref so the (async) layout effect can read the LATEST
  // hidden set without taking it as a dependency — which would force an ELK
  // relayout on every toggle. Needed so rebuilt edges stay hidden-correct: the
  // layout effect's setEdges otherwise clobbers the [hidden] effect's flags
  // (race when applying a view that sets collapsed + hidden together → wires to
  // removed processes kept rendering).
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;
  // Guards the once-per-composite startup-view apply (declared here, up top, so
  // the postMessage composite-load handler can RESET it — a re-open of the same
  // composite must re-apply its saved default view, not keep the reset defaults).
  const startupViewRef = useRef<string | null>(null);
  // Explicit-emit store paths (joined by '/'). Descendants inherit emission.
  // Seeded from the composite's declared emit-all paths when it declares an
  // emitter step, else every top-level store (see `initialEmitSet`).
  const [emitSet, setEmitSet] = useState<Set<string>>(
    () => initialEmitSet(decodeUrlComposite()),
  );
  // Which layout mode arranges the graph, and the dispatcher that runs it.
  // Adding a mode to layouts/registry makes it selectable from the toolbar
  // with no change here.
  const layoutMode = useLayoutMode();
  // Collapse view: 'none' (both), 'stores' (process-only graph), 'processes'
  // (stores + processes shrunk to hyperedge junctions).
  // Which processes are "active" (hovered / selected / pinned). Modes that
  // implement `edgeVisibility` use this to cull wires; modes that don't
  // (hierarchy) ignore it entirely and keep drawing every edge.
  const focus = useFocus();
  // Whether the ACTIVE mode reveals edges in response to FOCUS (hover /
  // selection / pins). `layoutMode.mode` is a module-level singleton from the
  // registry, so this is stable between renders that don't switch modes.
  // Process-column mode is focus-driven; hierarchy mode culls hub wires
  // STRUCTURALLY via a toggle (its `edgeVisibility` is defined but ignores
  // focus), so its focus is inert — hover tracking, the focus hint, and pin
  // pruning stay gated off, exactly as before hierarchy gained hub-hiding.
  // `culls` (focus-culling: start with wires hidden, hover/click/pin to reveal)
  // is defined AFTER `raw` below — it now also depends on the wire-edge count so
  // small composites never cull (every mode shows all wires; `○` matches flow).
  // Hub-store wires (bulk/listeners/…) are always hidden in the overview — the
  // former "Show hub wires" toggle was removed as noise. Kept as a const so the
  // drawn-edge seam (drawFocus / culls) still reads it; focusing a process still
  // reveals its hub wires on demand.
  const showHubWires = false;
  // Initial tab honours ?tab=<id> (used by the workbench to embed a single
  // view — e.g. the Visualizations/Results panel inside the card's Outputs).
  // The surface has no tab chrome anymore — the bigraph is always shown, so `tab`
  // is pinned to 'wiring'. Kept as state only because a few internal effects still
  // reference it (fit-on-mount, topology arm). The old ?tab=/?tabs= deep-link
  // plumbing is gone with the tabs.
  const [tab, setTab] = useState<TabId>('wiring');
  // Chromeless embed mode (?chrome=off): the workbench ProcessCard already
  // provides Configure/Run/Outputs, so when embedded we hide the loom's own
  // breadcrumb + tab strip and show only the Explore (wiring) graph.
  const chromeless = (() => {
    try { return new URLSearchParams(window.location.search).get('chrome') === 'off'; } catch { return false; }
  })();
  // Embedded FULL surface (in a workbench card): show the whole stacked surface
  // but hide the loom's own breadcrumb — the card header already names the
  // composite (avoids a double header). `?header=off`.
  const hideHeader = (() => {
    try { return new URLSearchParams(window.location.search).get('header') === 'off'; } catch { return false; }
  })();
  const [compositeId, setCompositeId] = useState<string | null>(() => {
    // Bootstrap from URL query if present (for popups deep-linked with ?id=)
    const p = new URLSearchParams(window.location.search);
    return p.get('id');
  });
  // Static / view-only mode (?static=1): no dashboard server is available (e.g.
  // GitHub Pages), so show ONLY the View tab and load the composite state from a
  // committed JSON snapshot (?stateUrl=) instead of the /api/* endpoints.
  const STATIC = useMemo(
    () => new URLSearchParams(window.location.search).get('static') === '1',
    [],
  );
  // Read-only render mode: the headless figure renderer sets ?nopersist=1 so it
  // NEVER writes node positions back to the workspace. Without this, opening a
  // composite to render it re-ran auto-layout and persisted THOSE positions,
  // silently overwriting the user's hand-arranged default layout.
  const NO_PERSIST = useMemo(
    () => new URLSearchParams(window.location.search).get('nopersist') === '1',
    [],
  );
  // ?cardw=N widens process cards (App.css --proc-card-w) so a text-heavy
  // single-card figure (e.g. Fig 3b/6b contract cards) wraps less and reads
  // wider + shorter. Applied once from the URL to the document root.
  useEffect(() => {
    const w = new URLSearchParams(window.location.search).get('cardw');
    if (w && /^\d+$/.test(w)) {
      document.documentElement.style.setProperty('--proc-card-w', `${w}px`);
    }
  }, []);
  // Transparent export: with ?bg=transparent (or ?transparent=1), SVG/PNG export
  // omits the white backdrop so a figure composited onto a colored panel lets the
  // panel background show through. Default stays white.
  const exportBg = useMemo(() => {
    try {
      const p = new URLSearchParams(window.location.search);
      return (p.get('bg') === 'transparent' || p.get('transparent') === '1')
        ? undefined : '#ffffff';
    } catch { return '#ffffff'; }
  }, []);
  const [runContext, setRunContext] = useState<string>('');
  // Display metadata for the top bar — composite name + the library it's from.
  const [name, setName] = useState<string | null>(null);
  const [description, setDescription] = useState<string | null>(null);
  const [library, setLibrary] = useState<string | null>(null);
  // Composite-Process drill-down. `drillHops` is the accumulated list of node
  // paths (one per level) into successive inner composites; `drillCrumbs` the
  // matching human labels for the breadcrumb. `rootIdRef`/`rootNameRef` hold the
  // TOP composite's id + name (drilling repoints `compositeId`/`name` at the
  // inner view, but every /api/composite-inner-state call keys off the root).
  const [drillHops, setDrillHops] = useState<string[][]>([]);
  const [drillCrumbs, setDrillCrumbs] = useState<string[]>([]);
  const rootIdRef = useRef<string | null>(null);
  const rootNameRef = useRef<string | null>(null);
  // Persistent drill TABS: double-clicking a Composite Process opens it as a
  // tab that stays until closed (× ), even after switching back to the root
  // ("Super-sim"). Replaces the breadcrumb. `activeTabKey` '' = the root.
  type DrillTab = { key: string; name: string; hops: string[][] };
  const [openTabs, setOpenTabs] = useState<DrillTab[]>([]);
  const [activeTabKey, setActiveTabKey] = useState<string>('');
  // Composite parameters + current overrides (for the Configure tab).
  const [parameters, setParameters] = useState<Record<string, ParameterDecl>>({});
  // ?overrides=<json>: a study deep-links its composite with the study's real
  // config (conditions.baseline.params) as overrides. Parse once so the Configure
  // panel shows the STUDY config (not the composite's bare defaults) and the live
  // resolve builds the composite WITH it. Absent/invalid → {}.
  const urlOverrides = useMemo<Record<string, unknown>>(
    () => parseUrlOverrides(window.location.search), []);
  const [overrides, setOverrides] = useState<Record<string, unknown>>(urlOverrides);
  // From the composite_generator decorator's `default_n_steps=` argument.
  // SetupRunPanel seeds its steps input from this when a new composite loads.
  const [defaultSteps, setDefaultSteps] = useState<number | undefined>(undefined);
  // Run output, lifted up so Results / Visualizations tabs can read it.
  const [trajectory, setTrajectory] = useState<TrajectoryRow[] | null>(null);
  const [vizHtml, setVizHtml] = useState<Record<string, { html: string }> | null>(null);
  // Latest run id + downloadable flag, lifted from SetupRunPanel via onRunState.
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [downloadable, setDownloadable] = useState(false);
  // The docked Outputs panel starts collapsed (just its tab strip) so the empty
  // dock doesn't eat vertical space before a run; it auto-expands on completion.
  const [outputsOpen, setOutputsOpen] = useState(false);
  useEffect(() => {
    if (trajectory !== null || vizHtml !== null) setOutputsOpen(true);
  }, [trajectory, vizHtml]);
  // Draggable height for the Outputs dock — drag its top grip UP to see more of
  // the viz (the graph above shrinks to give it room).
  const [outputsHeight, setOutputsHeight] = useState(300);
  const outputsDragRef = useRef<{ startY: number; startH: number } | null>(null);
  const onOutputsGripDown = useCallback((e: React.PointerEvent) => {
    outputsDragRef.current = { startY: e.clientY, startH: outputsHeight };
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    e.preventDefault();
  }, [outputsHeight]);
  const onOutputsGripMove = useCallback((e: React.PointerEvent) => {
    const d = outputsDragRef.current;
    if (!d) return;
    const dy = e.clientY - d.startY;           // drag up → dy<0 → taller dock
    const h = Math.max(120, Math.min(window.innerHeight * 0.88, d.startH - dy));
    setOutputsHeight(h);
  }, []);
  const onOutputsGripUp = useCallback((e: React.PointerEvent) => {
    outputsDragRef.current = null;
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
  }, []);

  // ── Topology playback (loom-live-play) ───────────────────────────────────
  // When a run's trajectory carries a place-graph that CHANGES over steps
  // (cell division, aggregation), step the graph itself through the frames.
  // Gated to topology runs so ordinary scalar runs leave the canvas alone.
  const [frameIdx, setFrameIdx] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const baseStateRef = useRef<any>(null);
  const isTopoTraj = useMemo(() => !!trajectory && trajectory.length >= 2 &&
    trajectory.some((r) => Object.values(r.state || {}).some(
      (v) => v && typeof v === 'object' && !Array.isArray(v))), [trajectory]);
  // Refs so the (state-driven) graph-build effect can tell it's rebuilding for a
  // HISTORY frame step — then it keeps existing node positions and only places
  // NEW nodes (incremental, overlap-free) instead of re-laying out everything.
  const isTopoTrajRef = useRef(isTopoTraj);
  const frameIdxRef = useRef(frameIdx);
  useEffect(() => { isTopoTrajRef.current = isTopoTraj; }, [isTopoTraj]);
  useEffect(() => { frameIdxRef.current = frameIdx; }, [frameIdx]);
  // Set by applyView so the next layout-effect pass does a FULL apply of the
  // view's saved positions, even while a topology trajectory is armed. Without
  // it the topo-playback branch below would keep the current on-screen positions
  // and silently drop the applied view — "restore default does nothing on a live
  // run" (see applyView). Consumed (reset) once the effect applies it.
  const applyingViewRef = useRef(false);
  // Set (via window.__loomSetFreshLayout) while exporting a snapshot series in
  // "clean layout" mode: each frame is laid out FRESH (ignore both the playback
  // keep-positions branch and any saved/dragged positions), so every snapshot is
  // a tidy tree rather than the accumulated on-screen playback arrangement.
  const freshLayoutRef = useRef(false);
  // A new topology trajectory arms the transport at frame 0 (pristine state
  // captured so we can restore it on exit).
  useEffect(() => {
    if (isTopoTraj) {
      baseStateRef.current = state; setFrameIdx(0); setPlaying(false);
      setTab('wiring');   // stay on the graph so the topology is watchable
    } else { setFrameIdx(null); setPlaying(false); baseStateRef.current = null; }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trajectory, isTopoTraj]);
  // Apply the current frame: overlay its emitted stores onto the pristine
  // composite (so processes/other stores persist), preserving each store's
  // _type so a tree[node] still renders as one.
  useEffect(() => {
    if (frameIdx === null || !trajectory || !trajectory[frameIdx]) return;
    const base = baseStateRef.current || {};
    const emitted = trajectory[frameIdx].state || {};
    const frame: any = { ...base };
    for (const k of Object.keys(emitted)) {
      if (k === 'time' || k === 'global_time') continue;
      const b = (base as any)[k];
      const bt = (b && typeof b === 'object' && b._type) ? b._type : undefined;
      frame[k] = bt ? { _type: bt, ...(emitted[k] as object) } : emitted[k];
    }
    setState(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frameIdx, trajectory]);
  // Playback timer.
  useEffect(() => {
    if (!playing || frameIdx === null || !trajectory) return;
    if (frameIdx >= trajectory.length - 1) { setPlaying(false); return; }
    const id = setTimeout(() => setFrameIdx((i) => (i === null ? 0 : i + 1)), 700);
    return () => clearTimeout(id);
  }, [playing, frameIdx, trajectory]);
  const exitPlayback = useCallback(() => {
    if (baseStateRef.current !== null) setState(baseStateRef.current);
    setFrameIdx(null); setPlaying(false); baseStateRef.current = null;
  }, []);

  // ---- Save-points --------------------------------------------------------
  // Capture the state currently ON the graph: the emitted frame when a
  // trajectory is armed, else the store subtrees of the base state (skip
  // processes/steps — a save-point is a STATE snapshot, forked runs rebuild the
  // processes). Returns null when there's nothing meaningful to save.
  const captureFrameState = useCallback((): Record<string, unknown> | null => {
    if (trajectory && frameIdx != null && trajectory[frameIdx]) {
      return trajectory[frameIdx].state as Record<string, unknown>;
    }
    if (state && typeof state === 'object') {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(state as Record<string, unknown>)) {
        const o = v as { _type?: string; _control?: string } | null;
        if (o && typeof o === 'object' && !Array.isArray(o)
          && (o._type || o._control) && o._type !== 'process' && o._type !== 'step') {
          out[k] = v;
        }
      }
      return Object.keys(out).length ? out : null;
    }
    return null;
  }, [trajectory, frameIdx, state]);

  // View a saved state: overlay its stores onto whatever's loaded (preserving
  // each store's _type) and exit any active playback so the frame effect
  // doesn't overwrite it.
  const viewSavedState = useCallback((saved: Record<string, unknown>) => {
    setFrameIdx(null); setPlaying(false);
    setState((cur: unknown) => {
      const base = (cur && typeof cur === 'object') ? (cur as Record<string, unknown>) : {};
      const frame: Record<string, unknown> = { ...base };
      for (const k of Object.keys(saved)) {
        if (k === 'time' || k === 'global_time') continue;
        const b = base[k] as { _type?: string } | undefined;
        const bt = (b && typeof b === 'object' && b._type) ? b._type : undefined;
        frame[k] = bt ? { _type: bt, ...(saved[k] as object) } : saved[k];
      }
      return frame;
    });
  }, []);

  // Download the composite as JSON (name · description · parameters · state) —
  // the workbench card's "{ } JSON" action, available in the standalone loom.
  const downloadCompositeJson = useCallback(() => {
    const doc = { name, description, parameters, state };
    const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = (compositeId ? compositeId.split('.').pop()! : (name || 'composite')) + '.composite.json';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }, [name, description, parameters, state, compositeId]);

  // "Pop into" the full workbench view (Modules card + rail) for this composite.
  const openInWorkbench = useCallback(() => {
    // The loom is served under the workbench origin (/bigraph-loom/…); the
    // workbench SPA is the origin root. Carry the composite id so it can focus.
    const url = '/' + (compositeId ? ('?composite=' + encodeURIComponent(compositeId)) : '');
    window.open(url, '_blank', 'noopener');
  }, [compositeId]);

  const readyFiredRef = useRef(false);
  // React Flow instance, captured via onInit, so Re-layout can frame the
  // freshly-consolidated set (App is the ReactFlowProvider's PARENT, so it can't
  // call useReactFlow() directly). canvasWrapRef measures the viewport size so
  // we can compute the zoom deterministically.
  const rfRef = useRef<any>(null);
  const canvasWrapRef = useRef<HTMLDivElement | null>(null);

  // Use React Flow's controlled-state hooks so drag changes persist across
  // re-renders. Without these, every parent re-render would reset positions
  // to the auto-layout output.
  const [nodes, setNodes, onNodesChange] = useNodesState<any>([]);

  // Auto-frame the graph whenever the Explore tab is active and there are nodes.
  // The canvas is display:none on other tabs (zero size → fitView can't measure),
  // and the default-collapse + node mount settle a beat after the composite
  // loads, so fire fitView across a short window; the last one lands over the
  // settled (collapsed) bounds. Simpler + more reliable than gating on refs.
  const fitTab = tab === 'wiring';
  const haveNodes = nodes.length > 0;
  // Zoom-fight guard: once the user pans/zooms, a still-pending auto-fit timer
  // must NOT yank the viewport back. Set on user-initiated onMove (below);
  // reset per composite so a fresh load / drill still gets its initial fit.
  const userMovedRef = useRef(false);
  useEffect(() => { userMovedRef.current = false; }, [compositeId]);
  useEffect(() => {
    if (!fitTab || !haveNodes) return;
    const fit = () => {
      if (userMovedRef.current) return;  // user took over — don't fight them
      rfRef.current?.fitView?.({ padding: 0.2, duration: 300 });
    };
    const timers = [120, 500, 1200, 2400, 4000].map((d) => window.setTimeout(fit, d));
    return () => timers.forEach(clearTimeout);
  }, [fitTab, haveNodes, compositeId]);

  // Refit when the canvas transitions from zero size to visible. When the loom
  // is embedded (iframe) inside a hidden / display:none container — e.g. the
  // study Visualizations tab, which is `hidden` until selected — the ReactFlow
  // viewport is 0×0 for the entire timed-fit window above, so every fitView is a
  // no-op and the nodes render stacked at the origin (the "overlapping / broken"
  // symptom). A ResizeObserver catches the 0→non-zero transition when the tab is
  // finally shown and lands one more fit then. Respects userMovedRef so it never
  // fights a user who has already panned/zoomed.
  useEffect(() => {
    if (!haveNodes || typeof ResizeObserver === 'undefined') return;
    const el = canvasWrapRef.current;
    if (!el) return;
    let wasZero = el.clientWidth === 0 || el.clientHeight === 0;
    const ro = new ResizeObserver(() => {
      const isZero = el.clientWidth === 0 || el.clientHeight === 0;
      if (wasZero && !isZero && !userMovedRef.current) {
        // Let layout settle a frame, then frame the graph once.
        window.setTimeout(() => {
          if (!userMovedRef.current) rfRef.current?.fitView?.({ padding: 0.2, duration: 300 });
        }, 60);
      }
      wasZero = isZero;
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [haveNodes, compositeId]);

  // Scroll-to-zoom, handled ourselves instead of React Flow's `zoomOnScroll`
  // (disabled below). React Flow's pane-level wheel handler proved flaky in
  // some embeds/browsers (e.g. Safari in the read-only workbench iframe: the
  // wheel zoomed over a node but not over the empty pane between nodes). A
  // single non-passive listener on the canvas WRAPPER catches the wheel wherever
  // it lands — pane, edge, or node — via bubbling, so zoom is uniform and
  // standard. Zooms about the cursor (keep the point under the pointer fixed),
  // normalizes deltaMode across mouse/trackpad, and skips ctrl-wheel so pinch
  // still goes to React Flow's `zoomOnPinch`. The inner-composite mini-map stops
  // propagation + carries `nowheel`, so its own zoom is untouched.
  useEffect(() => {
    const el = canvasWrapRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey) return;                                   // pinch → React Flow
      if (!(e.target as Element)?.closest?.('.react-flow')) return; // toolbar/hint: ignore
      const inst = rfRef.current;
      if (!inst?.getViewport || !inst.setViewport) return;
      e.preventDefault();
      let dy = e.deltaY;
      if (e.deltaMode === 1) dy *= 16;                         // lines → px
      else if (e.deltaMode === 2) dy *= el.clientHeight;       // pages → px
      const { x, y, zoom } = inst.getViewport();
      const next = Math.min(12, Math.max(0.02, zoom * Math.exp(-dy * 0.0015)));
      if (next === zoom) return;
      const rect = el.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      const k = next / zoom;                                   // zoom about the cursor
      inst.setViewport({ x: px - (px - x) * k, y: py - (py - y) * k, zoom: next });
      userMovedRef.current = true;
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
    // Re-attach once the canvas is actually mounted (the wrapper renders after
    // state loads; a mount-only effect would bind to a null ref and never zoom).
  }, [haveNodes, compositeId]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);
  // Mirror `nodes` for the edge-visibility seam below (same reason hiddenRef
  // exists): dragging a node rewrites `nodes` on every animation frame, and a
  // filter that took `nodes` as a dependency would re-run — and hand React Flow
  // a brand-new edges array — on each of those frames for an identical result.
  const nodesRef = useRef<any[]>([]);
  nodesRef.current = nodes;

  // Commit a hand-set node size into APP's node state (useNodesState), so it
  // survives layout rebuilds AND gets persisted. A node's own resizer used
  // useReactFlow().setNodes, which writes React Flow's internal store but does
  // NOT flow back into this useNodesState `nodes` array — so `data._size` never
  // reached positionsFromNodes and the size silently reset on the next reload.
  const commitNodeSize = useCallback(
    (id: string, size: { width: number; height: number }) => {
      setNodes((ns: any[]) => ns.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, _size: size } } : n));
      // Flush the size to persistence IMMEDIATELY (not via the 250 ms debounce),
      // so a resize can never be lost if the user reloads/navigates right after.
      // setNodes is async, so compute the positions from the latest nodes with
      // this one node's size overridden rather than reading stale state.
      if (NO_PERSIST || !compositeId) return;
      const updated = (nodesRef.current || []).map((n: any) =>
        n.id === id ? { ...n, data: { ...n.data, _size: size } } : n);
      const positions = positionsFromNodes(updated as any);
      saveLayout(compositeId, positions, layoutMode.modeId);
      void fetch('/api/composite-layout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: compositeId, mode: layoutMode.modeId, positions }),
      }).catch(() => {});
    }, [setNodes, NO_PERSIST, compositeId, layoutMode.modeId]);

  // Commit a hand-set port-label column width (or 0 = collapse/hide the ports)
  // for one process node — same immediate-persist path as commitNodeSize, so a
  // saved/default view restores how wide each process shows its port labels.
  const commitPortCol = useCallback(
    (id: string, pc: number) => {
      setNodes((ns: any[]) => ns.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, _portCol: pc } } : n));
      if (NO_PERSIST || !compositeId) return;
      const updated = (nodesRef.current || []).map((n: any) =>
        n.id === id ? { ...n, data: { ...n.data, _portCol: pc } } : n);
      const positions = positionsFromNodes(updated as any);
      saveLayout(compositeId, positions, layoutMode.modeId);
      void fetch('/api/composite-layout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: compositeId, mode: layoutMode.modeId, positions }),
      }).catch(() => {});
    }, [setNodes, NO_PERSIST, compositeId, layoutMode.modeId]);

  // Semantic-zoom tier, driven by the live viewport zoom (process-column mode
  // only). The tier decides how much detail each process card shows AND how
  // tall it is, so the column must re-flow when it changes. `tierForZoom`'s
  // hysteresis keeps cards from flickering when the user scrolls back and forth
  // across a tier boundary.
  // The live semantic-zoom tier drives only CARD CONTENT (how much detail each
  // card reveals), NOT node positions — the layout is always computed at the
  // `full` tier (LAYOUT_TIER), so a tier change re-renders cards in place and
  // never re-runs the layout. `tieredNodes` stamps `_tier` from this for card
  // rendering; the layout effect deliberately does not depend on it.
  const [tier, setTier] = useState<ZoomTierId>('ports');
  // Manual DETAIL override (top-toolbar dropdown). `null` = Auto: the card
  // detail follows the zoom-driven `tier`. Set to a tier to PIN card detail to
  // exactly that level at ANY zoom — from `minimal` (name only) up through
  // `full` — so you can force less OR more detail than the zoom would give.
  const [detailFloor, setDetailFloor] = useState<ZoomTierId | null>(() => {
    // ?detail=<tier> pins the detail level on load (used by the headless SVG
    // renderer to force e.g. `glyph` for big array composites). Empty/invalid
    // → null (Auto, zoom-driven).
    try {
      const d = new URLSearchParams(window.location.search).get('detail');
      const valid = ['glyph', 'ports', 'types', 'config', 'contract', 'full'];
      return d && valid.includes(d) ? (d as ZoomTierId) : null;
    } catch { return null; }
  });
  const effTier: ZoomTierId = detailFloor ?? tier;
  // Per-feature Detail overrides (the Detail menu) — each 'auto' follows the
  // zoom tier; anything forced layers on top of the node's tier-derived `show`.
  const [detailOverrides, setDetailOverrides] = useState<DetailOverrides>(DETAIL_AUTO);
  // Node text scale (Font control). Multiplies every node font size via the
  // --loom-fs CSS var on the canvas; saved in the view so a headless render of a
  // default view keeps the chosen size. Clamped to a sane range.
  const [fontScale, setFontScale] = useState<number>(1);
  const bumpFont = useCallback((delta: number) =>
    setFontScale((f) => Math.min(2, Math.max(0.6, Math.round((f + delta) * 20) / 20))), []);
  // Zoom-fight fix: applying a new tier resizes every card, and doing that on
  // EVERY wheel step mid-gesture makes React Flow re-measure growing nodes while
  // the user is still zooming — which reads as the canvas shoving back / zooming
  // out. Defer the tier (card-size) change until the zoom settles (~130ms idle),
  // so zooming stays smooth and cards refine once the user pauses.
  const tierTimerRef = useRef<number | null>(null);
  const onMove = useCallback((event: unknown, vp: { zoom: number }) => {
    // Any DOM-event-driven move means the user took over (gate auto-fit).
    if (event) userMovedRef.current = true;
    if (!layoutMode.mode.tiers) return;
    if (tierTimerRef.current) clearTimeout(tierTimerRef.current);
    tierTimerRef.current = window.setTimeout(() => {
      setTier((cur) => tierForZoom(vp.zoom, cur));
    }, 130);
  }, [layoutMode.mode]);

  // Wire postMessage protocol. Use a ref guard so StrictMode's double-effect
  // doesn't fire `explore:ready` twice during dev.
  useEffect(() => {
    const off = onCompositeLoad((msg) => {
      setState(msg.state);
      // A (re)loaded composite must re-apply its saved default view — this handler
      // resets collapsed/hidden to defaults, so without re-arming the startup-view
      // guard a re-open of the SAME composite would keep the reset defaults (the
      // "default view keeps resetting" bug).
      startupViewRef.current = null;
      setCollapsed(defaultCollapsedIds(msg.state));  // light overview by default
      setHidden(defaultHiddenIds(msg.state));   // re-seed the noisy-process hide
      // Node ids are dotted paths, so a same-named path in the NEXT composite
      // would otherwise silently inherit whatever hover/selection/pins were
      // left over from this one — drop all three on every new composite.
      focus.clear();
      // Seed from the composite's declared emit-all paths when present, else
      // every top-level store, and broadcast so the dashboard's run-emit
      // selection stays in sync.
      const seeded = initialEmitSet(msg.state);
      setEmitSet(seeded);
      postEmitChanged([...seeded].sort());
      if (msg.metadata?.id) setCompositeId(msg.metadata.id);
      setRunContext(msg.metadata?.context || '');
      setName(msg.metadata?.name ?? null);
      setDescription(msg.metadata?.description ?? msg.description ?? null);
      setLibrary(msg.metadata?.library ?? null);
      setParameters(msg.parameters ?? {});
      setOverrides(msg.overrides ?? {});
      setDefaultSteps(msg.default_n_steps);
      // A new composite loaded — clear any prior run output.
      setTrajectory(null);
      setVizHtml(null);
      setActiveRunId(null);
      setDownloadable(false);
    });
    if (!readyFiredRef.current) {
      readyFiredRef.current = true;
      postReady();
    }
    return off;
  }, []);

  // Popout fallback: if the URL has ?id=<ref>, fetch state directly from the
  // server. This avoids any postMessage race with the opener — the popup
  // self-hydrates as soon as the API responds. The opener's postMessage
  // (which arrives later) is harmless because the state is already loaded.
  useEffect(() => {
    if (state) return;
    const params = new URLSearchParams(window.location.search);
    const stateUrl = params.get('stateUrl');
    // Forward the config overrides to the live state build so the GRAPH is
    // config-applied (e.g. n_generations>1 → the batch `batch_runner` wiring,
    // injected_processes wired) — matching the Configure form, which already
    // resolves with overrides. A static ?stateUrl= snapshot has no live build,
    // so it keeps its baked state.
    const ovParam = (urlOverrides && Object.keys(urlOverrides).length)
      ? '&overrides=' + encodeURIComponent(JSON.stringify(urlOverrides)) : '';
    const src = stateUrl
      ? stateUrl
      : (compositeId
          ? '/api/composite-state?ref=' + encodeURIComponent(compositeId) + ovParam
          : null);
    if (!src) return;
    let cancelled = false;
    fetch(src)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        // Accept either an /api/composite-state response ({state: ...}) or a
        // bare state object (a committed snapshot may be either shape).
        let st = (data && typeof data === 'object' && 'state' in data) ? data.state : data;
        // A discovered static composite spec is
        //   { name, description, requires, state: {...bigraph document} }
        // so the actual document is one level deeper. Unwrap it (guarded on the
        // spec markers + absence of a leaf `_type`) so the graph renders the
        // stores/processes, not the spec's metadata keys.
        if (st && typeof st === 'object' && 'state' in st && !('_type' in st) &&
            ('requires' in st || 'parameters' in st || 'description' in st)) {
          st = (st as { state: unknown }).state;
        }
        if (st && !state) {
          setState(st);
          setEmitSet(initialEmitSet(st));
          setCollapsed(defaultCollapsedIds(st));
          setHidden(defaultHiddenIds(st));
          // The published composite-state JSON is the full resolve dict — it also
          // carries the configure-form inputs. Seed them so Setup & Run renders
          // (read-only) in static mode. Absent on bare-state snapshots — tolerate.
          if (data && typeof data === 'object' && data !== st) {
            if (data.parameters) setParameters(data.parameters);
            if (data.overrides) setOverrides(data.overrides);
            if (data.default_n_steps != null) setDefaultSteps(data.default_n_steps);
            if (data.name) setName(data.name);
            if (data.description) setDescription(data.description);
            if (data.library) setLibrary(data.library);
            if (data.id) setCompositeId(data.id);
          }
          // Static (read-only) mode: also load the composite's BAKED run-viz
          // (publish copies reports/composite-viz → api/composite-viz), so the
          // Visualizations panel shows its Plotly / movie without a live run.
          // The viz JSON is the state URL's sibling; best-effort (bundles that
          // didn't bake viz simply keep the panel's "run in a live dashboard").
          if (STATIC && stateUrl) {
            const vizUrl = stateUrl.replace('composite-state', 'composite-viz');
            if (vizUrl !== stateUrl) {
              fetch(vizUrl)
                .then((r) => (r.ok ? r.json() : null))
                .then((vz) => {
                  if (!cancelled && vz && typeof vz === 'object' && Object.keys(vz).length) {
                    setVizHtml(vz as Record<string, { html: string }>);
                  }
                })
                .catch(() => { /* no baked viz for this composite */ });
            }
          }
        }
      })
      .catch(() => { /* fall through to postMessage path */ });
    return () => { cancelled = true; };
  }, [compositeId, state, urlOverrides]);

  // Standardize Setup & Run: /api/composite-state carries the wiring but NOT the
  // config, so when a composite is opened by id (live mode) and no parameters
  // arrived (state fetch above, or a postMessage), fetch /api/composite-resolve
  // so EVERY composite shows the same parameter form — not just Steps + Run.
  useEffect(() => {
    if (STATIC || !compositeId) return;
    if (Object.keys(parameters).length > 0) return;   // already have config
    let cancelled = false;
    resolveComposite(compositeId, urlOverrides)
      .then((res) => {
        if (cancelled || !res || res.error) return;
        if (res.parameters) setParameters(res.parameters);
        if (res.overrides) setOverrides(res.overrides);
        if (res.default_n_steps != null) setDefaultSteps(res.default_n_steps);
        if (res.name) setName((n) => n ?? res.name ?? null);
        if (res.description) setDescription((d) => d ?? res.description ?? null);
      })
      .catch(() => { /* leave Setup & Run with Steps + Run only */ });
    return () => { cancelled = true; };
  }, [STATIC, compositeId, parameters, urlOverrides]);

  // Debounced save of current node positions to localStorage. Built once;
  // stable across re-renders. The callback closes over `compositeId` via the
  // effect below that reads the latest nodes.
  // Positions are scoped to the active layout mode, so switching modes doesn't
  // overwrite the arrangement the user built in the other one.
  const debouncedPersistRef = useRef<
    ((id: string, positions: ReturnType<typeof positionsFromNodes>, modeId: string) => void) | null
  >(null);
  if (!debouncedPersistRef.current) {
    debouncedPersistRef.current = debounce(
      (id: string, positions: ReturnType<typeof positionsFromNodes>, modeId: string) => {
        saveLayout(id, positions, modeId);
        // Also persist to the WORKSPACE so a hand-tuned layout survives reloads
        // across sessions AND is picked up by headless renders (the study figure
        // SVGs). Best-effort — offline/static mode just keeps the local cache.
        fetch('/api/composite-layout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id, mode: modeId, positions }),
        }).catch(() => {});
      },
      250,
    );
  }

  // Walk the composite state ONCE per state change. On the whole-cell baseline
  // the state is ~6 MB / 345 nodes, so this graph walk is expensive; the layout
  // effect, the reset handler, and the sidebar lists all derive from this.
  // "Collapse repeated processes": merge topologically-identical array elements
  // (dFBA[0,0], dFBA[1,0], … → one dFBA[*] with a count). Initialised from
  // ?collapse=1 so a headless figure render can request it; also captured in
  // saved views (see captureCurrentView/applyView).
  const [collapseRedundant, setCollapseRedundant] = useState<boolean>(() => {
    try { return new URLSearchParams(window.location.search).get('collapse') === '1'; }
    catch { return false; }
  });
  // Milner view: replace processes with hyperedges over their stores.
  const [hyperedgeMode, setHyperedgeMode] = useState<boolean>(() => {
    try { return new URLSearchParams(window.location.search).get('hyperedges') === '1'; }
    catch { return false; }
  });
  const raw = useMemo(
    () => {
      let g = state ? stateToReactFlow(state) : { nodes: [] as any[], edges: [] as any[] };
      if (collapseRedundant) {
        g = collapseRedundantProcesses(g.nodes as any[], g.edges as any[]) as typeof g;
        g = collapseRedundantStores(g.nodes as any[], g.edges as any[]) as typeof g;
      }
      if (hyperedgeMode) g = processesToHyperedges(g.nodes as any[], g.edges as any[]) as typeof g;
      return g;
    },
    [state, collapseRedundant, hyperedgeMode],
  );

  // Focus-culling (hover/click/pin to reveal wiring, with the "N pinned" hint)
  // is a DENSITY tool for big graphs. Only enable it when a mode asks for it AND
  // the graph is actually large — so small composites show ALL wires in every
  // mode and `○` is consistent with the flow (↓ / →) modes.
  const wireEdgeCount = useMemo(
    () => (raw.edges as any[]).filter((e) => {
      const k = (e.data as { edgeType?: string } | undefined)?.edgeType;
      return k === 'input' || k === 'output';
    }).length,
    [raw],
  );
  const culls = !!layoutMode.mode.focusReveals && wireEdgeCount > 120;

  // (Re)generate nodes + edges whenever the composite state OR the set of
  // collapsed groups changes. Saved positions take precedence over the
  // ELK-computed positions — drags survive page reloads and collapse/expand.
  useEffect(() => {
    if (!state) {
      setNodes([]);
      setEdges([]);
      return;
    }
    let cancelled = false;

    // Collapsed groups hide their descendants (path-prefix check). ELK lays out
    // the FULL collapsed-visible set regardless of `hidden` — explicitly-hidden
    // nodes keep their slot and are toggled off via React Flow's `node.hidden`
    // CSS flag in the separate effect below (no relayout, no remount).
    const isHidden = (n: any) => {
      const path: string[] = n.data?.path ?? [];
      for (let i = 1; i < path.length; i++) {
        if (collapsed.has(path.slice(0, i).join('.'))) return true;
      }
      return false;
    };

    const visibleNodes = raw.nodes
      .filter((n) => !isHidden(n))
      .map((n) => {
        if (collapsed.has(n.id)) {
          return { ...n, data: { ...n.data, isCollapsed: true } as any };
        }
        return n;
      });
    const visibleIds = new Set(visibleNodes.map((n) => n.id));
    // Re-target wires into collapsed branches to the nearest visible ancestor
    // (so a process still shows a wire to the branch it connects into).
    const visibleEdges = retargetEdgesToVisible(raw.edges as any[], visibleIds);

    (async () => {
      let saved = loadLayout(compositeId, layoutMode.modeId);
      // Prefer WORKSPACE-persisted positions (shared across sessions + used by
      // headless renders) over the local cache; merge so any local-only nodes
      // keep their spot. Best-effort — offline/static mode uses the local cache.
      // SKIP under nopersist: a headless render must be reproducible from its
      // ?view= alone (applyView already seeded `saved`); merging the machine-local
      // server layout here would let a STALE .pbg entry (e.g. an old generator-id
      // layout) clobber a node's saved size — the "simulations kept the wrong
      // size" bug. See render_loom_svgs' reproducibility note.
      if (compositeId && !NO_PERSIST) {
        try {
          const r = await fetch(
            `/api/composite-layout?id=${encodeURIComponent(compositeId)}&mode=${encodeURIComponent(layoutMode.modeId)}`);
          if (r.ok) {
            const serverPos = (await r.json())?.positions;
            if (serverPos && Object.keys(serverPos).length) saved = { ...saved, ...serverPos };
          }
        } catch { /* no server — use local cache */ }
      }
      // Always lay out at the LARGEST (full) tier so cards never overlap at any
      // zoom and positions are stable across tier changes (persistent placement).
      const { nodes: laidOut } = await layoutMode.runLayout(
        visibleNodes as any, visibleEdges as any, compositeId, LAYOUT_TIER,
      );
      // Clean-layout snapshot export: ignore saved/dragged positions so the
      // frame lays out FRESH (a tidy tree), not the accumulated arrangement.
      const effectiveSaved = freshLayoutRef.current ? {} : saved;
      let withSaved = applySavedPositions(laidOut as any, effectiveSaved) as any[];
      // Milner view: settle each hyperedge vertex to the centroid of the ports it
      // links, so the dashed spokes fan naturally from the middle instead of from
      // the collapsed process's old corner. Pinned (saved-position) vertices stay.
      if (hyperedgeMode) {
        withSaved = relaxHyperedgePositions(
          withSaved, visibleEdges as any[], new Set(Object.keys(effectiveSaved)));
      }
      if (cancelled) return;
      // Apply the CURRENT hidden set to the freshly-rebuilt nodes + edges (read
      // via ref, not a dep). Without this, rebuilding edges here would drop the
      // [hidden] effect's flags and render wires to hidden ("removed") processes.
      const hiddenIds = hiddenNodeIds(raw.nodes as any[], hiddenRef.current);
      // Preserve object identity for nodes that didn't move (or change hidden
      // state) so React Flow does NOT unmount+remount them on collapse/expand.
      setNodes((prev: any[]) => {
        const prevById = new Map(prev.map((n) => [n.id, n]));
        // HISTORY frame step (topology playback): keep every node already on
        // screen exactly where it is, and place ONLY the new nodes into free
        // space (no overlap). A full re-layout here would jitter stable cards
        // and, mixed with saved/dragged positions, stack cards on top of each
        // other — the overlap the user sees while stepping through a run.
        // EXCEPT when applyView asked for a full apply (restore/apply a saved
        // view): then fall through so `withSaved` (the view's positions) wins,
        // instead of pinning the current on-screen mess.
        if (isPlaybackStep(
          isTopoTrajRef.current, frameIdxRef.current, prev.length, applyingViewRef.current)) {
          const merged = withSaved.map((n) => {
            const p = prevById.get(n.id);
            const h = hiddenIds.has(n.id);
            return p ? { ...p, data: n.data, hidden: h } : { ...n, hidden: h };
          });
          placeNewNodesNoOverlap(merged, new Set(prev.map((n) => n.id)));
          return merged;
        }
        return withSaved.map((n) => {
          const h = hiddenIds.has(n.id);
          const p = prevById.get(n.id);
          if (p
            && p.position?.x === n.position?.x
            && p.position?.y === n.position?.y
            && (p.hidden ?? false) === h
            && (p.data?.isCollapsed ?? false) === (n.data?.isCollapsed ?? false)) {
            // Same slot — but the node's DATA can still change (e.g. an Apply
            // Inputs edits a store value without moving anything). Keep identity
            // only when data is unchanged too; otherwise refresh data in place
            // (same id+position → React Flow updates content, no remount).
            let sameData = false;
            try { sameData = JSON.stringify(p.data) === JSON.stringify(n.data); } catch { sameData = false; }
            if (sameData) return p;
            return { ...p, data: n.data };
          }
          return { ...(p ?? n), position: n.position, data: n.data, hidden: h };
        });
      });
      setEdges(visibleEdges.map((e: any) => {
        const h = hiddenIds.has(e.source) || hiddenIds.has(e.target);
        return h ? { ...e, hidden: true } : e;
      }));
      // Consumed: a one-shot full-apply requested by applyView has now been
      // applied; later passes (frame steps) go back to keep-position playback.
      applyingViewRef.current = false;
    })();

    return () => { cancelled = true; };
    // layoutMode.modeId / runLayout: switching layout mode re-runs the layout.
    // `tier` is deliberately NOT a dep: the layout is computed at LAYOUT_TIER
    // (full) regardless of the live tier, so zooming reveals card content but
    // does not move nodes (persistent placement).
    // `hidden` is deliberately NOT a dep — see hiddenRef above.
  }, [state, raw, collapsed, compositeId, setNodes, setEdges,
      layoutMode.modeId, layoutMode.runLayout]);

  // Toggle the `hidden` CSS flag on existing nodes/edges WITHOUT relayout or
  // remount. O(changed nodes): only nodes/edges whose hidden state actually
  // flips get a new object; everything else keeps identity, so React Flow
  // leaves the DOM untouched (no ELK, no blank flash). Hidden nodes keep their
  // layout slot until the user clicks Re-layout (which re-packs).
  useEffect(() => {
    // Derive the hidden-id set from the memoized full node list (stable; has
    // data.path). Edges then hide when either endpoint is hidden.
    const hiddenIds = hiddenNodeIds(raw.nodes as any[], hidden);
    setNodes((ns: any[]) => ns.map((n) => {
      const h = hiddenIds.has(n.id);
      return (n.hidden ?? false) === h ? n : { ...n, hidden: h };
    }));
    setEdges((es: any[]) => es.map((e) => {
      const h = hiddenIds.has(e.source) || hiddenIds.has(e.target);
      return (e.hidden ?? false) === h ? e : { ...e, hidden: h };
    }));
  }, [hidden, raw, setNodes, setEdges]);

  // ?only=processes — hide EVERY store once the composite's nodes exist, so only
  // the process card(s) remain (a headless render loads the composite BY ID after
  // mount, so the initial `hidden` seed had no store ids to work with yet).
  const onlyProcesses = useMemo(() => {
    try { return new URLSearchParams(window.location.search).get('only') === 'processes'; }
    catch { return false; }
  }, []);
  useEffect(() => {
    if (!onlyProcesses) return;
    const storeIds = (raw.nodes as any[]).filter((n) => n.type === 'store').map((n) => n.id);
    if (!storeIds.length) return;
    setHidden((prev) => {
      let changed = false;
      const next = new Set(prev);
      for (const id of storeIds) if (!next.has(id)) { next.add(id); changed = true; }
      return changed ? next : prev;
    });
  }, [onlyProcesses, raw]);

  // A pinned node that then gets explicitly hidden (sidebar Processes/Nodes
  // toggle) would otherwise be unreachable — nothing on screen to shift-click
  // to un-pin it, so the focus hint keeps asserting "N pinned" over an empty
  // canvas. Prune any pin the current hidden set swallows. Gated on `culls`:
  // in hierarchy mode pins are inert, so there is nothing worth pruning.
  useEffect(() => {
    if (!culls) return;
    const hiddenIds = hiddenNodeIds(raw.nodes as any[], hidden);
    focus.prunePins((id) => !hiddenIds.has(id));
  }, [culls, hidden, raw, focus.prunePins]);

  // What actually gets drawn: the edge state, minus whatever the active layout
  // mode culls for the current focus. Hierarchy mode declares no
  // `edgeVisibility`, so it short-circuits to `edges` — same array identity,
  // same rendering as before this existed. Process-column mode drops every wire
  // that doesn't touch a focused/pinned node, leaving just the store hierarchy
  // until the user hovers something.
  //
  // Deps are all identity-stable between real changes: `focus.ctx` is memoized
  // inside useFocus, `layoutMode.mode` is a module-level singleton from the
  // registry, and the filter itself is O(edges) with no node scan. `nodes` is
  // passed through for the seam's signature but the shipped modes don't read
  // it, so it is deliberately NOT a dependency — including it would re-filter
  // on every frame of a node drag for no change in output.
  // Focus context handed to the drawn-edge seam, augmented with the hierarchy
  // "Show hub wires" toggle. Identity-stable between real changes so the memo
  // below (and hierarchy's identity-preserving edgeVisibility) don't churn.
  const drawFocus = useMemo(
    () => ({ ...focus.ctx, showHubWires }),
    [focus.ctx, showHubWires],
  );
  const drawnEdges = useMemo(
    // Only cull when `culls` is on (a focus-reveal mode AND a large graph).
    // Otherwise draw every wire — so small composites show all wiring in every
    // mode, with no "hover/pin to reveal" surprise in the `○` view.
    () => (culls
      ? pickDrawnEdges(layoutMode.mode, edges as any[], drawFocus, nodesRef.current as any[])
      : edges as any[]),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [edges, drawFocus, layoutMode.mode, culls],
  );

  /** Distinct nodes whose wiring is currently drawn (hover/selection ∪ pins). */
  const activeFocusCount = useMemo(
    () => new Set([...focus.ctx.focused, ...focus.ctx.pinned]).size,
    [focus.ctx],
  );

  // Stamp the current semantic-zoom tier (and pinned-open flag) onto each
  // process node's data so ProcessNode can gate its rows. Applies to any mode
  // with a tier ladder (process-column and hierarchy); a mode without `.tiers`
  // leaves its nodes un-stamped, so ProcessNode renders its legacy fixed card
  // and that mode is entirely unaffected. New data objects are minted only when
  // `nodes`, `tier`, or the pin set changes (useMemo), so a plain re-render or a
  // pan within a tier does not defeat the identity guarantee the layout effect's
  // setNodes reducers rely on to avoid remounting the graph.
  // Hub stores whose wire fan hierarchy mode is currently hiding. Their
  // reader/writer counts must be surfaced on the card at EVERY tier (not just
  // contract+ like ordinary stores) so the hidden fan reads as "41 read · 12
  // write" instead of vanishing. Only meaningful in hierarchy mode with the
  // toggle off; empty otherwise so process-column is untouched.
  const hubIds = useMemo(
    // Any mode that culls edges (defines edgeVisibility — Packing / Tree / Grid)
    // hides hub-store wire fans; those stores must surface their read/write count
    // at every tier so the hidden fan reads as a count, not a vanished wire. Flow
    // draws every wire (no edgeVisibility) → no hub hiding, empty set.
    () => (layoutMode.mode.edgeVisibility != null && !showHubWires
      ? hubStoreIds(edges as any[])
      : new Set<string>()),
    [layoutMode.mode.edgeVisibility, showHubWires, edges],
  );

  // Lineage spotlight: when a node is selected (plain click → focus.locked), walk
  // the PLACE graph to its containment path UP to the root plus its immediate
  // children, so selecting a node reveals "where it sits in the hierarchy and
  // what's inside it." Returns the node ids + place-edge ids on that lineage;
  // `active` is false when nothing is selected (no spotlight, nothing dimmed).
  const lineage = useMemo(() => {
    const sel = focus.locked;
    const empty = { active: false, nodes: null as Set<string> | null, edges: null as Set<string> | null, wired: null as Set<string> | null };
    if (!sel) return empty;
    const parentOf = new Map<string, string>();
    const childrenOf = new Map<string, string[]>();
    const edgeIdOf = new Map<string, string>();       // `${source}>${target}` → edge id
    const wired = new Set<string>();                  // WIRE-connected neighbours (reads/writes)
    for (const e of edges as any[]) {
      const kind = (e.data as any)?.edgeType;
      if (kind === 'place') {
        parentOf.set(e.target, e.source);
        let ch = childrenOf.get(e.source);
        if (!ch) { ch = []; childrenOf.set(e.source, ch); }
        ch.push(e.target);
        edgeIdOf.set(`${e.source}>${e.target}`, e.id);
      } else if (kind === 'input' || kind === 'output') {
        // Directly wired to the selection → its other endpoint is a neighbour.
        if (e.source === sel) wired.add(e.target);
        else if (e.target === sel) wired.add(e.source);
      }
    }
    if (!parentOf.has(sel) && !childrenOf.has(sel) && wired.size === 0) {
      return empty;                                   // nothing connected — no spotlight
    }
    const nodes = new Set<string>([sel]);
    const edgeIds = new Set<string>();
    // Ancestors: chain to the root.
    let cur = sel;
    for (let guard = 0; guard < 500 && parentOf.has(cur); guard++) {
      const p = parentOf.get(cur)!;
      if (nodes.has(p)) break;                         // cycle guard
      nodes.add(p);
      const eid = edgeIdOf.get(`${p}>${cur}`);
      if (eid) edgeIds.add(eid);
      cur = p;
    }
    // Immediate children.
    for (const c of childrenOf.get(sel) ?? []) {
      nodes.add(c);
      const eid = edgeIdOf.get(`${sel}>${c}`);
      if (eid) edgeIds.add(eid);
    }
    // A node that is BOTH contained-lineage and wired reads as lineage (blue).
    for (const id of nodes) wired.delete(id);
    return { active: true, nodes, edges: edgeIds, wired };
  }, [focus.locked, edges]);

  const tieredNodes = useMemo(() => {
    if (!layoutMode.mode.tiers) return nodes;
    // Reader/writer facts are only surfaced from the 'contract' tier up;
    // computing them for every store at every tier would be wasted work — EXCEPT
    // for hub stores, whose hidden fan must show its count at any tier.
    const wiringTier = effTier === 'contract' || effTier === 'full';
    // Lineage spotlight stamps: keep the selected node's CONTAINMENT lineage
    // (blue) and its WIRE-connected neighbours (teal) lit + raised, dim the rest.
    const lin = (id: string) => {
      if (!lineage.active) return { _dim: false, _lineage: false, _wired: false, z: undefined as number | undefined };
      const on = lineage.nodes!.has(id);
      const wired = !on && lineage.wired!.has(id);
      return {
        _dim: !on && !wired,
        _lineage: on && focus.locked !== id,
        _wired: wired,
        z: (on || wired) ? 10 : undefined,
      };
    };
    return (nodes as any[]).map((n) => {
      const L = lin(n.id);
      if (n.type === 'process') {
        return {
          ...n,
          zIndex: L.z,
          data: {
            ...n.data, _tier: effTier, _detailOverrides: detailOverrides,
            _dim: L._dim, _lineage: L._lineage, _wired: L._wired,
            // Full-detail ("open") card = explicitly kept-open ONLY. A plain
            // single click just SELECTS (drives the Inspector + wire highlight)
            // and must NOT change the card's size/detail — so the user can drag
            // and arrange without the card jumping to full detail. DOUBLE click
            // toggles keep-open (see handleNodeDoubleClick) to blow the card up
            // to full detail + full size for the current tier.
            _pinnedOpen: focus.keptOpen.has(n.id),
            _locked: focus.locked === n.id,
            // Drill context for the in-card inner-composite mini-map: the root
            // composite id + the accumulated hops to THIS view. The card appends
            // its own path to fetch its inner composite (Composite Processes).
            _rootId: rootIdRef.current,
            _hops: drillHops,
            _commitSize: commitNodeSize,
            _commitPortCol: commitPortCol,
          },
        };
      }
      const isHub = hubIds.has(n.id);
      const wiring = (wiringTier || isHub)
        ? readersAndWriters(n.id, edges as any[])
        : { readers: [], writers: [] };
      return {
        ...n,
        zIndex: L.z,
        data: {
          ...n.data, _tier: effTier, _detailOverrides: detailOverrides, _isHub: isHub,
          _dim: L._dim, _lineage: L._lineage, _wired: L._wired,
          _readers: wiring.readers, _writers: wiring.writers, _commitSize: commitNodeSize,
          _commitPortCol: commitPortCol,
        },
      };
    });
  }, [nodes, edges, effTier, detailOverrides, focus.keptOpen, focus.selected, focus.locked, lineage, layoutMode.modeId, hubIds, drillHops, commitNodeSize]);

  // Map from node id to node, for the edge stamp below (which needs the process
  // end's port-type schema and derived contract). Rebuilt only when `nodes`
  // changes identity — stable across pans within a tier.
  const nodeById = useMemo(
    () => new Map((nodes as any[]).map((n) => [n.id, n])),
    [nodes],
  );

  // Assign each DRAWN wire its renderer + (for focused wires) its label.
  //
  // PERF: the default (non-focused) fan renders through the cheap `light` edge
  // (straight, no floating-anchor recompute, no label layout) — ~110 wires that
  // used to each recompute a floating circle anchor + a label per render. Only
  // FOCUSED wires (a handful) keep the rich `floating` renderer, and only those
  // carry the tier/port/type/semantic label stamp (skipped at `glyph`). Place
  // edges keep React Flow's default renderer untouched.
  //
  // New objects are minted only when the drawn set, node map, or tier changes —
  // so a plain pan/hover-within-a-tier does not churn the edge array.
  const tieredEdges = useMemo(() => {
    // Small composites: route EVERY wire around the cards (the floating edge's
    // perpendicular port exit) so dashed wires stay trackable instead of
    // cutting straight under the process boxes. Large composites keep the cheap
    // straight `light` edge for non-focused wires (the ~400-edge perf path).
    const wireCount = (drawnEdges as any[]).filter((e) => {
      const k = (e.data as any)?.edgeType;
      return k === 'input' || k === 'output';
    }).length;
    const routeAroundAll = wireCount <= 120;
    return (drawnEdges as any[]).map((e) => {
      const kind = (e.data as any)?.edgeType;
      if (kind !== 'input' && kind !== 'output') {          // place edge: default renderer
        if (!lineage.active) return e;
        const on = lineage.edges!.has(e.id);
        // Bold + raise the selected lineage's containment edges; fade the rest.
        return { ...e, zIndex: on ? 11 : undefined, data: { ...e.data, _lineage: on, _dim: !on } };
      }
      const focused = (e.data as any)?._focused === true;
      // Non-focused wire → straight `light` edge in big graphs (perf); in small
      // graphs, route it around the cards like the focused wires do.
      if (!focused) return { ...e, type: routeAroundAll ? 'floating' : 'light' };
      // Focused wire → rich floating edge; label it in step with the cards
      // (except at glyph / non-tiered modes, where no label is drawn).
      const base = { ...e, type: 'floating' };
      if (!layoutMode.mode.tiers || effTier === 'glyph') return base;
      const isOut = kind === 'output';
      const pdata = nodeById.get(isOut ? e.source : e.target)?.data as
        ProcessNodeData | undefined;
      const port: string = (e.label as string) ?? (isOut ? e.sourceHandle : e.targetHandle) ?? '';
      const types = ((pdata as any)?.[isOut ? 'outputSchema' : 'inputSchema']) ?? {};
      const contract = pdata ? deriveContract(pdata) : null;
      return {
        ...base,
        data: {
          ...e.data, _tier: effTier, port,
          _portType: portType(types[port]) || undefined,
          _semantic: isOut ? contract?.outputs?.[port] : contract?.inputs?.[port],
        },
      };
    });
  }, [drawnEdges, nodeById, effTier, lineage, layoutMode.modeId]);

  // Persist node positions on every change. The layout effect itself sets
  // node positions; we save those too so the layout is "pinned" the first
  // time a composite renders. Subsequent drags update the same store.
  useEffect(() => {
    if (NO_PERSIST) return;   // headless render: never write layouts back
    if (!compositeId || nodes.length === 0) return;
    debouncedPersistRef.current?.(compositeId, positionsFromNodes(nodes as any), layoutMode.modeId);
  }, [nodes, compositeId, layoutMode.modeId, NO_PERSIST]);

  // ("Stack stores by depth" was retired — the flow-direction switch supersedes
  // it. `depthStackLayout` remains on disk, exercised by its unit tests.)

  // "Adjust ▸ Center on this process": one-shot — its read-only stores to the
  // left, write-only to the right, read+write below, everything else parked out
  // of frame — then fit to that ego set. The nodes stay where they land.
  const handleCenterOnProcess = useCallback((procId: string) => {
    let egoIds: string[] = [];
    setNodes((ns: any[]) => {
      const { positions, egoIds: ids } = egoLayout(ns as any, edges as any, procId);
      egoIds = ids;
      if (!positions.size) return ns;
      return ns.map((n) =>
        positions.has(n.id) ? { ...n, position: positions.get(n.id)! } : n,
      );
    });
    if (egoIds.length) {
      window.setTimeout(
        () => rfRef.current?.fitView?.({
          nodes: egoIds.map((id) => ({ id })), padding: 0.25, duration: 500,
        }),
        80,
      );
    }
  }, [edges, setNodes]);

  const handleResetLayout = useCallback(() => {
    if (!compositeId) return;
    clearLayout(compositeId, layoutMode.modeId);
    // Force a re-layout by bumping a dependency. Simplest: clear nodes so the
    // layout effect sees `!state` is false but `nodes.length === 0`, then on
    // the next state-driven tick it lays out fresh. Cleaner: just toggle
    // collapsed temporarily — but the effect already re-runs whenever
    // `compositeId` changes, and we keep `compositeId` stable. So instead,
    // we directly invoke the layout pipeline here.
    (async () => {
      const isHidden = (n: any) => {
        const path: string[] = n.data?.path ?? [];
        for (let i = 1; i < path.length; i++) {
          if (collapsed.has(path.slice(0, i).join('.'))) return true;
        }
        return isHiddenByAncestor(path, hidden);
      };
      const visibleNodes = raw.nodes
        .filter((n) => !isHidden(n))
        .map((n) =>
          collapsed.has(n.id) ? { ...n, data: { ...n.data, isCollapsed: true } as any } : n,
        );
      const visibleIds = new Set(visibleNodes.map((n) => n.id));
      const visibleEdges = retargetEdgesToVisible(raw.edges as any[], visibleIds);
      const { nodes: laidOut } = await layoutMode.runLayout(
        visibleNodes as any, visibleEdges as any, compositeId, LAYOUT_TIER,
      );
      const laid = laidOut as any[];
      // Reuse unchanged node objects so consolidating the layout doesn't remount
      // nodes that kept their position + hidden state.
      setNodes((prev: any[]) => {
        const prevById = new Map(prev.map((n) => [n.id, n]));
        return laid.map((n) => {
          const p = prevById.get(n.id);
          if (p
            && p.position?.x === n.position?.x
            && p.position?.y === n.position?.y
            && (p.hidden ?? false) === (n.hidden ?? false)) {
            return p;
          }
          return p ? { ...p, position: n.position, data: n.data } : n;
        });
      });
      setEdges(visibleEdges as any);
      window.setTimeout(
        () => rfRef.current?.fitView?.({ padding: 0.2, duration: 400 }), 120,
      );
    })();
  }, [compositeId, state, raw, collapsed, hidden, setNodes, setEdges,
      layoutMode.modeId, layoutMode.runLayout]);

  // ---- Saved views ---------------------------------------------------------
  // A "view" snapshots the current arrangement + visibility. Capturing reads the
  // live node positions plus the collapsed/hidden selections.
  const captureCurrentView = useCallback((): View => ({
    v: 1,
    positions: positionsFromNodes(nodes as any),
    collapsed: [...collapsed],
    hidden: [...hidden],
    // Record the mode the arrangement was captured in, so applying the view
    // restores the layout it was built for.
    mode: layoutMode.modeId,
    // Record the manual detail override (Detail dropdown) — null = Auto — so a
    // saved/default view restores the level of detail it was captured at.
    detail: detailFloor,
    // Record whether repeated processes were collapsed, so the view restores it.
    collapse: collapseRedundant,
    // Record the Milner (processes → hyperedges) view.
    hyperedges: hyperedgeMode,
    // Record the per-feature Detail overrides (Detail menu).
    detailOverrides,
    // Record the node text scale (Font control).
    fontScale,
  }), [nodes, collapsed, hidden, layoutMode.modeId, detailFloor, collapseRedundant, hyperedgeMode, detailOverrides, fontScale]);

  // Copy a self-contained shareable link to the CURRENT view — the whole View
  // (positions, detail mix, collapse, font) is lz-compressed into `?view=`, so
  // the link reopens the exact arrangement anywhere, including the static
  // read-only workbench (no server needed). Unmodified, this is the default view.
  const [shareCopied, setShareCopied] = useState(false);
  const onCopyViewLink = useCallback(() => {
    const url = shareableUrl(captureCurrentView());
    const done = () => { setShareCopied(true); window.setTimeout(() => setShareCopied(false), 1800); };
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(url).then(done).catch(() => window.prompt('Copy this view link:', url));
    else window.prompt('Copy this view link:', url);
  }, [captureCurrentView]);

  // Applying a view pins its positions (via the layout store, which the layout
  // effect reads) and sets collapsed/hidden — the existing effects re-lay-out
  // and toggle visibility. Then re-fit so the saved arrangement is framed.
  const applyView = useCallback((view: View) => {
    if (!compositeId || !view) return;
    // Ask the layout effect to do a FULL apply of this view's positions on its
    // next pass — otherwise, while a topology trajectory is armed, the
    // keep-position playback branch would ignore the view (restore/apply a saved
    // view "does nothing" on a live run).
    applyingViewRef.current = true;
    // Legacy views carry no mode and resolve to the default, 'hierarchy' —
    // which is the arrangement they were captured in.
    // A view can also name a mode THIS build does not register — a `?view=`
    // link or a `.view.json` file made by a newer build, neither of which is
    // validated on the way in (normalizeView only checks it is a string). Run
    // it through the registry so an unknown id falls back to the default
    // instead of desyncing the mode <select> from state and persisting
    // positions under a phantom localStorage key.
    const viewMode = getMode(view.mode).id;
    saveLayout(compositeId, view.positions || {}, viewMode);
    // ALSO overwrite the WORKSPACE-persisted layout. The layout effect prefers
    // server positions (/api/composite-layout) over the local cache, so without
    // this a node you moved AFTER saving the default would win — "restore default"
    // would appear to do nothing. Skip under nopersist (headless render).
    if (!NO_PERSIST) {
      try {
        void fetch('/api/composite-layout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: compositeId, mode: viewMode, positions: view.positions || {} }),
        });
      } catch { /* offline — localStorage is enough */ }
    }
    if (viewMode !== layoutMode.modeId) layoutMode.setModeId(viewMode);
    setCollapsed(new Set(view.collapsed || []));
    // Union the view's hidden set with the always-noise defaults (top-level
    // global_time, emitters, allocators, listeners, timing scratch). Those are
    // bookkeeping, never biology — so they must STAY hidden even when a saved or
    // bootstrapped view (captured while they were visible, e.g. hidden:[]) is
    // applied. Without this, hiding global_time never "sticks" across a reload
    // or a headless figure render — it kept reappearing.
    setHidden(new Set([...(view.hidden || []), ...defaultHiddenIds(state)]));
    // Restore the detail override (null / absent = Auto). Older views without a
    // `detail` field leave the current setting untouched? No — normalizeView
    // fills detail:null for them, so a legacy view resets to Auto, matching how
    // it actually rendered when captured (before the override existed).
    setDetailFloor((view.detail as ZoomTierId | null) ?? null);
    // Restore the collapse-repeats + Milner-hyperedge toggles (absent = off).
    setCollapseRedundant(view.collapse === true);
    setHyperedgeMode(view.hyperedges === true);
    // Restore the per-feature Detail overrides (absent = all Auto).
    setDetailOverrides({
      ports: (view.detailOverrides?.ports ?? 'auto') as PortsDetail,
      stores: (view.detailOverrides?.stores ?? 'auto') as StoresDetail,
      config: (view.detailOverrides?.config ?? 'auto') as TriDetail,
      contract: (view.detailOverrides?.contract ?? 'auto') as ContractDetail,
      figures: ((view.detailOverrides as { figures?: string } | undefined)?.figures ?? 'auto') as TriDetail,
    });
    // Restore the node text scale (absent = 1).
    setFontScale((view as { fontScale?: number }).fontScale ?? 1);
    window.setTimeout(() => rfRef.current?.fitView?.({ padding: 0.15, duration: 400 }), 240);
  }, [compositeId, state, layoutMode.modeId, layoutMode.setModeId]);

  // On open, apply a startup view ONCE per composite, in priority order:
  //   1. ?view=<encoded>   (ad-hoc shareable link)
  //   2. ?viewUrl=<url>    (committed view file — README-featured link)
  //   3. the saved default view for this composite (localStorage)
  //   4. the WORKSPACE default view (server) — the only one a headless figure
  //      render can see, so this is what makes renders match "Save as default".
  //  (startupViewRef is declared up top so the postMessage handler can reset it.)
  useEffect(() => {
    if (!state || !compositeId) return;
    if (startupViewRef.current === compositeId) return;
    startupViewRef.current = compositeId;
    (async () => {
      const params = new URLSearchParams(window.location.search);
      let view: View | null = decodeView(params.get('view'));
      const viewUrl = params.get('viewUrl');
      if (!view && viewUrl) view = await fetchView(viewUrl);
      if (!view) view = getDefaultView(compositeId);
      // Static snapshot (?static=1&stateUrl=…/composite-state/<id>.json): derive
      // the committed default view URL from the state URL so the read-only loom
      // applies the author's saved positions instead of a fresh auto-layout.
      if (!view) {
        const stateUrl = params.get('stateUrl');
        if (stateUrl && stateUrl.includes('/composite-state/')) {
          view = await fetchView(stateUrl.replace('/composite-state/', '/composite-default-view/'));
        }
      }
      if (!view) {
        try {
          const r = await fetch(
            `/api/composite-default-view?id=${encodeURIComponent(compositeId)}`);
          if (r.ok) {
            const raw = (await r.json())?.view;
            if (raw) view = normalizeView(raw);
          }
        } catch { /* offline / static — no server default */ }
      }
      if (view) applyView(view);
      // Explicit URL params OVERRIDE the applied view: a headless
      // `?hyperedges=1` / `?collapse=1` / `?detail=` render must win over a
      // default view that was saved in the opposite mode (e.g. rendering Fig 2a
      // with hyperedges from a default view saved in process mode). Applied
      // AFTER applyView, which would otherwise reset these to the view's values.
      if (params.get('hyperedges') === '1') setHyperedgeMode(true);
      if (params.get('collapse') === '1') setCollapseRedundant(true);
      const dParam = params.get('detail');
      if (dParam) setDetailFloor(dParam as ZoomTierId);
      // Node text scale from the URL (?font=1.3) — a headless render can force a
      // font size over the saved view, same as the Detail overrides above.
      const pFont = parseFloat(params.get('font') || '');
      if (pFont > 0) setFontScale(Math.min(2, Math.max(0.6, pFont)));
      // Per-feature Detail overrides from the URL (?ports= / ?config= / ?contract=)
      // so a headless render can force a specific detail mix over the saved view.
      const pPorts = params.get('ports');
      const pStores = params.get('stores');
      const pConfig = params.get('config');
      const pContract = params.get('contract');
      const pFigures = params.get('figures');
      if (pPorts || pStores || pConfig || pContract || pFigures) {
        setDetailOverrides((o) => ({
          ports: (['none', 'plain', 'types'].includes(pPorts || '') ? pPorts : o.ports) as PortsDetail,
          stores: (['name', 'value', 'type'].includes(pStores || '') ? pStores : o.stores) as StoresDetail,
          config: (['on', 'off'].includes(pConfig || '') ? pConfig : o.config) as TriDetail,
          contract: (['on', 'off', 'full'].includes(pContract || '') ? pContract : o.contract) as ContractDetail,
          figures: (['on', 'off'].includes(pFigures || '') ? pFigures : o.figures) as TriDetail,
        }));
      }
    })();
  }, [state, compositeId, applyView]);

  // Export the CURRENT layout (all nodes in their positions) to an image on a
  // WHITE background. Captures the React Flow viewport element via html-to-image,
  // framed to the full nodes bounds (not just the on-screen viewport).
  const [showExport, setShowExport] = useState(false);
  // While true, onlyRenderVisibleElements is disabled so the WHOLE graph (all
  // nodes AND edges) is in the DOM for html-to-image to capture. Otherwise the
  // off-viewport edges are culled and the export comes out wireless.
  const [exporting, setExporting] = useState(false);
  const exportImage = useCallback(async (format: 'png' | 'svg' | 'pdf') => {
    setShowExport(false);
    if (nodes.length === 0) return;
    setExporting(true);  // render everything, then wait for React Flow to paint
    await new Promise((r) => setTimeout(r, 250));
    try {
      const el = canvasWrapRef.current?.querySelector('.react-flow__viewport') as HTMLElement | null;
      if (!el) return;
      // Frame the VISIBLE nodes only. getNodesBounds does not honour the
      // `hidden` flag (fitView does), so exporting the raw node list padded the
      // image with the empty rectangle of whatever is toggled off — e.g. in
      // process-column mode the hidden bookkeeping band's ~2,096px tail. Fall
      // back to everything if the user hid literally the whole graph.
      const framed = (nodes as any[]).filter((n) => !n.hidden);
      const bounds = getNodesBounds((framed.length ? framed : nodes) as any);
      const PAD = 60, MAX = 6000;
      const rawW = bounds.width + PAD * 2, rawH = bounds.height + PAD * 2;
      // Longest-side clamp (both formats).
      let scale = Math.min(1, MAX / Math.max(rawW, rawH, 1));
      // Raster (png/pdf) additionally must fit the browser's canvas AREA limit.
      // Safari caps a canvas at ~16.78M px² — far below Chrome — so at
      // pixelRatio 2 a tall graph needs a 12000×N canvas that silently returns
      // BLANK. That's why png + pdf (pdf rasterizes via png) broke here while svg
      // (vector, no canvas) worked. Clamp so (w·pr)·(h·pr) stays Safari-safe.
      const PIXEL_RATIO = 2;
      if (format !== 'svg') {
        const RASTER_AREA_CAP = 16_000_000;
        const areaScale = Math.sqrt(
          RASTER_AREA_CAP / Math.max(1, rawW * rawH * PIXEL_RATIO * PIXEL_RATIO));
        scale = Math.min(scale, areaScale);
      }
      const w = Math.max(1, Math.ceil(rawW * scale)), h = Math.max(1, Math.ceil(rawH * scale));
      const vp = getViewportForBounds(bounds, w, h, 0.02, 4, 0.08);
      const style = {
        width: `${w}px`, height: `${h}px`,
        transform: `translate(${vp.x}px, ${vp.y}px) scale(${vp.zoom})`,
      };
      const baseName = (name || compositeId || 'composite').replace(/[^\w.-]+/g, '_');
      const grab = (url: string, ext: string) => {
        const a = document.createElement('a');
        a.href = url; a.download = `${baseName}.${ext}`; a.click();
      };
      // Pre-embed @font-face fonts (incl. KaTeX's math fonts) as data URIs so
      // the SVG-foreignObject rasterization has the glyphs — otherwise typeset
      // equations export as empty boxes (only the font-independent fraction
      // rules survive). Best-effort: a fetch failure just falls back to the
      // exporter's own embedding.
      const fontEmbedCSS = await getFontEmbedCSS(el).catch(() => undefined);
      if (format === 'svg') {
        // Re-encode the SVG as a proper UTF-8 blob with an XML prolog. The
        // data-URL html-to-image returns can be read back as Latin-1 by a
        // standalone .svg viewer, mojibake-ing every unicode math symbol
        // (∂, ∇, λ, −, —, ·). A UTF-8 blob + `encoding="UTF-8"` fixes it.
        const dataUrl = await toSvg(el, { backgroundColor: exportBg, width: w, height: h, style, fontEmbedCSS });
        const svgText = decodeURIComponent(dataUrl.slice(dataUrl.indexOf(',') + 1));
        const withProlog = svgText.startsWith('<?xml')
          ? svgText
          : '<?xml version="1.0" encoding="UTF-8"?>\n' + svgText;
        const blobUrl = URL.createObjectURL(
          new Blob([withProlog], { type: 'image/svg+xml;charset=utf-8' }),
        );
        grab(blobUrl, 'svg');
        setTimeout(() => URL.revokeObjectURL(blobUrl), 2000);
      } else {
        const png = await toPng(el, { backgroundColor: exportBg, width: w, height: h, style, pixelRatio: PIXEL_RATIO, fontEmbedCSS });
        if (format === 'png') { grab(png, 'png'); }
        else {
          const { jsPDF } = await import('jspdf');
          const pdf = new jsPDF({ orientation: w >= h ? 'landscape' : 'portrait', unit: 'px', format: [w, h] });
          pdf.addImage(png, 'PNG', 0, 0, w, h);
          pdf.save(`${baseName}.pdf`);
        }
      }
    } catch (err) {
      console.error('[bigraph-loom] export failed', err);
    } finally {
      setExporting(false);
    }
  }, [nodes, name, compositeId]);

  // Headless export hook: returns the SVG string directly (no download), so a
  // renderer (playwright) can grab it via page.evaluate() — blob-URL downloads
  // are unreliable to capture headlessly. Same framing/font-embedding as the
  // Download → svg path. Returns null if the graph has not rendered yet.
  useEffect(() => {
    // Toggle clean-layout mode for the snapshot-series export (ExploreRunBar).
    (window as any).__loomSetFreshLayout = (b: boolean) => { freshLayoutRef.current = !!b; };
    (window as any).__loomExportSvg = async (): Promise<string | null> => {
      if (nodes.length === 0) return null;
      setExporting(true);
      await new Promise((r) => setTimeout(r, 250));
      try {
        const el = canvasWrapRef.current?.querySelector('.react-flow__viewport') as HTMLElement | null;
        if (!el) return null;
        const framed = (nodes as any[]).filter((n) => !n.hidden);
        const bounds = getNodesBounds((framed.length ? framed : nodes) as any);
        const PAD = 60, MAX = 6000;
        const rawW = bounds.width + PAD * 2, rawH = bounds.height + PAD * 2;
        const scale = Math.min(1, MAX / Math.max(rawW, rawH, 1));
        const w = Math.max(1, Math.ceil(rawW * scale)), h = Math.max(1, Math.ceil(rawH * scale));
        const vp = getViewportForBounds(bounds, w, h, 0.02, 4, 0.08);
        const style = { width: `${w}px`, height: `${h}px`, transform: `translate(${vp.x}px, ${vp.y}px) scale(${vp.zoom})` };
        const fontEmbedCSS = await getFontEmbedCSS(el).catch(() => undefined);
        const dataUrl = await toSvg(el, { backgroundColor: exportBg, width: w, height: h, style, fontEmbedCSS });
        const svgText = decodeURIComponent(dataUrl.slice(dataUrl.indexOf(',') + 1));
        return svgText.startsWith('<?xml') ? svgText : '<?xml version="1.0" encoding="UTF-8"?>\n' + svgText;
      } catch (err) {
        console.error('[bigraph-loom] __loomExportSvg failed', err);
        return null;
      } finally {
        setExporting(false);
      }
    };
    // PNG twin of __loomExportSvg — same framing/font-embedding, rasterised via
    // toPng at 2× so a headless renderer can emit a .png alongside the .svg.
    // Returns a `data:image/png;base64,…` URL (or null if not yet rendered).
    (window as any).__loomExportPng = async (): Promise<string | null> => {
      if (nodes.length === 0) return null;
      setExporting(true);
      await new Promise((r) => setTimeout(r, 250));
      try {
        const el = canvasWrapRef.current?.querySelector('.react-flow__viewport') as HTMLElement | null;
        if (!el) return null;
        const framed = (nodes as any[]).filter((n) => !n.hidden);
        const bounds = getNodesBounds((framed.length ? framed : nodes) as any);
        const PAD = 60, MAX = 6000;
        const rawW = bounds.width + PAD * 2, rawH = bounds.height + PAD * 2;
        const scale = Math.min(1, MAX / Math.max(rawW, rawH, 1));
        const w = Math.max(1, Math.ceil(rawW * scale)), h = Math.max(1, Math.ceil(rawH * scale));
        const vp = getViewportForBounds(bounds, w, h, 0.02, 4, 0.08);
        const style = { width: `${w}px`, height: `${h}px`, transform: `translate(${vp.x}px, ${vp.y}px) scale(${vp.zoom})` };
        const fontEmbedCSS = await getFontEmbedCSS(el).catch(() => undefined);
        return await toPng(el, { backgroundColor: exportBg, width: w, height: h, style, pixelRatio: 2, fontEmbedCSS });
      } catch (err) {
        console.error('[bigraph-loom] __loomExportPng failed', err);
        return null;
      } finally {
        setExporting(false);
      }
    };
  }, [nodes]);

  const handleNodeClick = useCallback((ev: any, node: any) => {
    const payload = {
      path: node.data?.path ?? [],
      kind: node.type as 'store' | 'process',
      details: node.data ?? {},
    };
    setSelection(payload);
    postInspect(payload);
    // Shift/⌘-click PINS (accumulates), so two processes' wiring can be held on
    // screen and compared. A plain click LOCKS: it selects (drives the Inspector),
    // persistently highlights the node's wiring at any zoom, and — via the reveal
    // nonce below — ensures the Inspector panel is visible. Clicking another node
    // switches the lock; clicking empty canvas unlocks (see handlePaneClick).
    if (ev?.shiftKey || ev?.metaKey) {
      focus.togglePin(node.id);
    } else {
      focus.lock(node.id);
      setInspectorReveal((n) => n + 1);
    }
  }, [focus.lock, focus.togglePin]);

  // Hovering reveals a node's wiring in modes that cull edges; leaving hides it
  // again unless the node is also selected or pinned.
  const handleNodeMouseEnter = useCallback(
    (_: any, node: any) => focus.hover(node.id), [focus.hover]);
  const handleNodeMouseLeave = useCallback(() => focus.hover(null), [focus.hover]);
  // Clicking empty canvas UNLOCKS: drops the lock + selection (comparison pins
  // survive) — the way back to the clean, structure-only view.
  const handlePaneClick = useCallback(() => focus.lock(null), [focus.lock]);

  // Shift-held drag = axis lock: constrain movement to pure horizontal or pure
  // vertical from the drag origin (whichever the pointer has moved further).
  // We snapshot the start position(s) of every node in the drag (the dragged
  // node plus any co-selected nodes move together) and, while Shift is held,
  // re-pin the off-axis coordinate to its start value on each drag tick.
  const dragStartRef = useRef<Map<string, { x: number; y: number }>>(new Map());
  const handleNodeDragStart = useCallback((_ev: any, _node: any, dragged: any[]) => {
    const m = new Map<string, { x: number; y: number }>();
    for (const n of dragged ?? []) m.set(n.id, { x: n.position.x, y: n.position.y });
    dragStartRef.current = m;
  }, []);
  const handleNodeDrag = useCallback((ev: any, _node: any, dragged: any[]) => {
    if (!ev?.shiftKey) return;
    const starts = dragStartRef.current;
    const group = dragged ?? [];
    // Decide the lock axis from the leader's total displacement.
    const lead = group[0];
    const s0 = lead && starts.get(lead.id);
    if (!lead || !s0) return;
    const horizontal = Math.abs(lead.position.x - s0.x) >= Math.abs(lead.position.y - s0.y);
    rfRef.current?.setNodes?.((ns: any[]) => ns.map((n) => {
      const s = starts.get(n.id);
      if (!s) return n;
      return horizontal
        ? { ...n, position: { x: n.position.x, y: s.y } }
        : { ...n, position: { x: s.x, y: n.position.y } };
    }));
  }, []);

  // Jump the canvas to a process picked in the rail, matching handleResetLayout's
  // deterministic setCenter (with a clamped zoom). setCenter frames the node so
  // the edge-culling reveal of its wiring is actually on screen.
  const handleRailNavigate = useCallback((id: string) => {
    const n = nodes.find((x) => x.id === id);
    const inst = rfRef.current;
    if (!n || !inst) return;
    const zoom = Math.max(0.05, Math.min(1.2, inst.getZoom?.() ?? 1));
    inst.setCenter?.(n.position.x + 110, n.position.y + 30, { zoom, duration: 300 });
  }, [nodes]);

  // Keep the ROOT composite id/name current while at the top level, so drill-in
  // (which repoints compositeId/name at the inner view) can always resolve back
  // to the real generator for /api/composite-inner-state.
  useEffect(() => {
    if (drillHops.length === 0) {
      rootIdRef.current = compositeId;
      rootNameRef.current = name;
    }
  }, [compositeId, name, drillHops.length]);

  // Apply a freshly-fetched composite state (an inner drill target, or the root
  // when popping back) to the canvas — resetting the derived view sets + focus
  // exactly like a fresh composite load.
  const applyLoadedState = useCallback((st: any) => {
    setState(st);
    setCollapsed(defaultCollapsedIds(st));
    setHidden(defaultHiddenIds(st));
    const seeded = initialEmitSet(st);
    setEmitSet(seeded);
    postEmitChanged([...seeded].sort());
    focus.clear();
    setTrajectory(null);
    setVizHtml(null);
    setActiveRunId(null);
    setDownloadable(false);
  }, [focus]);

  // Drill INTO a Composite Process: fetch its inner composite and show it in
  // place, pushing a breadcrumb. Recursive — the inner view's own Composite
  // Processes are flagged too, so it works all the way down.
  const drillInto = useCallback(async (node: any) => {
    const data = node?.data as ProcessNodeData | undefined;
    if (!data?.isCompositeProcess) return;
    const root = rootIdRef.current;
    if (!root) return;
    const newHops = [...drillHops, data.path];
    try {
      const res = await fetchInnerComposite(root, newHops, urlOverrides);
      if (!res?.state) return;
      applyLoadedState(res.state);
      setDrillHops(newHops);
      setDrillCrumbs(
        res.crumbs && res.crumbs.length === newHops.length
          ? res.crumbs
          : [...drillCrumbs, data.label],
      );
      // Synthetic id so layout persistence / saved views are scoped per level.
      setCompositeId(root + ' ▸ ' + newHops.map((h) => h.join('.')).join(' ▸ '));
      setName(data.label);
      setTab('wiring');
      // Open (or focus) a persistent tab for this drilled composite.
      const key = newHops.map((h) => h.join('.')).join('▸');
      setOpenTabs((ts) => (ts.some((t) => t.key === key) ? ts : [...ts, { key, name: data.label, hops: newHops }]));
      setActiveTabKey(key);
    } catch (e) {
      console.error('[bigraph-loom] drill failed', e);
    }
  }, [drillHops, drillCrumbs, applyLoadedState, urlOverrides]);

  // Switch to a drill tab (hops:[] = the root "Super-sim"). Re-fetches that
  // level so the shown state stays consistent (cached server-side).
  const selectTab = useCallback(async (tab: DrillTab) => {
    const root = rootIdRef.current;
    if (!root) return;
    try {
      if (!tab.hops.length) {
        // Re-fetch the ROOT WITH overrides so switching back to the root tab
        // restores the config-applied top-level view (e.g. the batch's
        // batch_runner), not the bare default composite.
        const ov = (urlOverrides && Object.keys(urlOverrides).length)
          ? '&overrides=' + encodeURIComponent(JSON.stringify(urlOverrides)) : '';
        const r = await fetch('/api/composite-state?ref=' + encodeURIComponent(root) + ov);
        const data = await r.json();
        const st = (data && typeof data === 'object' && 'state' in data) ? data.state : data;
        if (!st) return;
        applyLoadedState(st);
        setDrillHops([]);
        setDrillCrumbs([]);
        setCompositeId(root);
        setName(rootNameRef.current);
      } else {
        const res = await fetchInnerComposite(root, tab.hops, urlOverrides);
        if (!res?.state) return;
        applyLoadedState(res.state);
        setDrillHops(tab.hops);
        setDrillCrumbs(res.crumbs && res.crumbs.length === tab.hops.length ? res.crumbs : tab.hops.map(() => tab.name));
        setCompositeId(root + ' ▸ ' + tab.hops.map((h) => h.join('.')).join(' ▸ '));
        setName(tab.name);
      }
      setActiveTabKey(tab.key);
      setTab('wiring');
    } catch (e) {
      console.error('[bigraph-loom] tab switch failed', e);
    }
  }, [applyLoadedState, urlOverrides]);

  // Close a drill tab; if it was active, fall back to the root "Super-sim".
  const closeTab = useCallback((key: string, e: { stopPropagation: () => void }) => {
    e.stopPropagation();
    setOpenTabs((ts) => ts.filter((t) => t.key !== key));
    setActiveTabKey((cur) => {
      if (cur !== key) return cur;
      void selectTab({ key: '', name: rootNameRef.current || 'Super-sim', hops: [] });
      return '';
    });
  }, [selectTab]);

  // Prefetch every Composite Process's inner composite in the BACKGROUND as soon
  // as the composite loads, so its in-card mini-map renders instantly when the
  // user zooms in (no "building…" flash). Deferred + best-effort; the env worker
  // is serial so these just queue. Re-runs per composite / drill level.
  useEffect(() => {
    if (STATIC) return;
    const root = drillHops.length === 0 ? compositeId : rootIdRef.current;
    if (!root) return;
    const comps = (raw.nodes as any[]).filter(
      (n) => n.type === 'process' && n.data?.isCompositeProcess,
    );
    if (!comps.length) return;
    const t = window.setTimeout(() => {
      for (const n of comps) prefetchInner(root, [...drillHops, n.data.path]);
    }, 400);
    return () => clearTimeout(t);
  }, [raw, compositeId, drillHops, STATIC]);

  const handleNodeDoubleClick = useCallback((_: any, node: any) => {
    // A Composite Process drills into its inner composite (all the way down).
    if (node.type === 'process' && (node.data as any)?.isCompositeProcess) {
      drillInto(node);
      return;
    }
    // A group store (synthesized container node) toggles collapse.
    if ((node.data as any)?.isGroup) {
      setCollapsed((prev) => {
        const next = new Set(prev);
        if (next.has(node.id)) next.delete(node.id);
        else next.add(node.id);
        return next;
      });
      return;
    }
    // Any other PROCESS: double-click blows it up to full detail + full size for
    // the current tier (single click only selects, keeping the compact card for
    // easy dragging/arrangement). Double-clicking again collapses it back.
    if (node.type === 'process') focus.toggleKeepOpen(node.id);
  }, [drillInto, focus.toggleKeepOpen]);

  const handleApplied = useCallback(
    (newOverrides: Record<string, unknown>, newState: unknown) => {
      setOverrides(newOverrides);
      setState(newState);
    },
    [setState],
  );

  // All nodes (pre-visibility-filter) for the sidebar Processes/Nodes lists, so
  // hidden nodes can still be listed and re-shown. The graph itself renders the
  // filtered `nodes` array. Derived from the single `raw` walk above.
  const allNodes = raw.nodes;
  // Top-level store nodes are the emit candidates the run-bar's emit chip toggles.
  const emitCandidates = useMemo(
    () => (allNodes as any[])
      .filter((n) => n.type === 'store' && Array.isArray(n.data?.path) && n.data.path.length === 1)
      .map((n) => n.id as string),
    [allNodes],
  );
  const toggleEmit = useCallback((key: string) => {
    setEmitSet((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      postEmitChanged([...next].sort());
      return next;
    });
  }, [setEmitSet]);

  // Run kind: a TEMPORAL composite has ≥1 time-driven Process (runs forward over
  // time on its own timestep) → Setup&Run offers a Duration. A WORKFLOW composite
  // is a Step network only (each Step runs when its inputs are ready) → it just
  // "Run"s once, no duration. Detected from the node inventory; defaults to
  // temporal when there's nothing to judge yet.
  const runKind: 'temporal' | 'workflow' = useMemo(() => {
    const hasProcess = allNodes.some((n) => n.type === 'process');
    const hasStep = allNodes.some((n) => n.type === 'step');
    return hasProcess ? 'temporal' : (hasStep ? 'workflow' : 'temporal');
  }, [allNodes]);

  const toggleHidden = useCallback((id: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const showAll = useCallback((kind: 'process' | 'store') => {
    setHidden((prev) => {
      const ids = new Set(
        allNodes.filter((n) => n.type === kind).map((n) => n.id),
      );
      const next = new Set([...prev].filter((id) => !ids.has(id)));
      return next;
    });
    // Stores also UNFOLD: clear the viewer's collapse set so the whole store
    // hierarchy expands at once (the Nodes tree's "Show all" = show + unfold).
    if (kind === 'store') setCollapsed(new Set());
  }, [allNodes]);

  // Fold/unfold a group store on the canvas from the Nodes tree (caret /
  // check-to-unfold). Idempotent: returns the same set when nothing changes.
  const setNodeCollapsed = useCallback((id: string, val: boolean) => {
    setCollapsed((prev) => {
      if (val ? prev.has(id) : !prev.has(id)) return prev;
      const next = new Set(prev);
      if (val) next.add(id); else next.delete(id);
      return next;
    });
  }, []);

  // Reveal a node's PATH: show the node and unhide + unfold its ancestors, but
  // leave siblings untouched — checking `DNA` shows DNA (and molecules > cell >
  // …) while mRNA/protein stay hidden. Off-path children of a NEWLY-revealed
  // ancestor are hidden so unfolding it doesn't dump every sibling into view;
  // ancestors already open keep their visible children. A checked GROUP also
  // unfolds so its own children appear.
  const revealPath = useCallback((nodeId: string) => {
    const childrenByParent = new Map<string, string[]>();
    for (const n of allNodes) {
      const path: string[] = n.data?.path ?? [];
      if (path.length < 1) continue;
      const parent = path.slice(0, -1).join('.');
      if (!parent) continue;                       // top-level: parent is root
      const list = childrenByParent.get(parent) ?? [];
      list.push(path.join('.'));
      childrenByParent.set(parent, list);
    }
    const parts = nodeId.split('.');
    const onPath = new Set<string>();
    for (let i = 1; i <= parts.length; i++) onPath.add(parts.slice(0, i).join('.'));

    const nextHidden = new Set(hidden);
    const nextCollapsed = new Set(collapsed);
    nextHidden.delete(nodeId);
    for (let i = 1; i < parts.length; i++) {
      const anc = parts.slice(0, i).join('.');
      const wasFolded = collapsed.has(anc) || hidden.has(anc);   // not currently showing children
      nextHidden.delete(anc);
      nextCollapsed.delete(anc);
      if (wasFolded) {
        for (const c of childrenByParent.get(anc) ?? []) {
          if (!onPath.has(c)) nextHidden.add(c);
        }
      }
    }
    // A checked group unfolds so its own children render (siblings-of-children
    // are all its children → all shown, which is the "unfold this branch" case).
    if (childrenByParent.has(nodeId)) nextCollapsed.delete(nodeId);
    setHidden(nextHidden);
    setCollapsed(nextCollapsed);
  }, [allNodes, hidden, collapsed]);

  // The three dockable panels around the canvas. Process starts LEFT; Inspector
  // and Nodes stack on the RIGHT. `render` closes over the latest props so each
  // panel always sees current selection / hidden / focus. Rebuilt when any of
  // those inputs change identity — the DockContainer just calls render().
  const dockPanels: DockPanelSpec[] = useMemo(() => ([
    {
      id: 'config',
      title: 'Configure & Inputs',
      defaultSide: 'left',
      render: () => (
        <ConfigPanel
          compositeId={compositeId}
          parameters={parameters}
          overrides={overrides}
          readOnly={STATIC}
          onApplied={handleApplied}
          state={state}
          onInputsApplied={(s) => setState(s)}
        />
      ),
    },
    {
      id: 'process',
      title: 'Processes',
      defaultSide: 'right',
      defaultCollapsed: true,
      render: () => (
        <ProcessPanel
          nodes={allNodes}
          focus={focus}
          onNavigate={handleRailNavigate}
          hidden={hidden}
          onToggleHidden={toggleHidden}
          onShowAll={showAll}
          rootId={drillHops.length === 0 ? compositeId : rootIdRef.current}
          hopsPrefix={drillHops}
        />
      ),
    },
    {
      id: 'inspector',
      title: 'Inspector',
      defaultSide: 'right',
      defaultCollapsed: true,
      render: () => (
        <InspectorPanel
          selection={selection}
          // The selected node is "locked" when it is the focus lock target
          // (node id = dotted path). Drives the lock chip in the header.
          locked={!!focus.locked && focus.locked === (selection?.path.join('.') ?? null)}
        />
      ),
    },
    {
      id: 'nodes',
      title: 'Nodes',
      defaultSide: 'right',
      defaultCollapsed: true,
      render: () => (
        <NodesPanel
          nodes={allNodes}
          hidden={hidden}
          onToggleHidden={toggleHidden}
          onShowAll={showAll}
          collapsedGroups={collapsed}
          onSetCollapsed={setNodeCollapsed}
          onRevealPath={revealPath}
          selectedId={selection?.kind === 'store' ? (selection.path.join('.') || '<root>') : null}
          revealNonce={inspectorReveal}
        />
      ),
    },
  ] as DockPanelSpec[]).filter((p) => !(chromeless && p.id === 'config')), [allNodes, focus, handleRailNavigate, hidden, toggleHidden, showAll, collapsed, setNodeCollapsed, revealPath, selection, inspectorReveal,
      compositeId, parameters, overrides, handleApplied, STATIC, chromeless, state, setState]);

  if (!state) {
    return (
      <div style={{ padding: 24, fontFamily: 'system-ui' }}>
        <h3>bigraph-loom</h3>
        <p style={{ color: '#666' }}>
          {compositeId ? `Loading composite "${compositeId}"…` : 'Waiting for composite data…'}
        </p>
        {compositeId && (
          <p style={{ color: '#888', fontSize: 12 }}>
            Fetching from <code>/api/composite-state?ref={compositeId}</code>.
            If this hangs, the dashboard server may be unreachable.
          </p>
        )}
        {!compositeId && (
          <p style={{ color: '#888', fontSize: 12 }}>
            Embed this page and post a <code>composite:load</code> message,
            or open with <code>?id=&lt;ref&gt;</code>.
          </p>
        )}
      </div>
    );
  }

  // All tabs are available in static mode too. Setup & Run renders read-only
  // (form visible, Run/Preview disabled); Results/Visualizations show a
  // read-only empty state (no run data in the snapshot).
  // The loom is a self-contained, pop-out-able STACKED SURFACE — no workbench
  // tab chrome. The bigraph leads (always visible); Configure/Inputs is the side
  // panel, Run/Step the bottom bar, and Outputs a docked panel below. Other apps
  // can embed this same surface standalone. (`tab` stays 'wiring' internally.)
  return (
    <ReactFlowProvider>
      <div style={{ display: 'flex', flexDirection: 'column', width: '100vw', height: '100vh' }}>
        {/* Composite top bar: name · id · library · counts, with an expandable
            description and a minimize toggle. The full workbench card supplies
            its own header, so the card embed (header=off) hides this one. */}
        {!chromeless && !hideHeader && (name || compositeId) && (
          <CompositeInfo
            name={name}
            description={description}
            compositeId={compositeId}
            library={library}
            nProcesses={state && typeof state === 'object'
              ? Object.values(state as Record<string, unknown>).filter(
                (v) => v && typeof v === 'object'
                  && ((v as { _type?: string })._type === 'process'
                    || (v as { _type?: string })._type === 'step')).length
              : undefined}
            nParams={Object.keys(parameters).length || undefined}
            onShare={onCopyViewLink}
            shareCopied={shareCopied}
            onDownloadJson={state ? downloadCompositeJson : undefined}
            onOpenWorkbench={compositeId ? openInWorkbench : undefined}
          />
        )}
        <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
          {/* The bigraph surface — always rendered (no tab chrome). */}
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex',
            flexDirection: 'column',
          }}>
            {/* Drill tabs: Super-sim + each opened inner composite (× to close).
                Shown once you've drilled in; visible even in chromeless embeds. */}
            {openTabs.length > 0 && (
              <div style={{
                display: 'flex', gap: 2, alignItems: 'flex-end',
                padding: '4px 8px 0', borderBottom: '1px solid #e5e7eb',
                background: '#fff', flex: '0 0 auto', overflowX: 'auto',
              }}>
                {[{ key: '', name: rootNameRef.current || 'Super-sim', hops: [] as string[][] }, ...openTabs].map((t) => {
                  const active = activeTabKey === t.key;
                  return (
                    <div
                      key={t.key || '__root'}
                      onClick={() => selectTab(t)}
                      title={t.name}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: 6,
                        padding: '5px 11px', borderRadius: '7px 7px 0 0', cursor: 'pointer', whiteSpace: 'nowrap',
                        fontSize: 13, fontWeight: active ? 600 : 400,
                        color: active ? '#2563eb' : '#6b7280',
                        background: active ? '#eff6ff' : 'transparent',
                        border: '1px solid ' + (active ? '#bfdbfe' : 'transparent'), borderBottom: 'none',
                      }}
                    >
                      <span style={{ maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.name}</span>
                      {t.key !== '' && (
                        <span
                          onClick={(e) => closeTab(t.key, e)}
                          title="Close tab"
                          style={{ color: '#9ca3af', fontSize: 15, lineHeight: 1, cursor: 'pointer' }}
                        >×</span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
            <EmitContext.Provider value={emitSet}>
              {/* Dock row (flex:1) holds the Config/Process/Inspector/Nodes panels
                  flanking the canvas; a slim run bar is pinned along the bottom so
                  the composite can be run without leaving the graph. */}
              <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'row' }}>
              <DockContainer
                panels={dockPanels}
                expandRequest={{ id: 'inspector', nonce: inspectorReveal }}
              >
              {/* Canvas column — fills the dock center. Holds the Re-layout
                  button + ReactFlow. */}
              <div
                ref={canvasWrapRef}
                className={`loom-canvas loom-mode-${layoutMode.modeId}`}
                style={{ flex: 1, position: 'relative', minWidth: 0, ['--loom-fs' as string]: fontScale } as React.CSSProperties}
              >

                {/* Modes that cull edges start with NO wires drawn, which without
                    a word of explanation reads as a broken canvas rather than a
                    deliberate clean slate. One line, only in those modes. */}
                {culls && (
                  <div className="loom-focus-hint">
                    {activeFocusCount
                      ? `highlighting wiring for ${activeFocusCount} node`
                        + `${activeFocusCount === 1 ? '' : 's'}`
                        + (focus.ctx.pinned.size ? ` (${focus.ctx.pinned.size} pinned)` : '')
                      : 'hover or click a process to highlight its wiring · shift-click to pin'}
                  </div>
                )}
                {/* Top-right toolbar: Re-layout + Download (current layout, white bg). */}
                <div style={{
                  position: 'absolute', top: 8, right: 8, zIndex: 10,
                  display: 'flex', gap: 6, alignItems: 'center',
                  // Stay WITHIN the canvas and wrap when narrow, rather than
                  // overflowing left onto the docked side panels (which would
                  // cover their collapse/dock buttons).
                  flexWrap: 'wrap', justifyContent: 'flex-end',
                  maxWidth: 'calc(100% - 16px)',
                }}>
                  {/* Layout controls grouped into one dropdown to keep the top
                      bar to Layout · Detail · Views · Download. */}
                  <LayoutMenu
                    modeId={layoutMode.modeId}
                    setModeId={layoutMode.setModeId}
                    canCenter={!!focus.locked}
                    onCenter={() => { if (focus.locked) handleCenterOnProcess(focus.locked); }}
                    collapseRedundant={collapseRedundant}
                    toggleCollapse={() => setCollapseRedundant((v) => !v)}
                    hyperedgeMode={hyperedgeMode}
                    toggleHyperedges={() => setHyperedgeMode((v) => !v)}
                    onRelayout={handleResetLayout}
                  />
                  {/* Detail: per-feature toggles (ports / config / contract),
                      each Auto = follow the zoom-driven semantic tier. */}
                  {/* Detail: per-feature card toggles + text size (folded in to
                      keep the toolbar to Layout · Detail · Share · Views · Download). */}
                  <DetailMenu overrides={detailOverrides} setOverrides={setDetailOverrides}
                    fontScale={fontScale} bumpFont={bumpFont} setFontScale={setFontScale} />
                  {/* Share lives in the composite TOP BAR now (CompositeInfo),
                      so it isn't duplicated here in the graph toolbar. */}
                  {/* The two menus sit together at the end of the toolbar. */}
                  <ViewsMenu
                    compositeId={compositeId}
                    captureCurrentView={captureCurrentView}
                    applyView={applyView}
                  />
                  <div style={{ position: 'relative' }}>
                    <button
                      onClick={() => setShowExport((v) => !v)}
                      title="Download the current layout as an image (white background)"
                      style={{
                        height: 28, padding: '0 10px', fontSize: 12,
                        display: 'inline-flex', alignItems: 'center',
                        background: '#fff', border: '1px solid #d1d5db',
                        borderRadius: 4, cursor: 'pointer', color: '#374151',
                      }}
                    >
                      Download
                    </button>
                    {showExport && (
                      <div style={{
                        position: 'absolute', top: '100%', right: 0, marginTop: 4,
                        background: '#fff', border: '1px solid #d1d5db', borderRadius: 4,
                        boxShadow: '0 2px 8px rgba(0,0,0,0.12)', overflow: 'hidden',
                        minWidth: 90,
                      }}>
                        {(['png', 'svg', 'pdf'] as const).map((fmt) => (
                          <button
                            key={fmt}
                            onClick={() => exportImage(fmt)}
                            style={{
                              display: 'block', width: '100%', textAlign: 'left',
                              padding: '6px 12px', fontSize: 12, border: 0,
                              background: '#fff', cursor: 'pointer', color: '#374151',
                            }}
                            onMouseEnter={(e) => (e.currentTarget.style.background = '#f3f4f6')}
                            onMouseLeave={(e) => (e.currentTarget.style.background = '#fff')}
                          >
                            {fmt.toUpperCase()}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
                <ReactFlow
                  nodes={tieredNodes}
                  edges={tieredEdges}
                  onInit={(inst) => { rfRef.current = inst; }}
                  onMove={onMove}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  nodeTypes={NODE_TYPES}
                  edgeTypes={EDGE_TYPES}
                  onNodeClick={handleNodeClick}
                  onNodeDoubleClick={handleNodeDoubleClick}
                  onNodeDragStart={handleNodeDragStart}
                  onNodeDrag={handleNodeDrag}
                  // Only wired up in modes that actually cull edges by focus
                  // (hierarchy mode's focus is inert): otherwise every node the
                  // pointer crosses sets state and re-renders App — which, on
                  // the wiring tab, re-renders the whole non-memoized Sidebar.
                  onNodeMouseEnter={culls ? handleNodeMouseEnter : undefined}
                  onNodeMouseLeave={culls ? handleNodeMouseLeave : undefined}
                  onPaneClick={handlePaneClick}
                  /* Framing is driven imperatively by fitGraph (which waits for
                     the default-collapse + node mount to settle). The `fitView`
                     prop fought that — it fit once on mount over the pre-collapse
                     bounds and clamped to minZoom. */
                  /* Big composites have hundreds of nodes + custom floating edges;
                     only render what's in the viewport so pan/zoom stays smooth. */
                  onlyRenderVisibleElements={!exporting && !STATIC}
                  /* Scroll-to-zoom is handled by our own wheel listener on the
                     canvas wrapper (see the effect above) — more reliable across
                     embeds/browsers than the pane-level handler. Pinch + double-
                     click zoom stay with React Flow. */
                  zoomOnScroll={false}
                  minZoom={0.02}
                  /* Allow zooming right in on a card / its inner-composite
                     mini-map, almost to a full-screen view of one process. */
                  maxZoom={12}
                  /* Read-only viewer for wiring/structure, but users CAN rearrange
                     node positions by dragging individual nodes. What's forbidden:
                     new edges, edge reconnects, and any delete. */
                  nodesDraggable
                  nodesConnectable={false}
                  edgesReconnectable={false}
                  connectOnClick={false}
                  deleteKeyCode={null}
                  /* Box-select: left-drag on empty canvas draws a selection
                     rectangle; the selected nodes then drag together. Panning
                     moves to middle/right mouse so left-drag is free for select.
                     Shift stays reserved for axis-locked node drag (above), so
                     multi-select-by-click uses Meta/Ctrl instead. */
                  selectionOnDrag
                  selectNodesOnDrag={false}
                  panOnDrag={[1, 2]}
                  multiSelectionKeyCode={['Meta', 'Control']}
                  selectionKeyCode={null}
                >
                  <Background />
                  <Controls />
                  {/* Off-screen store labels for focused wires. Only reads edges
                      stamped `_focused`, so it is inert until a process is
                      focused; re-renders live on pan/zoom via the RF store. */}
                  {culls && <BoundaryLabels edges={tieredEdges as any[]} />}
                </ReactFlow>
              </div>
              </DockContainer>
              </div>
              {/* Run + step are ONE unified control (the transport folds into the
                  run bar). Run itself is provided by the workbench card in
                  chromeless embeds, but the transport still shows there. */}
              {(() => {
                const transport = (frameIdx !== null && trajectory && trajectory.length > 1) ? {
                  frameIdx,
                  frameCount: trajectory.length,
                  frameTime: trajectory[frameIdx]?.time,
                  // Per-frame times, so the snapshot export can label each frame
                  // with WHEN it is (not just its index).
                  frameTimes: trajectory.map((r) => r.time),
                  playing,
                  onPrev: () => { setPlaying(false); setFrameIdx((i) => Math.max(0, (i ?? 0) - 1)); },
                  onNext: () => { setPlaying(false); setFrameIdx((i) => Math.min(trajectory.length - 1, (i ?? 0) + 1)); },
                  onToggle: () => setPlaying((p) => !p),
                  onScrub: (i: number) => { setPlaying(false); setFrameIdx(i); },
                  onExit: exitPlayback,
                } : null;
                return (
                  <>
                    {!chromeless && (
                      <ExploreRunBar
                        compositeId={compositeId}
                        overrides={overrides}
                        emitSet={emitSet}
                        runContext={runContext}
                        defaultSteps={defaultSteps}
                        runKind={runKind}
                        readOnly={STATIC}
                        onTrajectory={setTrajectory}
                        onVizHtml={setVizHtml}
                        onCompleted={() => { /* Outputs is docked below — always visible */ }}
                        onRunState={(s) => { setActiveRunId(s.runId); setDownloadable(s.downloadable); }}
                        transport={transport}
                        emitCandidates={emitCandidates}
                        onToggleEmit={toggleEmit}
                        captureState={captureFrameState}
                        onViewState={viewSavedState}
                      />
                    )}
                    {chromeless && transport && (
                      <div className="explore-runbar"><TopoTransport {...transport} /></div>
                    )}
                  </>
                );
              })()}
              {/* Docked OUTPUTS below the run/step bar — always part of the
                  stacked surface (drag its top edge to resize). In chromeless
                  embeds the workbench card owns Outputs, so it is hidden here. */}
              {!chromeless && (
                <div className={'loom-outputs-dock' + (outputsOpen ? '' : ' collapsed')}
                  style={outputsOpen ? { height: outputsHeight } : undefined}>
                  {outputsOpen && (
                    <div className="loom-outputs-grip"
                      onPointerDown={onOutputsGripDown}
                      onPointerMove={onOutputsGripMove}
                      onPointerUp={onOutputsGripUp}
                      title="Drag to resize — pull up to see more of the outputs" />
                  )}
                  <button className="loom-outputs-toggle" onClick={() => setOutputsOpen((o) => !o)}
                    title={outputsOpen ? 'Collapse outputs' : 'Expand outputs'}>
                    <span className="loom-outputs-chevron">{outputsOpen ? '▾' : '▸'}</span>
                    Outputs
                    {(trajectory !== null || vizHtml !== null) && !outputsOpen && (
                      <span className="loom-outputs-badge">run ready</span>
                    )}
                  </button>
                  <OutputsPanel
                    trajectory={trajectory}
                    vizHtml={vizHtml}
                    hasRun={trajectory !== null || vizHtml !== null}
                    runId={activeRunId}
                    downloadable={downloadable}
                    readOnly={STATIC}
                    baseName={compositeId ? compositeId.split('.').pop() : (name || 'composite')}
                  />
                </div>
              )}
            </EmitContext.Provider>
          </div>
        </div>
      </div>
    </ReactFlowProvider>
  );
}
