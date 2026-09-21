// src/nodes/InnerCompositePreview.tsx — a live thumbnail of a Composite
// Process's INNER composite, rendered inside its card (in place of the
// contract) at high zoom. Lazily fetches /api/composite-inner-state (the same
// endpoint the drill-down uses), converts it to nodes/edges, lays them out with
// the sync depth-stack layout, and draws a scaled-to-fit static SVG mini-map.
//
// The inner build is ParCa-heavy (a few seconds the first time), so the fetch
// is lazy + module-cached + shows a "building…" placeholder. Double-clicking
// the card still opens the full drill-down view (App's onNodeDoubleClick).

import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import {
  stateToReactFlow, defaultCollapsedIds, defaultHiddenIds,
} from '../convert';
import { fetchInnerComposite } from '../api';
import { displayName } from '../labels';

type Graph = { nodes: any[]; edges: any[] };
type CacheEntry = { status: 'loading' | 'ready' | 'error'; graph?: Graph; error?: string };

// Two kinds of text about the inner composite, deliberately kept apart:
//  • INNER_DESC — a static, figure-appropriate caption of WHAT this is (it reads
//    the same whether or not the preview renders, and belongs in printed/figure
//    output). It never mentions the mouse.
//  • INNER_INTERACT — HOW to use it, surfaced only on hover (the title tooltip),
//    so the interaction help doesn't clutter the static figure.
const INNER_DESC =
  'Inside this Composite Process: the internal process-bigraph it runs — the ' +
  'sub-processes it schedules each step and the shared stores they read and write.';
const INNER_INTERACT =
  'Scroll to zoom · drag to pan · double-click the card to open the full explorer';

// Module-level cache keyed by the drill target, so re-renders / re-mounts (and
// both colony cells sharing the same inner model shape) never refetch.
const _CACHE = new Map<string, CacheEntry>();
const _WAITERS = new Map<string, Set<() => void>>();

// The config overrides these previews must resolve with (n_generations>1 → the
// batch's batch_runner exists to drill into, etc.), set by App whenever the
// root's applied config changes. Module-level so we don't thread a prop through
// every ProcessNode; the cache key includes it, so a config change re-keys
// instead of surfacing a stale default-config preview (or hanging on a hop that
// only exists in the config-applied graph).
let _lastOverrides: Record<string, unknown> = {};
export function setInnerCompositeOverrides(ov: Record<string, unknown> | undefined): void {
  _lastOverrides = ov || {};
}

function _key(rootId: string, hops: string[][], overrides: Record<string, unknown> = _lastOverrides): string {
  const ov = overrides && Object.keys(overrides).length ? '::' + JSON.stringify(overrides) : '';
  return rootId + '::' + JSON.stringify(hops) + ov;
}

function _notify(key: string) {
  (_WAITERS.get(key) ?? new Set()).forEach((cb) => cb());
}

/** Fetch the inner state, retrying transient failures with backoff. The inner
 *  build runs in an env-worker warm pool that can be briefly unavailable (503)
 *  right after a server (re)start, and the first cold build is a few seconds —
 *  a single attempt turns those into a permanent "preview unavailable". Retry a
 *  few times before giving up so the preview renders on its own. */
async function _fetchWithRetry(
  rootId: string, hops: string[][], overrides: Record<string, unknown>, tries = 4,
) {
  let lastErr: any;
  for (let i = 0; i < tries; i++) {
    try {
      return await fetchInnerComposite(rootId, hops, overrides);
    } catch (e) {
      lastErr = e;
      if (i < tries - 1) await new Promise((r) => setTimeout(r, 700 * (i + 1)));
    }
  }
  throw lastErr;
}

async function _load(rootId: string, hops: string[][], overrides: Record<string, unknown> = _lastOverrides) {
  const key = _key(rootId, hops, overrides);
  const cur = _CACHE.get(key);
  // Load once; but a prior ERROR is retryable (click-to-retry re-enters here).
  if (cur && cur.status !== 'error') return;
  _CACHE.set(key, { status: 'loading' });
  _notify(key);
  try {
    const res = await _fetchWithRetry(rootId, hops, overrides);
    _CACHE.set(key, { status: 'ready', graph: _overviewGraph(res.state) });
  } catch (e: any) {
    _CACHE.set(key, { status: 'error', error: e?.message || String(e) });
  }
  _notify(key);
}

