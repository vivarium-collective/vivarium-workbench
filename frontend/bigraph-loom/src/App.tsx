import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow, Background, Controls, ReactFlowProvider,
  useNodesState, useEdgesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

// ProcessNode and StoreNode are default exports from the loom node modules
import ProcessNode from './nodes/ProcessNode';
import StoreNode from './nodes/StoreNode';
import FloatingStoreEdge from './edges/FloatingStoreEdge';
import { applyLayout } from './layout';
import {
  loadLayout, saveLayout, clearLayout,
  applySavedPositions, positionsFromNodes, debounce,
} from './layoutStore';
import { stateToReactFlow, topLevelStorePaths } from './convert';
import { isHiddenByAncestor } from './panels/filterHidden';
import { Sidebar } from './panels/Sidebar';
import { RunPanel } from './panels/RunPanel';
import { ResultsPanel } from './panels/ResultsPanel';
import { VisualizationsPanel } from './panels/VisualizationsPanel';
import { DocumentPanel } from './panels/DocumentPanel';
import { ConfigurePanel } from './panels/ConfigurePanel';
import { EmitContext } from './EmitContext';
import {
  postReady, postInspect, postEmitChanged, onCompositeLoad, decodeUrlComposite,
} from './api';
import type { ExploreInspectMsg, ParameterDecl } from './api';

// applyLayout(nodes, edges) → Node[] (returns nodes array directly)
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

type TabId = 'view' | 'configure' | 'run' | 'results' | 'visualizations' | 'document';

type TrajectoryRow = { step: number; time?: number; state: Record<string, unknown> };

