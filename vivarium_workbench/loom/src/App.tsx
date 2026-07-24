import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow, Background, Controls, ReactFlowProvider,
  useNodesState, useEdgesState, getNodesBounds, getViewportForBounds,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { toPng, toSvg } from 'html-to-image';

// ProcessNode and StoreNode are default exports from the loom node modules
import ProcessNode from './nodes/ProcessNode';
import StoreNode from './nodes/StoreNode';
import FloatingStoreEdge from './edges/FloatingStoreEdge';
import { useLayoutMode } from './hooks/useLayoutMode';
import { useFocus } from './hooks/useFocus';
import { LAYOUT_MODES, getMode } from './layouts/registry';
import { pickDrawnEdges } from './layouts/pickDrawnEdges';
import {
  loadLayout, saveLayout, clearLayout,
  applySavedPositions, positionsFromNodes, debounce,
} from './layoutStore';
import { stateToReactFlow, defaultCollapsedIds, defaultHiddenIds, initialEmitSet } from './convert';
import { isHiddenByAncestor, retargetEdgesToVisible, hiddenNodeIds } from './panels/filterHidden';
import ViewsMenu from './panels/ViewsMenu';
import { getDefaultView, decodeView, fetchView, type View } from './viewStore';
import { Sidebar } from './panels/Sidebar';
import { ProcessRail } from './panels/ProcessRail';
import { SetupRunPanel } from './panels/SetupRunPanel';
import { ResultsPanel } from './panels/ResultsPanel';
import { VisualizationsPanel } from './panels/VisualizationsPanel';
import { DocumentPanel } from './panels/DocumentPanel';
import { EmitContext } from './EmitContext';
import {
  postReady, postInspect, postEmitChanged, onCompositeLoad, decodeUrlComposite,
} from './api';
import type { ExploreInspectMsg, ParameterDecl } from './api';

// Layout runs go through the mode registry (see hooks/useLayoutMode): a mode's
// run() returns { nodes, bands? }, so call sites destructure `nodes`.
const NODE_TYPES = { process: ProcessNode, store: StoreNode };
const EDGE_TYPES = { floating: FloatingStoreEdge };

/** Bounding rect of laid-out nodes, using known node sizes (process 140×60,
 *  store 80×80) so we can frame the graph without waiting for DOM measurement. */
function boundsOf(nodes: any[]): { x: number; y: number; width: number; height: number } | null {
  if (!nodes.length) return null;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of nodes) {
    const x = n.position?.x ?? 0;
    const y = n.position?.y ?? 0;
    const w = n.type === 'process' ? 140 : 80;
    const h = n.type === 'process' ? 60 : 80;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x + w > maxX) maxX = x + w;
    if (y + h > maxY) maxY = y + h;
  }
  if (!isFinite(minX)) return null;
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

type TabId = 'setup' | 'results' | 'visualizations' | 'wiring' | 'document';

type TrajectoryRow = { step: number; time?: number; state: Record<string, unknown> };