/** The OVERVIEW subset of a composite for the thumbnail: the full state has
 *  hundreds of deep leaf stores (a WCM has ~476), which scale to sub-pixel dots
 *  and swamp the processes. Collapse deep container stores + hide bookkeeping
 *  noise (the same defaults the full canvas opens with), so the thumbnail shows
 *  the processes + top-level stores — a legible map, not a grey haze. */
function _overviewGraph(state: any): Graph {
  const all = stateToReactFlow(state);
  const collapsed = defaultCollapsedIds(state);
  const hidden = defaultHiddenIds(state);
  const underCollapsed = (path: string[]) => {
    for (let i = 1; i < path.length; i++) {
      if (collapsed.has(path.slice(0, i).join('.'))) return true;
    }
    return false;
  };
  const nodes = all.nodes.filter(
    (n) => !hidden.has(n.id) && !underCollapsed((n.data as any)?.path ?? []),
  );
  const ids = new Set(nodes.map((n) => n.id));
  const edges = all.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  return { nodes, edges };
}

/** Warm the cache for a Composite Process's inner composite in the background,
 *  so its in-card mini-map renders instantly when the user zooms in (no
 *  "building…" flash). No-op if already loaded/loading. */
export function prefetchInner(
  rootId: string, hops: string[][], overrides: Record<string, unknown> = _lastOverrides,
) {
  if (!_CACHE.has(_key(rootId, hops, overrides))) _load(rootId, hops, overrides);
}

const PROC = { w: 176, h: 64 };
const STORE = { w: 132, h: 68 };

/** Truncate a process label to fit inside the mini-card. */
function _short(label: string, max = 13): string {
  if (!label) return '';
  return label.length > max ? label.slice(0, max - 1) + '…' : label;
}

/** Compact grid layout for the thumbnail — tight gaps so the nodes FILL the box
 *  (the shared depth-stack layout uses full-card-size gaps, which shrink these
 *  mini nodes to nothing). Stores in a band on top, processes in a grid below;
 *  columns chosen so the whole figure roughly matches the card's aspect and fits
 *  with no scrolling. Returns top-left positions by node id. */
function _miniLayout(nodes: any[]): Map<string, { x: number; y: number }> {
  const procs = nodes.filter((n) => n.type === 'process');
  const stores = nodes.filter((n) => n.type !== 'process');
  const pos = new Map<string, { x: number; y: number }>();

  const SG_X = 26, SG_Y = 46;   // store grid gaps (room above dot for its label)
  const PG_X = 40, PG_Y = 48;   // process grid gaps
  const colsS = Math.max(1, Math.round(Math.sqrt(stores.length * 3.2)));
  // Soft depth alignment: group stores by bigraph depth (path length) and stack
  // the groups in bands top→bottom, so stores keep SOME preference for aligning
  // by depth while still wrapping (not a strict single row per depth).
  const depthOf = (n: any) =>
    Math.max(1, ((n.data?.path as unknown[] | undefined)?.length ?? 1));
  const byDepth = new Map<number, any[]>();
  for (const s of stores) {
    const d = depthOf(s);
    if (!byDepth.has(d)) byDepth.set(d, []);
    byDepth.get(d)!.push(s);
  }
  let sy = 0;
  for (const d of [...byDepth.keys()].sort((a, b) => a - b)) {
    const grp = byDepth.get(d)!
      .slice()
      .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    grp.forEach((s, i) => {
      pos.set(s.id, {
        x: (i % colsS) * (STORE.w + SG_X),
        y: sy + Math.floor(i / colsS) * (STORE.h + SG_Y),
      });
    });
    const rows = Math.max(1, Math.ceil(grp.length / colsS));
    sy += rows * (STORE.h + SG_Y) + 14;  // small gap between depth bands
  }
  const bandH = sy + 20;

  const colsP = Math.max(1, Math.round(Math.sqrt(procs.length * 1.9)));
  procs.forEach((n, i) => {
    pos.set(n.id, {
      x: (i % colsP) * (PROC.w + PG_X),
      y: bandH + Math.floor(i / colsP) * (PROC.h + PG_Y),
    });
  });
  return pos;
}

/** Map a composite's SAVED VIEW positions onto the mini-nodes, normalized so the
 *  mini node sizes stay legible (the view's coords are full-canvas scale). The
 *  arrangement is preserved; only the overall scale is fit to the mini node grid.
 *  Falls back to the auto grid if the view doesn't position every node. */
