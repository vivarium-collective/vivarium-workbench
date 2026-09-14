import { memo, useEffect, useRef, useState } from "react";
import { Handle, Position, NodeResizer, useReactFlow, useNodeId, type NodeProps } from "@xyflow/react";
import type { ProcessNodeData } from "../types";
import { deriveContract, contractCompleteness } from "../contract";
import { portInfo } from "../portInfo";
import { KatexBlock } from "../Katex";
import { toLatex, MathText } from "../mathText";
import InnerCompositePreview from "./InnerCompositePreview";
import { configParams } from "../configView";
import { displayName } from "../labels";

// Canvas text metrics — used to reserve exactly the room the widest port label
// needs (see the adaptive port-column width below). A shared module-level context
// is cheap; falls back to a per-char estimate only when there is no DOM (the
// headless figure render runs in real chromium, so this is exact there too).
const _measureCanvas: HTMLCanvasElement | null =
  typeof document !== 'undefined' ? document.createElement('canvas') : null;
const _measureCtx = _measureCanvas ? _measureCanvas.getContext('2d') : null;
function measureLabel(text: string, font: string, perChar: number): number {
  if (_measureCtx) { _measureCtx.font = font; return _measureCtx.measureText(text).width; }
  return text.length * perChar;
}

function _classifyStep(address: string | undefined, label: string | undefined): 'process' | 'emitter' | 'visualization' {
  const addr = address || '';
  const lbl = label || '';
  // Emitter convention: process-bigraph emitters end with 'Emitter' OR labeled emitter_*
  if (/Emitter\b/.test(addr) || /^(sqlite_)?emitter\b|^user_emitter\b/.test(lbl)) {
    return 'emitter';
  }
  // Visualization-class heuristic — by-class-name OR by viz_* convention on the step label.
  // Covers TestSuiteTimeSeries, FieldSnapshotsGrid, FieldAnimationGif, FieldHeatmap, DemoTimeSeriesPlot,
  // BondNetworkPlots, MembranePlots, Distribution, PhaseSpace, ParamVsObservable, TimeSeriesPlot, etc.
  if (/(Plot|Heatmap|Animation|Snapshots|Distribution|Viz|TimeSeries|Series|Chart|Trajectory|Histogram|PhaseSpace|ParamVs)/i.test(addr)
      || /^viz[_-]/i.test(lbl)) {
    return 'visualization';
  }
  return 'process';
}

/** One-line "what is this section" hints, shown on hover of each middle box. */
const SECTION_HINT = {
  config:   'Configuration — build-time parameters that set how this process behaves. Click for values & types.',
  meta:     'Process type and how many input / output ports it has.',
  contract: 'Contract — what this process reads, computes, and writes. Click for the full description.',
  math:     'Governing equations — how the outputs are computed from the inputs.',
  symbols:  'Symbol legend — what each variable in the equations means.',
} as const;

/**
 * Legacy card body: centered label + type with flanking port labels. Used in
 * modes that do NOT drive semantic zoom (hierarchy). Preserved verbatim so
 * hierarchy renders exactly as before this task — semantic zoom is opt-in via a
 * stamped `_tier`, which only process-column mode sets.
 */