export default function App() {
  const [state, setState] = useState<any | null>(decodeUrlComposite());
  const [selection, setSelection] = useState<Omit<ExploreInspectMsg, 'type'> | null>(null);
  // Collapsed group-node ids — children of these nodes are filtered out of the graph.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // Explicitly hidden node ids (via the sidebar Processes/Nodes toggles).
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  // Explicit-emit store paths (joined by '/'). Descendants inherit emission.
  // Seeded with every top-level store so all states emit by default.
  const [emitSet, setEmitSet] = useState<Set<string>>(
    () => new Set(topLevelStorePaths(decodeUrlComposite())),
  );
  const [tab, setTab] = useState<TabId>('view');
  const [compositeId, setCompositeId] = useState<string | null>(() => {
    // Bootstrap from URL query if present (for popups deep-linked with ?id=)
    const p = new URLSearchParams(window.location.search);
    return p.get('id');
  });
  const [runContext, setRunContext] = useState<string>('');
  // Display metadata for the top bar — composite name + the library it's from.
  const [name, setName] = useState<string | null>(null);
  const [library, setLibrary] = useState<string | null>(null);
  // Composite parameters + current overrides (for the Configure tab).
  const [parameters, setParameters] = useState<Record<string, ParameterDecl>>({});
  const [overrides, setOverrides] = useState<Record<string, unknown>>({});
  // From the composite_generator decorator's `default_n_steps=` argument.
  // RunPanel seeds its steps input from this when a new composite loads.
  const [defaultSteps, setDefaultSteps] = useState<number | undefined>(undefined);
  // Run output, lifted up so Results / Visualizations tabs can read it.
  const [trajectory, setTrajectory] = useState<TrajectoryRow[] | null>(null);
  const [vizHtml, setVizHtml] = useState<Record<string, { html: string }> | null>(null);
  const readyFiredRef = useRef(false);
  // React Flow instance, captured via onInit, so Re-layout can fitView() the
  // freshly-consolidated set (App is the ReactFlowProvider's PARENT, so it can't
  // call useReactFlow() directly).
  const rfRef = useRef<any>(null);

  // Use React Flow's controlled-state hooks so drag changes persist across
  // re-renders. Without these, every parent re-render would reset positions
  // to the auto-layout output.
  const [nodes, setNodes, onNodesChange] = useNodesState<any>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);

  // Wire postMessage protocol. Use a ref guard so StrictMode's double-effect
  // doesn't fire `explore:ready` twice during dev.
  useEffect(() => {
    const off = onCompositeLoad((msg) => {
      setState(msg.state);
      setCollapsed(new Set());  // reset folding when a new composite loads
      setHidden(new Set());     // reset show/hide selections too
      // All states emit by default: seed with every top-level store and
      // broadcast so the dashboard's run-emit selection stays in sync.
      const seeded = new Set(topLevelStorePaths(msg.state));
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
    if (!compositeId || state) return;
    let cancelled = false;
    fetch('/api/composite-state?ref=' + encodeURIComponent(compositeId))
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        if (data?.state && !state) {
          setState(data.state);
          setEmitSet(new Set(topLevelStorePaths(data.state)));
        }
      })
      .catch(() => { /* fall through to postMessage path */ });
    return () => { cancelled = true; };
  }, [compositeId, state]);

  // Debounced save of current node positions to localStorage. Built once;
  // stable across re-renders. The callback closes over `compositeId` via the
  // effect below that reads the latest nodes.
  const debouncedPersistRef = useRef<((id: string, positions: ReturnType<typeof positionsFromNodes>) => void) | null>(null);
  if (!debouncedPersistRef.current) {
    debouncedPersistRef.current = debounce(
      (id: string, positions: ReturnType<typeof positionsFromNodes>) => saveLayout(id, positions),
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

    // Collapsed groups hide their descendants (path-prefix check). Hidden nodes
    // cascade the same way — hiding a parent store drops its entire subtree.
    const isHidden = (n: any) => {
      const path: string[] = n.data?.path ?? [];
      for (let i = 1; i < path.length; i++) {
        if (collapsed.has(path.slice(0, i).join('.'))) return true;
      }
      return isHiddenByAncestor(path, hidden);
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
    const visibleEdges = raw.edges.filter(
      (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
    );

    (async () => {
      const saved = loadLayout(compositeId);
      const laid = await applyLayout(visibleNodes as any, visibleEdges as any);
      const withSaved = applySavedPositions(laid as any, saved);
      if (cancelled) return;
      setNodes(withSaved as any);
      setEdges(visibleEdges as any);
    })();

    return () => { cancelled = true; };
  }, [state, raw, collapsed, hidden, compositeId, setNodes, setEdges]);

  // Persist node positions on every change. The layout effect itself sets
  // node positions; we save those too so the layout is "pinned" the first
  // time a composite renders. Subsequent drags update the same store.
  useEffect(() => {
    if (!compositeId || nodes.length === 0) return;
    debouncedPersistRef.current?.(compositeId, positionsFromNodes(nodes as any));
  }, [nodes, compositeId]);

  const handleResetLayout = useCallback(() => {
    if (!compositeId) return;
    clearLayout(compositeId);
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
      const visibleEdges = raw.edges.filter(
        (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
      );
      const laid = await applyLayout(visibleNodes as any, visibleEdges as any);
      setNodes(laid as any);
      setEdges(visibleEdges as any);
      // Frame the freshly-consolidated set. Compute the bounds from the layout
      // positions ourselves and use fitBounds — this does NOT depend on React
      // Flow having measured the new DOM nodes yet (fitView() before measurement
      // pans to stale/empty coords, which is why the graph went "out of view").
      const b = boundsOf(laid as any);
      setTimeout(() => {
        const inst = rfRef.current;
        if (!inst) return;
        if (b && typeof inst.fitBounds === 'function') {
          inst.fitBounds(b, { padding: 0.12, duration: 400 });
        } else {
          inst.fitView?.({ padding: 0.2, duration: 400 });
        }
      }, 60);
    })();
  }, [compositeId, state, raw, collapsed, hidden, setNodes, setEdges]);

  const handleNodeClick = useCallback((_: any, node: any) => {
    const payload = {
      path: node.data?.path ?? [],
      kind: node.type as 'store' | 'process',
      details: node.data ?? {},
    };
    setSelection(payload);
    postInspect(payload);
  }, []);

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
        <h3>bigraph-loom-explore</h3>
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

  const tabs: TabId[] = ['view', 'configure', 'run', 'results', 'visualizations', 'document'];

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
              {t}
            </button>
          ))}
        </nav>

        <div style={{ flex: 1, overflow: 'auto', position: 'relative' }}>
          {/* The View tab must always be rendered so ReactFlow doesn't lose
              its node-position state on tab switches; we hide it instead. */}
          <div style={{
            position: 'absolute', inset: 0,
            display: tab === 'view' ? 'flex' : 'none',
            flexDirection: 'row',
          }}>
            <EmitContext.Provider value={emitSet}>
              {/* Canvas column — flex:1. Holds the Reset button + ReactFlow. */}
              <div style={{ flex: 1, position: 'relative', minWidth: 0 }}>
                {/* Re-layout button — top-right of the canvas. Re-runs auto-layout
                    on the CURRENT visible set (after hiding/collapsing nodes),
                    consolidating them into a tight view, and fits the viewport. */}
                <button
                  onClick={handleResetLayout}
                  title="Re-run auto-layout on the currently visible nodes and fit the view"
                  style={{
                    position: 'absolute', top: 8, right: 8, zIndex: 10,
                    padding: '4px 10px', fontSize: 12,
                    background: '#fff', border: '1px solid #d1d5db',
                    borderRadius: 4, cursor: 'pointer', color: '#374151',
                  }}
                >
                  Re-layout
                </button>
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onInit={(inst) => { rfRef.current = inst; }}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  nodeTypes={NODE_TYPES}
                  edgeTypes={EDGE_TYPES}
                  onNodeClick={handleNodeClick}
                  onNodeDoubleClick={handleNodeDoubleClick}
                  fitView
                  fitViewOptions={{ padding: 0.2 }}
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
          {tab === 'configure' && (
            <ConfigurePanel
              compositeId={compositeId}
              parameters={parameters}
              overrides={overrides}
              onApplied={handleApplied}
            />
          )}
          {tab === 'run' && (
            <RunPanel
              compositeId={compositeId}
              emitSet={emitSet}
              overrides={overrides}
              runContext={runContext}
              defaultSteps={defaultSteps}
              onTrajectory={setTrajectory}
              onVizHtml={setVizHtml}
            />
          )}
          {tab === 'results' && (
            <ResultsPanel
              trajectory={trajectory}
              hasRun={trajectory !== null || vizHtml !== null}
            />
          )}
          {tab === 'visualizations' && (
            <VisualizationsPanel
              vizHtml={vizHtml}
              hasRun={trajectory !== null || vizHtml !== null}
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