function _viewLayout(nodes: any[], viewPos: Record<string, { x: number; y: number }>)
  : Map<string, { x: number; y: number }> | null {
  const pts: Array<[string, { x: number; y: number }]> = [];
  for (const n of nodes) {
    const v = viewPos[n.id];
    if (!v || typeof v.x !== 'number' || typeof v.y !== 'number') return null;  // incomplete → grid
    pts.push([n.id, { x: v.x, y: v.y }]);
  }
  if (!pts.length) return null;
  // Fit the saved view into a LANDSCAPE box (mini-cards are wide, and a portrait
  // source layout would otherwise blow the preview's height). x and y scale
  // independently so the left/right ordering is preserved while the vertical
  // spread is compressed to fit — enough to convey the arrangement.
  const xs = pts.map(([, p]) => p.x), ys = pts.map(([, p]) => p.y);
  const spanX = Math.max(1, Math.max(...xs) - Math.min(...xs));
  const spanY = Math.max(1, Math.max(...ys) - Math.min(...ys));
  const W = Math.max(1, Math.ceil(Math.sqrt(pts.length))) * (PROC.w + 40);
  const H = W * 0.6;
  const sx = W / spanX, sy = H / spanY;
  const minX = Math.min(...xs), minY = Math.min(...ys);
  const out = new Map<string, { x: number; y: number }>();
  for (const [id, p] of pts) out.set(id, { x: (p.x - minX) * sx, y: (p.y - minY) * sy });
  return out;
}

/** Draw the laid-out inner graph as a scaled static SVG mini-map: real
 *  labelled process cards, small store dots, thin wires. Aspect-fit (the SVG box
 *  matches the content's aspect via width:100%/height:auto), so the graph fills
 *  the width with no wasted vertical margins. */