function LegacyBody({ data, stepKind }: {
  data: ProcessNodeData;
  stepKind: 'process' | 'emitter' | 'visualization';
}) {
  const inputPorts = data.inputPorts ?? [];
  const outputPorts = data.outputPorts ?? [];
  const portSchema = data.inputPortsSchema ?? {};
  const outSchema = data.outputPortsSchema ?? {};
  return (
    <div className={`process-node process-node-${stepKind}`}>
      {inputPorts.map((port, i) => {
        const typeStr = portSchema[port] ? String(portSchema[port]) : undefined;
        const top = `${((i + 1) / (inputPorts.length + 1)) * 100}%`;
        return (
          <div key={`in-${port}`}>
            <Handle
              type="target"
              position={Position.Left}
              id={port}
              className="port-handle port-handle-input"
              style={{ top }}
            />
            <div className="port-label port-label-left" style={{ top }}>
              <span className="port-label-name">{port}</span>
              {typeStr && (
                <span className="port-label-tooltip">{typeStr}</span>
              )}
            </div>
          </div>
        );
      })}

      <div className="process-body">
        <div className="process-label">
          {displayName(data.label)}
          {(data as any).isDraft && (
            <span className="process-node-draft" title="Draft process — typed ports + contract, but NO update dynamics yet">
              DRAFT
            </span>
          )}
          {data.isCompositeProcess && (
            <span
              className="process-node-drill"
              title="Composite Process — double-click to open its inner composite"
            >
              {' '}⤢
            </span>
          )}
        </div>
        <div className="process-type">{data.processType}</div>
      </div>

      {outputPorts.map((port, i) => {
        const typeStr = outSchema[port] ? String(outSchema[port]) : undefined;
        const top = `${((i + 1) / (outputPorts.length + 1)) * 100}%`;
        return (
          <div key={`out-${port}`}>
            <Handle
              type="source"
              position={Position.Right}
              id={port}
              className="port-handle port-handle-output"
              style={{ top }}
            />
            <div className="port-label port-label-right" style={{ top }}>
              <span className="port-label-name">{port}</span>
              {typeStr && (
                <span className="port-label-tooltip">{typeStr}</span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ProcessNode({ data }: NodeProps & { data: ProcessNodeData }) {
  const inputPorts = data.inputPorts ?? [];
  const outputPorts = data.outputPorts ?? [];
  const stepKind = _classifyStep((data as any).address, data.label);

  // Semantic zoom is opt-in: only process-column mode stamps `_tier` (or
  // `_pinnedOpen`). Absent both, render the legacy fixed card so hierarchy mode
  // is untouched.
  if ((data as any)._tier == null && !(data as any)._pinnedOpen) {
    return <LegacyBody data={data} stepKind={stepKind} />;
  }

  // Semantic zoom: the stamped tier decides WHICH rows exist. Font size is
  // constant across tiers (see App.css) — legibility at low zoom comes from
  // dropping content, never from shrinking text. A pinned-open card always
  // shows full detail regardless of the current zoom tier.
  const tier = ((data as any)._tier ?? 'ports') as
    'glyph' | 'ports' | 'types' | 'config' | 'contract' | 'full';
  const t = (data as any)._pinnedOpen ? 'full' : tier;

  const show = {
    ports:    t !== 'glyph',
    types:    t === 'types' || t === 'config' || t === 'contract' || t === 'full',
    // The config band moved to its own tier (between Port types and Contracts):
    // it appears from `config` up, NOT at the `types` tier.
    config:   t === 'config' || t === 'contract' || t === 'full',
    contract: t === 'contract' || t === 'full',
    full:     t === 'full',
  };
  // Per-feature Detail overrides (the Detail menu) layer on top of the tier: each
  // feature is 'auto' (keep the tier value) or forced. Never applied to a pinned-
  // open card (that always shows everything).
  const ov = (data as any)._detailOverrides as
    { ports?: string; config?: string; contract?: string; figures?: string } | undefined;
  if (ov && !(data as any)._pinnedOpen) {
    if (ov.ports === 'none')  { show.ports = false; show.types = false; }
    else if (ov.ports === 'plain') { show.ports = true;  show.types = false; }
    else if (ov.ports === 'types') { show.ports = true;  show.types = true; }
    if (ov.config === 'on')  show.config = true;  else if (ov.config === 'off')  show.config = false;
    // 'full' = show the contract AND its full extended description (what you get
    // by clicking the process open), not just the summary.
    if (ov.contract === 'on') show.contract = true;
    else if (ov.contract === 'off') show.contract = false;
    else if (ov.contract === 'full') { show.contract = true; show.full = true; }
  }

  const contract = show.contract ? deriveContract(data) : null;
  const completeness = show.full ? contractCompleteness(contract, data) : null;
  // Optional per-node illustration: shown when the node carries a `_figure`
  // and Detail → Figures isn't forced off ('auto' shows it when present).
  const showFigure = !!data.figure && ov?.figures !== 'off';
  const inTypes = ((data as any).inputSchema ?? {}) as Record<string, unknown>;
  const outTypes = ((data as any).outputSchema ?? {}) as Record<string, unknown>;
  // Config comes from the declared schema (types + defaults), overlaid with any
  // set values — NOT just `data.config`, which is usually {} (defaults in use).
  const cfg = configParams(
    (data as { configSchema?: Record<string, unknown> }).configSchema,
    data.config,
  );
  // The config band shows real configuration KEYS only — drop the metadata keys
  // (summary / contract / status) that ride along in a draft process's config
  // dict; the summary belongs to the meta/contract, not the config.
  // `interval` is an ORCHESTRATION parameter (how often the process updates),
  // not a domain configuration — drop it from the config band like the other
  // metadata keys so a process that only sets its update interval reads clean.
  const CONFIG_META = new Set(["summary", "contract", "status", "description", "math", "symbols", "ports", "interval"]);
  const realCfg = cfg.filter((c) => !CONFIG_META.has(c.name));

  const topFor = (i: number, n: number) => `${((i + 1) / (n + 1)) * 100}%`;

  // Connection dots sit ON the card border (inputs left, outputs right) at each
  // port's vertical fraction — that's where wires attach, at every tier.
  const borderHandle = (
    port: string, isOut: boolean, i: number, n: number, types: Record<string, unknown>,
  ) => (
    <Handle
      key={`h-${isOut ? 'o' : 'i'}-${port}`}
      type={isOut ? 'source' : 'target'}
      position={isOut ? Position.Right : Position.Left}
      id={port}
      className={`port-handle ${isOut ? 'port-handle-output' : 'port-handle-input'}`}
      title={handleTitle(port, isOut, types)}
      style={{ top: topFor(i, n) }}
    />
  );

  // Port names live INSIDE the card in a left column (inputs) and a right column
  // (outputs), each aligned to its dot. The center content is margined clear of
  // these columns, so the card reads inputs → contract → outputs spatially.
  const insideLabel = (
    port: string, types: Record<string, unknown>, isOut: boolean, i: number, n: number,
  ) => {
    const info = portInfo(port, isOut, {
      typeSchema: types,
      portsSchema: isOut ? (data.outputPortsSchema ?? undefined) : (data.inputPortsSchema ?? undefined),
      portsTarget: isOut ? (data.outputPortsTarget ?? undefined) : (data.inputPortsTarget ?? undefined),
    });
    const key = `${isOut ? 'o' : 'i'}-${port}`;
    const open = openPort === key;
    const semantic = isOut ? contract?.outputs?.[port] : contract?.inputs?.[port];
    return (
      <div
        key={`${isOut ? 'o' : 'i'}lbl-${port}`}
        className={`port-in-label ${isOut ? 'is-out' : 'is-in'}${open ? ' is-open' : ''}`}
        style={{ top: topFor(i, n) }}
        title={handleTitle(port, isOut, types)}
        onClick={(e) => { e.stopPropagation(); setOpenPort(open ? null : key); }}
      >
        <span className="port-in-name">{port}</span>
        {show.types && info.type && (
          <span className="port-in-type" title={info.fullType}>{info.type}</span>
        )}
        {open && (
          <div className={`port-popover ${isOut ? 'is-out' : 'is-in'}`} onClick={(e) => e.stopPropagation()}>
            <div className="port-popover-head">
              <span className="port-popover-name">{port}</span>
              <span className={`port-popover-dir ${info.direction}`}>{info.direction}</span>
            </div>
            <div className="port-popover-row">
              <span className="port-popover-key">connects to</span>
              <span className="port-popover-val mono">{info.connectsTo || '(unwired)'}</span>
            </div>
            {info.fullType && (
              <div className="port-popover-row">
                <span className="port-popover-key">type</span>
                <span className="port-popover-val mono">{info.fullType}</span>
              </div>
            )}
            {semantic && <div className="port-popover-sem">{semantic}</div>}
          </div>
        )}
      </div>
    );
  };

  // A terse native tooltip for the raw handle (present at every tier, incl.
  // glyph where no port rows are drawn) so hovering the connector itself still
  // reveals direction + target.
  const handleTitle = (port: string, isOut: boolean, types: Record<string, unknown>) => {
    const info = portInfo(port, isOut, {
      typeSchema: types,
      portsSchema: isOut ? (data.outputPortsSchema ?? undefined) : (data.inputPortsSchema ?? undefined),
      portsTarget: isOut ? (data.outputPortsTarget ?? undefined) : (data.inputPortsTarget ?? undefined),
    });
    const parts = [`${port} · ${info.direction}`];
    if (info.connectsTo) parts.push(`→ ${info.connectsTo}`);
    if (info.type) parts.push(`(${info.type})`);
    return parts.join(' ');
  };

  const locked = (data as any)._locked === true;
  // Lineage spotlight (see StoreNode): highlighted on / dimmed off the selected
  // node's place-graph containment path.
  const isLineage = (data as any)._lineage === true;
  const isWired = (data as any)._wired === true;   // wire-connected to the selection
  const isDim = (data as any)._dim === true;

  // Manual resize: drag a corner to pull the card bigger. In-session inline
  // override of the tier size. The corner handles are HIDDEN by default and
  // revealed only on card hover (CSS), so they don't clutter every card — the
  // functionality is there, just not always visible.
  // Hand-set size persists on data._size (saved with the layout/default view);
  // local `dims` gives smooth drag feedback, seeded from + synced to it.
  const _rf = useReactFlow();
  const _nodeId = useNodeId();
  const nodeRef = useRef<HTMLDivElement>(null);
  const savedSize = (data as any)._size as { width: number; height: number } | undefined;
  const [dims, setDims] = useState<{ width: number; height: number } | null>(savedSize ?? null);
  useEffect(() => {
    if (savedSize) setDims(savedSize);
  }, [savedSize?.width, savedSize?.height]);
  const commitSize = (w: number, h: number) => {
    if (!_nodeId) return;
    // Route through App's commit so the size lands in the persisted node state
    // (a bare _rf.setNodes writes React Flow's store but not useNodesState, so
    // the size silently reset on the next reload).
    const commit = (data as any)._commitSize as
      | ((id: string, s: { width: number; height: number }) => void) | undefined;
    if (commit) commit(_nodeId, { width: w, height: h });
    else _rf.setNodes((ns: any[]) => ns.map((n) =>
      n.id === _nodeId ? { ...n, data: { ...n.data, _size: { width: w, height: h } } } : n));
  };
  // Snap-to-fit: release the explicit size so the card shrink-wraps to its
  // content (min-width/height only), then measure that natural box and persist
  // it — the process rectangle collapses down to the smallest size that still
  // fits its content (just the name, when that's all it shows).
  const snapToFit = (e: React.MouseEvent) => {
    e.stopPropagation();
    setDims(null);
    requestAnimationFrame(() => {
      const el = nodeRef.current;
      if (!el) return;
      commitSize(Math.ceil(el.offsetWidth), Math.ceil(el.offsetHeight));
    });
  };
  // Which port's info popover is open (click a port name). Keyed 'i-'/'o-'+port.
  const [openPort, setOpenPort] = useState<string | null>(null);
  // Which middle section's detail is expanded (click a center box). 'config'
  // opens a values+types popover; 'contract' reveals the full description.
  const [openSection, setOpenSection] = useState<'config' | 'contract' | null>(null);
  const toggleSection = (s: 'config' | 'contract') =>
    setOpenSection((cur) => (cur === s ? null : s));
  // Live width while dragging the port-label column resizer (null = not dragging).
  const [dragCol, setDragCol] = useState<number | null>(null);
  const dragColRef = useRef(0);

  // The card's minimum height must fit BOTH its text (CSS min-content) AND its
  // evenly-spaced port rows. Ports are absolutely positioned (out of flow), so
  // min-content can't see them — feed the room they need as a CSS var that the
  // min-height max()es against, so squeezing never overlaps the ports.
  const maxPorts = show.ports ? Math.max(inputPorts.length, outputPorts.length) : 0;
  const portsMinH = maxPorts > 0 ? (maxPorts + 1) * 46 : 0;

  // Adaptive port-column width: reserve EXACTLY the widest port label (name, or
  // its type when types show) needs — MEASURED at the tier's real font and capped
  // by the CSS max-width. Short-port cards stop squishing their middle text into a
  // sliver; wide types ("concentration") still get enough room to never overlap
  // it. A fixed margin could only ever be right for one of those.
  const bigPorts = t === 'ports' || t === 'types' || t === 'config';  // 24px names (App.css)
  const nameFont = `650 ${bigPorts ? 24 : 19}px Inter, system-ui, sans-serif`;
  const typeFont = `${bigPorts ? 17 : 15}px ui-monospace, monospace`;
  // Auto column caps the widest label so a runaway name can't blow out the card.
  // Raised well past the old 140/150 so ordinary long port names ("mechanical_ext",
  // "nucleic_acids") show in FULL under auto; a hand-set _portCol overrides it.
  const labelCap = bigPorts ? 320 : 300;
  let widestLabel = 0;
  if (show.ports) {
    const measurePort = (p: string, isOut: boolean, types: Record<string, unknown>) => {
      widestLabel = Math.max(widestLabel, measureLabel(String(p), nameFont, bigPorts ? 15 : 12));
      if (show.types) {
        const info = portInfo(p, isOut, {
          typeSchema: types,
          portsSchema: isOut ? (data.outputPortsSchema ?? undefined) : (data.inputPortsSchema ?? undefined),
          portsTarget: isOut ? (data.outputPortsTarget ?? undefined) : (data.inputPortsTarget ?? undefined),
        });
        if (info.type) widestLabel = Math.max(widestLabel, measureLabel(info.type, typeFont, bigPorts ? 11 : 9));
      }
    };
    inputPorts.forEach((p) => measurePort(p, false, inTypes));
    outputPorts.forEach((p) => measurePort(p, true, outTypes));
  }
  // 13px = the .port-in-label left/right offset; +10px clearance from the center.
  const autoCol = Math.round(13 + Math.min(labelCap, widestLabel) + 10);
  // _portCol: undefined = auto; 0 = ports COLLAPSED (hidden for this process);
  // >0 = a hand-set width (drag). During a drag, dragCol is the live value.
  const savedPortCol = (data as { _portCol?: number })._portCol;
  const effPortCol = dragCol != null ? dragCol : savedPortCol;
  const portsCollapsed = show.ports && effPortCol === 0;
  const showPortLabels = show.ports && !portsCollapsed;
  const portCol = !show.ports ? 14
    : portsCollapsed ? 16
      : (effPortCol != null && effPortCol > 0 ? effPortCol : autoCol);
  const commitPortCol = (data as { _commitPortCol?: (id: string, pc: number) => void })._commitPortCol;
  const startColDrag = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (!_nodeId || !commitPortCol) return;
    const start = (savedPortCol != null && savedPortCol > 0) ? savedPortCol : autoCol;
    const startX = e.clientX;
    const zoom = (_rf.getViewport?.().zoom) || 1;
    dragColRef.current = start;
    const move = (ev: MouseEvent) => {
      let w = Math.round(start + (ev.clientX - startX) / zoom);
      if (w < 28) w = 0;                              // dragged fully shut → collapse
      else w = Math.max(48, Math.min(600, w));
      dragColRef.current = w;
      setDragCol(w);
    };
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      commitPortCol(_nodeId, dragColRef.current);
      setDragCol(null);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  return (
    <div
      ref={nodeRef}
      className={`process-node process-node-${stepKind} process-node-${t}${locked ? ' is-locked' : ''}${isLineage ? ' is-lineage' : ''}${isWired ? ' is-wired' : ''}${isDim ? ' is-dim' : ''}${!show.ports ? ' process-node-noports' : ''}${dims ? ' is-sized' : ''}`}
      style={{
        ...(dims ? { width: dims.width, height: dims.height, overflow: 'visible' } : {}),
        // A hand-set / saved node height wins: cap the port-driven min-height to
        // it so a committed default view renders at its saved box size instead of
        // being stretched taller by the port rows (ports still show at the edges
        // via overflow:visible). Without a saved size, keep the full ports room.
        ['--ports-min-h' as string]: `${dims ? Math.min(portsMinH, dims.height) : portsMinH}px`,
        ['--port-col' as string]: `${portCol}px`,
      } as React.CSSProperties}
    >
      <NodeResizer
        isVisible
        minWidth={120}
        minHeight={48}
        onResize={(_e, p) => setDims({ width: p.width, height: p.height })}
        onResizeEnd={(_e, p) => commitSize(p.width, p.height)}
        handleClassName="loom-resize-handle"
        lineClassName="loom-resize-line"
      />
      {/* Snap-to-fit: collapse the rectangle down to its smallest content-fit
          size. Hover-revealed so it never clutters; crop-mark glyph reads as
          "shrink to frame". */}
      <button
        type="button"
        className="process-node-fit-btn nodrag nopan"
        title="Snap to smallest size — fit the rectangle to its content (the name)"
        onClick={snapToFit}
      >
        <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
          <path d="M6 2H2v4M10 2h4v4M6 14H2v-4M10 14h4v-4" fill="none"
                stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"
                strokeLinejoin="round" />
        </svg>
      </button>
      {/* Place-graph handles: a process nested inside a store (e.g. a Composite
          Process under a `simulations` store) is a place-graph child, so it needs
          the same top/bottom place anchors a store has — otherwise the parent's
          nesting edge has nothing to attach to and doesn't render. */}
      <Handle type="target" position={Position.Top} id="top-place" style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} id="bottom-place" style={{ opacity: 0 }} />
      {/* Connection dots on the border (all tiers, so focused wiring attaches). */}
      {inputPorts.map((p, i) => borderHandle(p, false, i, inputPorts.length, inTypes))}
      {outputPorts.map((p, i) => borderHandle(p, true, i, outputPorts.length, outTypes))}

      {/* Port-name columns INSIDE the card: inputs left, outputs right. */}
      {showPortLabels && inputPorts.map((p, i) => insideLabel(p, inTypes, false, i, inputPorts.length))}
      {showPortLabels && outputPorts.map((p, i) => insideLabel(p, outTypes, true, i, outputPorts.length))}
      {/* Port-column resizer: drag to widen the port-label column (labels stop
          truncating); drag fully shut to COLLAPSE (hide) this process's ports.
          The width (or the collapsed state) is saved as part of the view. */}
      {show.ports && commitPortCol && (
        <div
          className={`port-col-resizer nodrag nopan${portsCollapsed ? ' is-collapsed' : ''}`}
          style={{ left: 'var(--port-col)' }}
          onMouseDown={startColDrag}
          title={portsCollapsed
            ? 'Ports hidden for this process — drag right to show them'
            : 'Drag to widen the port-label column; drag fully left to hide this process’s ports (saved in the view)'}
        />
      )}

      {/* Center channel — margined clear of the port columns. Reads top-down:
          config (from above) → title → the contract (what inputs become). The
          left/right port columns supply the inputs→outputs framing spatially,
          so no abstract ƒ(inputs; config)→outputs line is needed. */}
      <div className="process-node-center">
        {show.config && realCfg.length > 0 && (
          <div className="process-node-config-band" title={SECTION_HINT.config}>
            <span className="config-band-caret">config</span>
            {realCfg.slice(0, 10).map((c) => (
              <span key={c.name} className="config-chip" title={`${c.name}: ${c.type || '—'}${c.value ? ' = ' + c.value : ''}`}>
                <span className="config-key">{c.name}</span>
                {/* Show the set VALUE inline (e.g. pmf_volts = 0.15) — the numeric
                    parametrization, not just the parameter surface. */}
                {c.scalar && c.value && c.value !== '—' && (
                  <span className="config-val">= {c.value}</span>
                )}
                {/* Types ride the port-detail level: shown once ports show types. */}
                {show.types && c.type && <span className="config-type">{c.type}</span>}
              </span>
            ))}
            {realCfg.length > 10 && (
              <span className="config-more">+{realCfg.length - 10} more…</span>
            )}
          </div>
        )}

        <div className="process-node-title">
          {locked && <span className="process-node-lock" title="Locked — click empty canvas to unlock">🔒</span>}
          {displayName(data.label)}
          {/* Collapsed array of identical processes → how many this stands for. */}
          {(data as any)._collapsedCount > 1 && (
            <span className="process-node-count" title={`${(data as any)._collapsedCount} identical processes collapsed into this one`}>
              ×{(data as any)._collapsedCount}
            </span>
          )}
          {/* No separate DRAFT badge — the meta line below reads "draft process"
              instead of "process" (more discrete, shown from the ports tier up). */}
          {data.isCompositeProcess && (
            <span
              className="process-node-drill"
              title="Composite Process — double-click to open its inner composite"
            >
              ⤢
            </span>
          )}
        </div>
        {show.ports && (
          <div className="process-node-meta" title={SECTION_HINT.meta}>
            {/* The simulation method / model type (e.g. "Agent-Based Model") when
                the process declares one, else "draft process" / "process". The
                in/out counts are omitted — the port columns show the arity. */}
            {(data as any).method
              ? (data as any).method
              : ((data as any).isDraft ? `draft ${data.processType}` : data.processType)}
            {data.interval != null && <span> · every {data.interval}</span>}
          </div>
        )}

        {/* Optional illustrative figure (Detail → Figures). Inline SVG string is
            injected verbatim; anything else (data-URI / url) renders as <img>.
            When the contract is showing the card is already tall with prose +
            equations, so tuck the figure into the top-left corner (absolute, out
            of flow) instead of a centered row that costs vertical space. */}
        {showFigure && (
          <div className={`node-figure${show.contract ? ' is-corner' : ''}`}>
            {data.figure!.trimStart().startsWith('<svg')
              ? <span className="node-figure-svg" dangerouslySetInnerHTML={{ __html: data.figure! }} />
              : <img className="node-figure-img" src={data.figure} alt="" draggable={false} />}
          </div>
        )}

        {/* Composite Process: render a live mini-map of its INNER composite in
            place of the contract. Shown from the `types` tier up — INDEPENDENT of
            the contract toggle, so a composite still shows its inner map even when
            contracts are turned off (Detail → Contract: Hide). Auto-loads only at
            `full` tier (the focused/most-zoomed card, and the headless figure
            render); at lower tiers it shows the ⤢ viz icon to render on demand, so
            zoom doesn't trigger a ParCa build on every card at once. */}
        {show.ports && data.isCompositeProcess
          && ((data as any)._rootId || (data.config as any)?.state) && (
          <InnerCompositePreview
            rootId={(data as any)._rootId ?? ''}
            hops={[...(((data as any)._hops as string[][]) ?? []), data.path]}
            localState={(data.config as any)?.state}
            viewPos={(data.config as any)?._inner_view?.positions}
            bridge={{ inputs: inputPorts, outputs: outputPorts }}
            auto
          />
        )}

        {/* The contract: a justified recital of what the process does, over its
            governing equations. Only shown when actually documented. Click to
            reveal the full description below (when there is one to reveal).
            Composite Processes show their inner mini-map above instead. */}
        {show.contract && !data.isCompositeProcess && contract?.summary && (
          <div
            className={`process-contract section-box${openSection === 'contract' ? ' is-open' : ''}`}
            title={SECTION_HINT.contract}
            onClick={(e) => {
              if (!contract.description || contract.description === contract.summary) return;
              e.stopPropagation(); toggleSection('contract');
            }}
          >
            <p className="contract-recital"><MathText text={contract.summary} /></p>
          </div>
        )}

        {show.contract && contract && contract.math.length > 0 && (
          <div className="process-node-math" title={SECTION_HINT.math}>
            {contract.math.map((m, i) => <KatexBlock key={i} tex={toLatex(m)} />)}
          </div>
        )}

        {show.full && contract && Object.keys(contract.symbols).length > 0 && (
          <div className="process-node-symbols" title={SECTION_HINT.symbols}>
            {Object.entries(contract.symbols).map(([s, meaning]) => (
              <div key={s}><em><MathText text={s} symbolKey /></em> — <MathText text={meaning} /></div>
            ))}
          </div>
        )}

        {/* Full description: shown at the `full` tier, OR on demand when the
            contract box is clicked at the `contract` tier. */}
        {(show.full || openSection === 'contract') && contract?.description
          && contract.description !== contract.summary && (
          <div className="process-node-description"><MathText text={contract.description} /></div>
        )}

        {show.types && (data as any).address && (
          <div className="process-node-address">{(data as any).address}</div>
        )}

        {show.full && show.contract && completeness && completeness.total > 0 && (
          <div className="process-node-completeness">
            {completeness.documented}/{completeness.total} ports documented
            {completeness.unknownPorts.length > 0 && (
              <span className="is-warn"> · unknown: {completeness.unknownPorts.join(', ')}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(ProcessNode);