export default function App() {
  const [state, setState] = useState<any | null>(decodeUrlComposite());
  const [selection, setSelection] = useState<Omit<ExploreInspectMsg, 'type'> | null>(null);
  // Collapsed group-node ids — children of these nodes are filtered out of the graph.
  const [collapsed, setCollapsed] = useState<Set<string>>(
    () => defaultCollapsedIds(decodeUrlComposite()),
  );
  // Explicitly hidden node ids (via the sidebar Processes/Nodes toggles). Seeded
  // with the noisy bookkeeping processes (unique_update*/allocator_*/*listener*)
  // so the default view is clean; re-show any via the Processes sidebar.
  const [hidden, setHidden] = useState<Set<string>>(
    () => defaultHiddenIds(decodeUrlComposite()),
  );
  // Mirror `hidden` in a ref so the (async) layout effect can read the LATEST
  // hidden set without taking it as a dependency — which would force an ELK
  // relayout on every toggle. Needed so rebuilt edges stay hidden-correct: the
  // layout effect's setEdges otherwise clobbers the [hidden] effect's flags
  // (race when applying a view that sets collapsed + hidden together → wires to
  // removed processes kept rendering).
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;
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
  // Which processes are "active" (hovered / selected / pinned). Modes that
  // implement `edgeVisibility` use this to cull wires; modes that don't
  // (hierarchy) ignore it entirely and keep drawing every edge.
  const focus = useFocus();
  // Whether the ACTIVE mode culls edges by focus. `layoutMode.mode` is a
  // module-level singleton from the registry, so this is stable between
  // renders that don't switch modes. In hierarchy mode (no edgeVisibility)
  // focus is entirely inert, so hover tracking + pin pruning are gated on
  // this to keep hierarchy mode paying nothing for a feature it never uses.
  const culls = !!layoutMode.mode.edgeVisibility;
  const [tab, setTab] = useState<TabId>('setup');
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
  const [runContext, setRunContext] = useState<string>('');
  // Display metadata for the top bar — composite name + the library it's from.
  const [name, setName] = useState<string | null>(null);
  const [library, setLibrary] = useState<string | null>(null);
  // Composite parameters + current overrides (for the Configure tab).
  const [parameters, setParameters] = useState<Record<string, ParameterDecl>>({});
  const [overrides, setOverrides] = useState<Record<string, unknown>>({});
  // From the composite_generator decorator's `default_n_steps=` argument.
  // SetupRunPanel seeds its steps input from this when a new composite loads.
  const [defaultSteps, setDefaultSteps] = useState<number | undefined>(undefined);
  // Run output, lifted up so Results / Visualizations tabs can read it.
  const [trajectory, setTrajectory] = useState<TrajectoryRow[] | null>(null);
  const [vizHtml, setVizHtml] = useState<Record<string, { html: string }> | null>(null);
  // Latest run id + downloadable flag, lifted from SetupRunPanel via onRunState.
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [downloadable, setDownloadable] = useState(false);
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
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);
  // Mirror `nodes` for the edge-visibility seam below (same reason hiddenRef
  // exists): dragging a node rewrites `nodes` on every animation frame, and a
  // filter that took `nodes` as a dependency would re-run — and hand React Flow
  // a brand-new edges array — on each of those frames for an identical result.
  const nodesRef = useRef<any[]>([]);
  nodesRef.current = nodes;

  // Wire postMessage protocol. Use a ref guard so StrictMode's double-effect
  // doesn't fire `explore:ready` twice during dev.
  useEffect(() => {
    const off = onCompositeLoad((msg) => {
      setState(msg.state);
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
    // Prefer a static state snapshot (?stateUrl=) — the only source available on
    // GitHub Pages; otherwise the dashboard's /api/composite-state by ref.
    const src = stateUrl
      ? stateUrl
      : (compositeId ? '/api/composite-state?ref=' + encodeURIComponent(compositeId) : null);
    if (!src) return;
    let cancelled = false;
    fetch(src)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        // Accept either an /api/composite-state response ({state: ...}) or a
        // bare state object (a committed snapshot may be either shape).
        const st = (data && typeof data === 'object' && 'state' in data) ? data.state : data;
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
            if (data.library) setLibrary(data.library);
            if (data.id) setCompositeId(data.id);
          }
        }
      })
      .catch(() => { /* fall through to postMessage path */ });
    return () => { cancelled = true; };
  }, [compositeId, state]);

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
      (id: string, positions: ReturnType<typeof positionsFromNodes>, modeId: string) =>
        saveLayout(id, positions, modeId),
      250,
    );
  }

  // Walk the composite state ONCE per state change. On the whole-cell baseline
  // the state is ~6 MB / 345 nodes, so this graph walk is expensive; the layout
  // effect, the reset handler, and the sidebar lists all derive from this.
  const raw = useMemo(
    () => (state ? stateToReactFlow(state) : { nodes: [] as any[], edges: [] as any[] }),
    [state],
  );

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
      const saved = loadLayout(compositeId, layoutMode.modeId);
      const { nodes: laidOut } = await layoutMode.runLayout(
        visibleNodes as any, visibleEdges as any, compositeId, 'mid',
      );
      const withSaved = applySavedPositions(laidOut as any, saved) as any[];
      if (cancelled) return;
      // Apply the CURRENT hidden set to the freshly-rebuilt nodes + edges (read
      // via ref, not a dep). Without this, rebuilding edges here would drop the
      // [hidden] effect's flags and render wires to hidden ("removed") processes.
      const hiddenIds = hiddenNodeIds(raw.nodes as any[], hiddenRef.current);
      // Preserve object identity for nodes that didn't move (or change hidden
      // state) so React Flow does NOT unmount+remount them on collapse/expand.
      setNodes((prev: any[]) => {
        const prevById = new Map(prev.map((n) => [n.id, n]));
        return withSaved.map((n) => {
          const h = hiddenIds.has(n.id);
          const p = prevById.get(n.id);
          if (p
            && p.position?.x === n.position?.x
            && p.position?.y === n.position?.y
            && (p.hidden ?? false) === h) {
            return p;
          }
          return { ...(p ?? n), position: n.position, data: n.data, hidden: h };
        });
      });
      setEdges(visibleEdges.map((e: any) => {
        const h = hiddenIds.has(e.source) || hiddenIds.has(e.target);
        return h ? { ...e, hidden: true } : e;
      }));
    })();

    return () => { cancelled = true; };
    // layoutMode.modeId / runLayout: switching layout mode re-runs the layout.
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
  const drawnEdges = useMemo(
    () => pickDrawnEdges(layoutMode.mode, edges as any[], focus.ctx, nodesRef.current as any[]),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [edges, focus.ctx, layoutMode.mode],
  );

  /** Distinct nodes whose wiring is currently drawn (hover/selection ∪ pins). */
  const activeFocusCount = useMemo(
    () => new Set([...focus.ctx.focused, ...focus.ctx.pinned]).size,
    [focus.ctx],
  );

  // Persist node positions on every change. The layout effect itself sets
  // node positions; we save those too so the layout is "pinned" the first
  // time a composite renders. Subsequent drags update the same store.
  useEffect(() => {
    if (!compositeId || nodes.length === 0) return;
    debouncedPersistRef.current?.(compositeId, positionsFromNodes(nodes as any), layoutMode.modeId);
  }, [nodes, compositeId, layoutMode.modeId]);

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
        visibleNodes as any, visibleEdges as any, compositeId, 'mid',
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
      // Frame the freshly-consolidated set DETERMINISTICALLY: compute the layout
      // bounds + the viewport pixel size ourselves, derive zoom, and setCenter().
      // This depends on NOTHING that React Flow measures asynchronously — earlier
      // fitView()/fitBounds() ran before DOM measurement and panned to empty space.
      const b = boundsOf(laid as any);
      setTimeout(() => {
        const inst = rfRef.current;
        if (!inst || !b) return;
        const el = canvasWrapRef.current;
        const vw = el?.clientWidth || 900;
        const vh = el?.clientHeight || 650;
        const PAD = 1.18; // ~9% margin each side
        const zoom = Math.max(0.05, Math.min(1.5,
          vw / (b.width * PAD || 1), vh / (b.height * PAD || 1)));
        const cx = b.x + b.width / 2;
        const cy = b.y + b.height / 2;
        if (typeof inst.setCenter === 'function') {
          inst.setCenter(cx, cy, { zoom, duration: 400 });
        } else {
          inst.setViewport?.({ x: vw / 2 - cx * zoom, y: vh / 2 - cy * zoom, zoom },
            { duration: 400 });
        }
      }, 60);
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
  }), [nodes, collapsed, hidden, layoutMode.modeId]);

  // Applying a view pins its positions (via the layout store, which the layout
  // effect reads) and sets collapsed/hidden — the existing effects re-lay-out
  // and toggle visibility. Then re-fit so the saved arrangement is framed.
  const applyView = useCallback((view: View) => {
    if (!compositeId || !view) return;
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
    if (viewMode !== layoutMode.modeId) layoutMode.setModeId(viewMode);
    setCollapsed(new Set(view.collapsed || []));
    setHidden(new Set(view.hidden || []));
    window.setTimeout(() => rfRef.current?.fitView?.({ padding: 0.15, duration: 400 }), 240);
  }, [compositeId, layoutMode.modeId, layoutMode.setModeId]);

  // On open, apply a startup view ONCE per composite, in priority order:
  //   1. ?view=<encoded>   (ad-hoc shareable link)
  //   2. ?viewUrl=<url>    (committed view file — README-featured link)
  //   3. the saved default view for this composite (localStorage)
  const startupViewRef = useRef<string | null>(null);
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
      if (view) applyView(view);
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
      const scale = Math.min(1, MAX / Math.max(rawW, rawH, 1));
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
      if (format === 'svg') {
        grab(await toSvg(el, { backgroundColor: '#ffffff', width: w, height: h, style }), 'svg');
      } else {
        const png = await toPng(el, { backgroundColor: '#ffffff', width: w, height: h, style, pixelRatio: 2 });
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

  const handleNodeClick = useCallback((ev: any, node: any) => {
    const payload = {
      path: node.data?.path ?? [],
      kind: node.type as 'store' | 'process',
      details: node.data ?? {},
    };
    setSelection(payload);
    postInspect(payload);
    // Shift/⌘-click PINS, so two processes' wiring can be held on screen and
    // compared; a plain click selects, which keeps one process's wires up after
    // the pointer leaves it.
    if (ev?.shiftKey || ev?.metaKey) focus.togglePin(node.id);
    else focus.select(node.id);
  }, [focus.select, focus.togglePin]);

  // Hovering reveals a node's wiring in modes that cull edges; leaving hides it
  // again unless the node is also selected or pinned.
  const handleNodeMouseEnter = useCallback(
    (_: any, node: any) => focus.hover(node.id), [focus.hover]);
  const handleNodeMouseLeave = useCallback(() => focus.hover(null), [focus.hover]);
  // Clicking empty canvas drops the selection (pins survive) — the way back to
  // the clean, structure-only view.
  const handlePaneClick = useCallback(() => focus.select(null), [focus.select]);

  // Changing granularity re-clusters, but the layout effect's
  // applySavedPositions would immediately overwrite the recomputed positions
  // with the persisted ones (App saves positions after every layout), so the
  // slider would recompute clusters WITHOUT moving anything until "Re-layout".
  // Clear this mode's saved positions first, so the fresh layout — re-run
  // because `runLayout`'s identity changes with granularity — actually lands.
  const handleGranularityChange = useCallback((g: number) => {
    if (compositeId) clearLayout(compositeId, layoutMode.modeId);
    layoutMode.setGranularity(g);
  }, [compositeId, layoutMode.modeId, layoutMode.setGranularity]);

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

  const handleNodeDoubleClick = useCallback((_: any, node: any) => {
    // Only group stores (synthesized container nodes) can be collapsed.
    if (!(node.data as any)?.isGroup) return;
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(node.id)) next.delete(node.id);
      else next.add(node.id);
      return next;
    });
  }, []);

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
  }, [allNodes]);

  const handleEmitToggle = useCallback((path: string[], on: boolean) => {
    setEmitSet((prev) => {
      const next = new Set(prev);
      const key = path.join('/');
      if (on) next.add(key);
      else next.delete(key);
      // Notify the embedding dashboard. Send a sorted list for deterministic
      // ordering on the receiving side.
      postEmitChanged(Array.from(next).sort());
      return next;
    });
  }, []);

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
  const tabs: TabId[] = ['setup', 'results', 'visualizations', 'wiring', 'document'];

  // Display label map: ids that need a human-readable label different from the
  // capitalized id. E.g. 'setup' → 'Setup & Run'.
  const TAB_LABELS: Partial<Record<TabId, string>> = { setup: 'Setup & Run' };

  return (
    <ReactFlowProvider>
      <div style={{ display: 'flex', flexDirection: 'column', width: '100vw', height: '100vh' }}>
        {/* Thin breadcrumb header: composite name + library.
            One layer up from the tabs so the tab strip stays compact. */}
        {(name || compositeId) && (
          <div style={{
            display: 'flex', alignItems: 'baseline', gap: 6,
            padding: '4px 16px',
            fontSize: 12,
            borderBottom: '1px solid #f3f4f6',
            background: '#fff',
            flex: '0 0 auto',
          }}>
            <span style={{ fontWeight: 600, color: '#111827' }}>
              {name || compositeId}
            </span>
            {library && (
              <>
                <span style={{ color: '#d1d5db' }}>·</span>
                <span style={{ color: '#6b7280' }}>{library}</span>
              </>
            )}
          </div>
        )}
        <nav style={{
          display: 'flex', gap: 24, alignItems: 'center',
          padding: '4px 16px',
          borderBottom: '1px solid #e5e7eb',
          background: '#fff',
          flex: '0 0 auto',
        }}>
          {tabs.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              style={{
                background: 'transparent', border: 0,
                padding: '6px 0', fontSize: 14,
                borderBottom: '2px solid ' + (tab === t ? '#2563eb' : 'transparent'),
                color: tab === t ? '#2563eb' : '#6b7280',
                fontWeight: tab === t ? 600 : 400,
                cursor: 'pointer', textTransform: 'capitalize',
              }}
            >
              {TAB_LABELS[t] ?? t}
            </button>
          ))}
        </nav>

        <div style={{ flex: 1, overflow: 'auto', position: 'relative' }}>
          {/* The Wiring tab must always be rendered so ReactFlow doesn't lose
              its node-position state on tab switches; we hide it instead. */}
          <div style={{
            position: 'absolute', inset: 0,
            display: tab === 'wiring' ? 'flex' : 'none',
            flexDirection: 'row',
          }}>
            <EmitContext.Provider value={emitSet}>
              {/* Cluster rail — only in process-column mode, left of the canvas.
                  Names the bands the canvas draws as bare clusters and drives the
                  same focus state, so selecting here reveals wiring on the canvas.
                  Absent in hierarchy mode, which is left entirely untouched. */}
              {layoutMode.modeId === 'process-column' && (
                <ProcessRail
                  bands={layoutMode.bands}
                  nodes={allNodes}
                  focus={focus}
                  granularity={layoutMode.granularity}
                  onGranularityChange={handleGranularityChange}
                  onNavigate={handleRailNavigate}
                  hiddenIds={hidden}
                />
              )}
              {/* Canvas column — flex:1. Holds the Re-layout button + ReactFlow. */}
              <div
                ref={canvasWrapRef}
                className={`loom-canvas loom-mode-${layoutMode.modeId}`}
                style={{ flex: 1, position: 'relative', minWidth: 0 }}
              >
                {/* Modes that cull edges start with NO wires drawn, which without
                    a word of explanation reads as a broken canvas rather than a
                    deliberate clean slate. One line, only in those modes. */}
                {culls && (
                  <div className="loom-focus-hint">
                    {activeFocusCount
                      ? `showing wiring for ${activeFocusCount} node`
                        + `${activeFocusCount === 1 ? '' : 's'}`
                        + (focus.ctx.pinned.size ? ` (${focus.ctx.pinned.size} pinned)` : '')
                      : 'hover to reveal wiring · click to keep · shift-click to pin'}
                  </div>
                )}
                {/* Top-right toolbar: Re-layout + Download (current layout, white bg). */}
                <div style={{
                  position: 'absolute', top: 8, right: 8, zIndex: 10,
                  display: 'flex', gap: 6,
                }}>
                  <ViewsMenu
                    compositeId={compositeId}
                    captureCurrentView={captureCurrentView}
                    applyView={applyView}
                  />
                  <select
                    className="loom-mode-select"
                    value={layoutMode.modeId}
                    onChange={(e) => layoutMode.setModeId(e.target.value)}
                    title="Layout mode"
                    style={{
                      padding: '4px 6px', fontSize: 12,
                      background: '#fff', border: '1px solid #d1d5db',
                      borderRadius: 4, cursor: 'pointer', color: '#374151',
                    }}
                  >
                    {LAYOUT_MODES.map((m) => (
                      <option key={m.id} value={m.id}>{m.label}</option>
                    ))}
                  </select>
                  <button
                    onClick={handleResetLayout}
                    title="Re-run auto-layout on the currently visible nodes and fit the view"
                    style={{
                      padding: '4px 10px', fontSize: 12,
                      background: '#fff', border: '1px solid #d1d5db',
                      borderRadius: 4, cursor: 'pointer', color: '#374151',
                    }}
                  >
                    Re-layout
                  </button>
                  <div style={{ position: 'relative' }}>
                    <button
                      onClick={() => setShowExport((v) => !v)}
                      title="Download the current layout as an image (white background)"
                      style={{
                        padding: '4px 10px', fontSize: 12,
                        background: '#fff', border: '1px solid #d1d5db',
                        borderRadius: 4, cursor: 'pointer', color: '#374151',
                      }}
                    >
                      Download ▾
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
                  nodes={nodes}
                  edges={drawnEdges}
                  onInit={(inst) => { rfRef.current = inst; }}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  nodeTypes={NODE_TYPES}
                  edgeTypes={EDGE_TYPES}
                  onNodeClick={handleNodeClick}
                  onNodeDoubleClick={handleNodeDoubleClick}
                  // Only wired up in modes that actually cull edges by focus
                  // (hierarchy mode's focus is inert): otherwise every node the
                  // pointer crosses sets state and re-renders App — which, on
                  // the wiring tab, re-renders the whole non-memoized Sidebar.
                  onNodeMouseEnter={culls ? handleNodeMouseEnter : undefined}
                  onNodeMouseLeave={culls ? handleNodeMouseLeave : undefined}
                  onPaneClick={handlePaneClick}
                  fitView
                  fitViewOptions={{ padding: 0.2 }}
                  /* Big composites have hundreds of nodes + custom floating edges;
                     only render what's in the viewport so pan/zoom stays smooth. */
                  onlyRenderVisibleElements={!exporting && !STATIC}
                  minZoom={0.02}
                  /* Read-only viewer for wiring/structure, but users CAN rearrange
                     node positions by dragging individual nodes. What's forbidden:
                     new edges, edge reconnects, and any delete. */
                  nodesDraggable
                  nodesConnectable={false}
                  edgesReconnectable={false}
                  connectOnClick={false}
                  deleteKeyCode={null}
                >
                  <Background />
                  <Controls />
                </ReactFlow>
              </div>
              <Sidebar
                selection={selection}
                nodes={allNodes}
                hidden={hidden}
                onToggleHidden={toggleHidden}
                onShowAll={showAll}
                emitSet={emitSet}
                onEmitToggle={handleEmitToggle}
              />
            </EmitContext.Provider>
          </div>
          {tab === 'setup' && (
            <SetupRunPanel
              compositeId={compositeId}
              parameters={parameters}
              overrides={overrides}
              emitSet={emitSet}
              runContext={runContext}
              defaultSteps={defaultSteps}
              onApplied={handleApplied}
              onTrajectory={setTrajectory}
              onVizHtml={setVizHtml}
              onCompleted={() => setTab('results')}
              onRunState={(s) => { setActiveRunId(s.runId); setDownloadable(s.downloadable); }}
              readOnly={STATIC}
            />
          )}
          {tab === 'results' && (
            <ResultsPanel
              trajectory={trajectory}
              hasRun={trajectory !== null || vizHtml !== null}
              runId={activeRunId}
              downloadable={downloadable}
              readOnly={STATIC}
            />
          )}
          {tab === 'visualizations' && (
            <VisualizationsPanel
              vizHtml={vizHtml}
              hasRun={trajectory !== null || vizHtml !== null}
              readOnly={STATIC}
            />
          )}
          {tab === 'document' && (
            <DocumentPanel state={state} compositeId={compositeId} />
          )}
        </div>
      </div>
    </ReactFlowProvider>
  );
}