function MiniMap(props: {
  graph: Graph;
  viewPos?: Record<string, { x: number; y: number }>;
  /** The composite process's OUTER ports — drawn as bridge connectors from the
   *  card edge to the matching inner store (input ports on the left, output on
   *  the right), representing how the Composite's ports link to its inner doc. */
  bridge?: { inputs: string[]; outputs: string[] };
}) {
  const { nodes, edges } = props.graph;
  const posById = (props.viewPos && _viewLayout(nodes, props.viewPos)) || _miniLayout(nodes);
  const sizeOf = (n: any) => (n.type === 'process' ? PROC : STORE);
  const centerById = new Map<string, { x: number; y: number }>();

  const lbl = PROC.h * 0.5;
  // A process box STRETCHES wider than PROC.w to fit its label (see the mini-proc
  // render below), centred on the node — so measure the bounds with that stretched
  // width, else a long-named process (e.g. "gene expression") gets clipped by the
  // fit-to-box viewBox.
  const procWidth = (n: any) =>
    n.type === 'process'
      ? Math.max(PROC.w, String(displayName(n.data?.label ?? '')).length * lbl * 0.6 + 26)
      : STORE.w;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of nodes) {
    const p = posById.get(n.id);
    if (!p) continue;
    const s = sizeOf(n);
    const ew = procWidth(n);                 // effective (rendered) width
    const left = p.x + (s.w - ew) / 2;        // process box is centred on the node
    minX = Math.min(minX, left); minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, left + ew); maxY = Math.max(maxY, p.y + s.h);
    centerById.set(n.id, { x: p.x + s.w / 2, y: p.y + s.h / 2 });
  }
  if (!isFinite(minX)) return null;
  const pad = 8;
  // Extra horizontal room for the composite-bridge connectors + their labels.
  const bgap = props.bridge ? PROC.w * 0.45 : 0;      // connector length
  const bmargin = props.bridge ? bgap + PROC.w * 0.55 : 0;   // + port + label room
  const w = maxX - minX + pad * 2 + 2 * bmargin;
  const h = maxY - minY + pad * 2;
  const vbX = minX - pad - bmargin;
  const vb = `${vbX} ${minY - pad} ${w} ${h}`;
  const Cx = vbX + w / 2, Cy = minY - pad + h / 2;
  const sw = Math.max(1, Math.max(w, h) / 700);

  const procCount = nodes.filter((n) => n.type === 'process').length;
  const storeCount = nodes.length - procCount;
  const drawEdges = edges.length <= 800;

  // --- Interaction: wheel-zoom (center-anchored), drag-pan, click-select.
  // Contained: wheel/mousedown stopPropagation so the OUTER canvas doesn't also
  // zoom/drag. Double-click still bubbles to the card → opens the full view.
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [k, setK] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [sel, setSel] = useState<string | null>(null);
  const drag = useRef<{ sx: number; sy: number; px: number; py: number } | null>(null);
  const tx = Cx * (1 - k) + pan.x, ty = Cy * (1 - k) + pan.y;

  const onWheel = (e: React.WheelEvent) => {
    e.stopPropagation();
    const f = e.deltaY < 0 ? 1.18 : 1 / 1.18;
    setK((cur) => Math.min(10, Math.max(1, cur * f)));
  };
  const onDown = (e: React.MouseEvent) => {
    e.stopPropagation();
    drag.current = { sx: e.clientX, sy: e.clientY, px: pan.x, py: pan.y };
  };
  const onMove = (e: React.MouseEvent) => {
    const d = drag.current, el = svgRef.current;
    if (!d || !el) return;
    const rect = el.getBoundingClientRect();
    setPan({
      x: d.px + ((e.clientX - d.sx) / rect.width) * w,
      y: d.py + ((e.clientY - d.sy) / rect.height) * h,
    });
  };
  const onUp = () => { drag.current = null; };
  const reset = (e: React.MouseEvent) => { e.stopPropagation(); setK(1); setPan({ x: 0, y: 0 }); };
  const panned = k !== 1 || pan.x !== 0 || pan.y !== 0;

  return (
    <div className="inner-preview" title={INNER_INTERACT}>
      <div className="inner-preview-head">
        <span className="inner-preview-badge">⤢ inner composite</span>
        <span className="inner-preview-count">
          {procCount} sub-processes · {storeCount} stores
          {panned && <button className="mini-reset" onClick={reset} title="Reset the preview's zoom and pan">reset</button>}
        </span>
      </div>
      <div className="inner-preview-desc">{INNER_DESC}</div>
      <svg
        ref={svgRef}
        /* nodrag/nowheel: let THIS element handle drag + wheel instead of the
           outer React Flow canvas (which owns node-drag + pane-zoom). */
        className="inner-preview-svg nodrag nowheel"
        viewBox={vb}
        preserveAspectRatio="none"
        /* Fill the outer box: the svg element already fills the card's content box
           (flex), and `none` lets the inner graph stretch to those box dimensions
           instead of aspect-fitting (`meet`) and leaving letterbox whitespace. So
           the inner-composite window is controlled by the card's shape — resize the
           card wider/taller and the mini-map follows, edge to edge. */
        style={{ cursor: 'grab' }}
        onWheel={onWheel}
        onMouseDown={onDown}
        onMouseMove={onMove}
        onMouseUp={onUp}
        onMouseLeave={onUp}
      >
        <g transform={`translate(${tx} ${ty}) scale(${k})`}>
        {drawEdges && edges.map((e: any) => {
          const a = centerById.get(e.source);
          const b = centerById.get(e.target);
          if (!a || !b) return null;
          const hot = sel != null && (e.source === sel || e.target === sel);
          const place = e.data?.edgeType === 'place';
          return (
            <line
              key={e.id}
              x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              // Match the outer canvas: place edges solid slate, process wires
              // dashed slate.
              stroke={hot ? '#2563eb' : place ? '#64748b' : '#94a3b8'}
              strokeWidth={hot ? sw * 3.4 : place ? sw * 3.4 : sw * 2.4}
              strokeDasharray={place ? undefined : `${sw * 5},${sw * 4}`}
              strokeOpacity={hot ? 0.95 : 0.85}
            />
          );
        })}
        {/* Stores as loom-style rounded rectangles (green border, label inside). */}
        {nodes.filter((n) => n.type !== 'process').map((n: any) => {
          const p = posById.get(n.id);
          const c = centerById.get(n.id);
          if (!p || !c) return null;
          const name = n.data?.label ?? '';
          const on = sel === n.id;
          return (
            <g key={n.id} className={`mini-store${on ? ' is-sel' : ''}`}
               onClick={(e) => { e.stopPropagation(); setSel(on ? null : n.id); }}>
              <title>{name} (store)</title>
              {(() => {
                const vtype = (n.data as { valueType?: string })?.valueType || '';
                const nameY = vtype ? c.y - lbl * 0.34 : c.y;
                return (
              <>
              <rect x={p.x} y={p.y} width={STORE.w} height={STORE.h} rx={10}
                fill={on ? '#ecfdf5' : '#ffffff'} stroke={on ? '#059669' : '#34d399'}
                strokeWidth={on ? sw * 3.2 : sw * 2.2} />
              {/* Match the outer store: dark-slate name (bold), grey type below. */}
              <text
                x={c.x} y={nameY} fontSize={lbl * 0.8}
                textAnchor="middle" dominantBaseline="central" fill="#1e293b" fontWeight={600}
                fontFamily="ui-sans-serif, system-ui, sans-serif"
                className="mini-store-label"
              >
                {_short(displayName(name), 12)}
              </text>
              {vtype && (
                <text
                  x={c.x} y={c.y + lbl * 0.5} fontSize={lbl * 0.6}
                  textAnchor="middle" dominantBaseline="central" fill="#94a3b8"
                  fontFamily="ui-monospace, monospace"
                >
                  {_short(vtype, 14)}
                </text>
              )}
              </>
                );
              })()}
            </g>
          );
        })}
        {/* Processes as clean labelled cards (on top). Hover shows full name. */}
        {nodes.filter((n) => n.type === 'process').map((n: any) => {
          const p = posById.get(n.id);
          if (!p) return null;
          const c = centerById.get(n.id)!;
          const name = n.data?.label ?? '';
          const on = sel === n.id;
          return (
            <g key={n.id} className={`mini-proc${on ? ' is-sel' : ''}`}
               onClick={(e) => { e.stopPropagation(); setSel(on ? null : n.id); }}>
              <title>{name} (process)</title>
              {/* Box STRETCHES to fit the label (centered on the node), so a long
                  name like gene_expression isn't clipped. Sharp purple rectangle +
                  dark-slate text, matching the outer process cards. */}
              {(() => { const pw = Math.max(PROC.w, name.length * lbl * 0.6 + 26); return (
              <>
              <rect
                x={c.x - pw / 2} y={p.y} width={pw} height={PROC.h} rx={0}
                fill={on ? '#eff6ff' : '#ffffff'} stroke={on ? '#1d4ed8' : '#6366f1'}
                strokeWidth={on ? sw * 3.4 : sw * 2.4}
              />
              <text
                x={c.x} y={c.y} fontSize={lbl} textAnchor="middle"
                dominantBaseline="central" fill="#1e293b" fontWeight={600}
                fontFamily="ui-sans-serif, system-ui, sans-serif"
              >
                {displayName(name)}
              </text>
              </>
              ); })()}
            </g>
          );
        })}
        {/* Composite bridge: the outer ports linked to the matching inner store —
            inputs enter from the left edge, outputs leave to the right, as small
            ports on dashed wires (the same style the outer card uses). */}
        {props.bridge && (() => {
          const storeCenter = (nm: string) => {
            const s = nodes.find((n) => n.type !== 'process' && n.data?.label === nm);
            return s ? centerById.get(s.id) ?? null : null;
          };
          const conns: Array<{ nm: string; side: -1 | 1 }> = [
            ...(props.bridge!.inputs || []).map((nm) => ({ nm, side: -1 as const })),
            ...(props.bridge!.outputs || []).map((nm) => ({ nm, side: 1 as const })),
          ];
          return conns.map(({ nm, side }) => {
            const t = storeCenter(nm);
            if (!t) return null;
            const px = side < 0 ? minX - pad - bgap : maxX + pad + bgap;
            const r = STORE.h * 0.16;
            return (
              <g key={`bridge-${side}-${nm}`}>
                <line x1={px} y1={t.y} x2={t.x} y2={t.y}
                  stroke="#94a3b8" strokeWidth={sw * 2.4}
                  strokeDasharray={`${sw * 5},${sw * 4}`} strokeOpacity={0.85} />
                <circle cx={px} cy={t.y} r={r} fill="#ffffff" stroke="#10b981"
                  strokeWidth={sw * 2.2} />
                <text x={side < 0 ? px - r - 6 : px + r + 6} y={t.y}
                  fontSize={lbl * 0.7} textAnchor={side < 0 ? 'end' : 'start'}
                  dominantBaseline="central" fill="#0f766e" fontWeight={600}
                  fontFamily="ui-sans-serif, system-ui, sans-serif">{nm}</text>
              </g>
            );
          });
        })()}
        </g>
      </svg>
    </div>
  );
}

export default function InnerCompositePreview(props: {
  rootId: string;
  hops: string[][];
  /** Auto-fetch on mount. When false, show a compact "render" button (the viz
   *  icon) instead — used at the `contract` tier to avoid a ParCa build on every
   *  card the moment zoom crosses the threshold; `full` tier auto-loads. */
  auto: boolean;
  /** The inner document, if the Composite Process already carries it statically
   *  (config.state). When present we render the mini-map DIRECTLY from it — no
   *  live `/api/composite-inner-state` build — so a static composite (and the
   *  headless figure render, where the env-worker build can't complete) shows its
   *  inner bigraph instead of "preview unavailable". */
  localState?: unknown;
  /** Saved-view node positions for the inner composite (from the process's
   *  config._inner_view.positions). When given, the mini-map uses this hand-tuned
   *  layout instead of the generic grid. */
  viewPos?: Record<string, { x: number; y: number }>;
  /** The composite process's outer ports → drawn as bridge connectors. */
  bridge?: { inputs: string[]; outputs: string[] };
}) {
  // Fast path: a self-contained composite process — render its own inner doc. If
  // the caller supplies the source composite's SAVED VIEW positions (props.viewPos,
  // from the process's config._inner_view), lay the mini-map out with them so the
  // preview mirrors that composite's hand-tuned layout.
  if (props.localState && typeof props.localState === 'object') {
    const graph = _overviewGraph(props.localState);
    if (graph.nodes.length) return <MiniMap graph={graph} viewPos={props.viewPos} bridge={props.bridge} />;
  }
  const key = _key(props.rootId, props.hops);
  const [, bump] = useState(0);
  const entry = _CACHE.get(key);

  // Subscribe to cache changes for this key so async loads re-render the card.
  useEffect(() => {
    const cb = () => bump((n) => n + 1);
    let set = _WAITERS.get(key);
    if (!set) { set = new Set(); _WAITERS.set(key, set); }
    set.add(cb);
    return () => { set!.delete(cb); };
  }, [key]);

  // Auto-load once when requested and not yet started.
  useEffect(() => {
    if (props.auto && !_CACHE.has(key)) _load(props.rootId, props.hops);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, props.auto]);

  if (!entry) {
    // Idle (contract tier, not auto): show the caption + a build-on-demand hint.
    return (
      <div
        className="inner-preview inner-preview-idle"
        title={'Click to build the preview · ' + INNER_INTERACT}
        onClick={(e) => { e.stopPropagation(); _load(props.rootId, props.hops); }}
      >
        <span className="inner-preview-badge">⤢ inner composite</span>
        <span className="inner-preview-desc">{INNER_DESC}</span>
        <span className="inner-preview-hint">click to build the preview</span>
      </div>
    );
  }
  if (entry.status === 'loading') {
    return (
      <div className="inner-preview inner-preview-loading" title={INNER_INTERACT}>
        <span className="inner-preview-badge">⤢ inner composite</span>
        <span className="inner-preview-desc">{INNER_DESC}</span>
        <span className="inner-preview-hint">building the inner model… (resolving the Composite's sub-model)</span>
      </div>
    );
  }
  if (entry.status === 'error' || !entry.graph) {
    // Keep the figure-appropriate caption even when the live build fails, and put
    // the actual reason + retry/open guidance in the hover tooltip.
    return (
      <div
        className="inner-preview inner-preview-error"
        title={(entry.error ? 'Could not build the preview: ' + entry.error + '\n\n' : '') +
               'Click to retry · double-click the card to open the full explorer'}
        onClick={(e) => { e.stopPropagation(); _load(props.rootId, props.hops); }}
      >
        <span className="inner-preview-badge">⤢ inner composite</span>
        <span className="inner-preview-desc">{INNER_DESC}</span>
        <span className="inner-preview-hint">preview couldn’t be built here — hover for details · click to retry</span>
      </div>
    );
  }
  return <MiniMap graph={entry.graph} viewPos={props.viewPos} bridge={props.bridge} />;
}
