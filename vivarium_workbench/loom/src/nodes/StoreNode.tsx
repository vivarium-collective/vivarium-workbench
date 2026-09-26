import { memo, useEffect, useState } from "react";
import { Handle, Position, NodeResizer, useReactFlow, useNodeId, type NodeProps } from "@xyflow/react";
import type { StoreNodeData } from "../types";
import type { ZoomTierId } from "../layouts/types";
import { abbreviateType } from "../contract";
import { displayName } from "../labels";

/** The four store handles + optional collapse indicator, shared by both the
 *  legacy (hierarchy) and the tiered (process-column) render paths. Edge
 *  attachment (Task 6 focus culling) depends on these handle ids, so they are
 *  present at every tier. */
function StoreHandles() {
  // Wiring convention:
  //   LEFT  source — store value flows OUT to a process's input (left of process)
  //   RIGHT target — store RECEIVES a process's output (right of process)
  //   TOP   target — place edge IN from parent store
  //   BOTTOM source — place edge OUT to child store
  return (
    <>
      <Handle type="target" position={Position.Top} id="top-place" />
      <Handle type="source" position={Position.Left} id="left-out" />
      <Handle type="target" position={Position.Right} id="right-in" />
      <Handle type="source" position={Position.Bottom} id="bottom-place" />
    </>
  );
}

function StoreNode({ data }: NodeProps & { data: StoreNodeData }) {
  const isCollapsed = (data as any).isCollapsed;
  const tierRaw = (data as any)._tier as ZoomTierId | undefined;

  // Manual resize: drag a corner to widen/enlarge the store, just like a
  // process card. Handles are hidden by CSS until the node is hovered.
  // Hand-set size persists on data._size (saved with the layout/default view);
  // local `dims` gives smooth drag feedback, seeded from + synced to it.
  const _rf = useReactFlow();
  const _nodeId = useNodeId();
  const savedSize = (data as any)._size as { width: number; height: number } | undefined;
  const [dims, setDims] = useState<{ width: number; height: number } | null>(savedSize ?? null);
  useEffect(() => {
    if (savedSize) setDims(savedSize);
  }, [savedSize?.width, savedSize?.height]);
  // Override the tier's max-width/height caps too, or the inline width can't
  // grow past them and a drag just shifts the box instead of widening it.
  const sizeStyle = dims
    ? { width: dims.width, height: dims.height, maxWidth: dims.width, maxHeight: dims.height }
    : undefined;
  const resizer = (
    <NodeResizer
      isVisible
      minWidth={72}
      minHeight={44}
      onResize={(_e, p) => setDims({ width: p.width, height: p.height })}
      onResizeEnd={(_e, p) => {
        // Route through App's commit so the size lands in the persisted node
        // state (a bare _rf.setNodes would reset on the next reload).
        const commit = (data as any)._commitSize as
          | ((id: string, s: { width: number; height: number }) => void) | undefined;
        if (_nodeId && commit) commit(_nodeId, { width: p.width, height: p.height });
        else if (_nodeId) _rf.setNodes((ns: any[]) => ns.map((n) =>
          n.id === _nodeId ? { ...n, data: { ...n.data, _size: { width: p.width, height: p.height } } } : n));
      }}
      handleClassName="loom-resize-handle"
      lineClassName="loom-resize-line"
    />
  );

  // Hyperedge marker (Milner view): the process is FULLY collapsed — no node
  // object at all, just a single point vertex where its (dashed) hyperedges
  // converge. All handles are centered on the point so every edge radiates from
  // it; the label sits just beneath.
  if ((data as any)._hyperedge) {
    return (
      <div className="loom-hyperedge" title={data.label}>
        <StoreHandles />
        <span className="loom-hyperedge-label">{displayName(data.label)}</span>
      </div>
    );
  }

  // The store's key color (shared with its wires + the port dots that bind it),
  // painted as the border so "the teal store" reads as one with its teal wires (#1).
  const accent = (data as StoreNodeData).storeColor;
  const accentStyle = accent ? { borderColor: accent } : undefined;

  // Hierarchy mode never stamps a tier — render the legacy circle unchanged so
  // that mode is byte-identical to before semantic zoom existed.
  if (tierRaw == null) {
    const hasValue = data.value !== undefined && data.value !== null;
    return (
      <div className={`store-node ${isCollapsed ? "store-node-collapsed" : ""}`} style={{ ...sizeStyle, ...accentStyle }}>
        {resizer}
        <Handle type="target" position={Position.Top} id="top-place" />
        <Handle type="source" position={Position.Left} id="left-out" />
        <Handle type="target" position={Position.Right} id="right-in" />
        <div className="store-label">{displayName(data.label)}</div>
        {hasValue && (
          <div className="store-value" title={String(data.value)}>
            {String(data.value).slice(0, 20)}
          </div>
        )}
        {(data as any).isGroup && isCollapsed && (
          <div className="collapse-indicator">▶</div>
        )}
        <Handle type="source" position={Position.Bottom} id="bottom-place" />
      </div>
    );
  }

  // Semantic zoom (process-column mode): the stamped tier decides WHICH rows
  // exist. Font size is constant across tiers (see App.css) — legibility at low
  // zoom comes from dropping content, never from shrinking text. A row with no
  // data is omitted, never rendered empty.
  const tier = tierRaw;
  const isHub: boolean = (data as any)._isHub === true;
  // Lineage spotlight: App flags a store on the selected node's containment path
  // (`is-lineage`, highlighted) or off it (`is-dim`, faded) so the hierarchy
  // around a selection stands out.
  const isLineage: boolean = (data as any)._lineage === true;
  const isWired: boolean = (data as any)._wired === true;   // wire-connected to the selection
  const isDim: boolean = (data as any)._dim === true;
  const rawType = typeof (data as any).valueType === "string" ? (data as any).valueType : "";

  const show = {
    value: tier !== "glyph",
    type: tier === "types" || tier === "config" || tier === "contract" || tier === "full",
    full: tier === "full",
  };
  // Stores Detail override (the Detail menu) controls how much of a store shows:
  //   'name'  → just the label   'value' → + value   'type' → + value + type
  const _ov = (data as any)._detailOverrides as { stores?: string; figures?: string } | undefined;
  if (_ov) {
    if (_ov.stores === "name")  { show.value = false; show.type = false; }
    else if (_ov.stores === "value") { show.value = true;  show.type = false; }
    else if (_ov.stores === "type")  { show.value = true;  show.type = true; }
  }
  // Optional per-store illustration (Detail → Figures; 'auto' shows when present).
  const showFigure = !!(data as any).figure && _ov?.figures !== "off";
  const figure: string | undefined = (data as any).figure;

  return (
    <div
      className={`store-node store-node-${tier} ${isCollapsed ? "store-node-collapsed" : ""} ${isHub ? "store-node-hub" : ""} ${isLineage ? "is-lineage" : ""} ${isWired ? "is-wired" : ""} ${isDim ? "is-dim" : ""}`}
      style={{ ...sizeStyle, ...accentStyle }}
    >
      {resizer}
      <StoreHandles />
      <div className="store-label">
        {displayName(data.label)}
        {/* Collapsed group of identical sibling stores → how many this stands for. */}
        {(data as any)._collapsedCount > 1 && (
          <span
            className="store-node-count"
            title={`${(data as any)._collapsedCount} identical sibling stores collapsed into this one`}
          >
            {" "}×{(data as any)._collapsedCount}
          </span>
        )}
      </div>
      {show.value && data.value != null && (
        <div className="store-node-value">{String(data.value)}</div>
      )}
      {show.type && rawType && (
        <div className="store-node-type" title={rawType}>{abbreviateType(rawType)}</div>
      )}
      {showFigure && (
        <div className="node-figure">
          {figure!.trimStart().startsWith("<svg")
            ? <span className="node-figure-svg" dangerouslySetInnerHTML={{ __html: figure! }} />
            : <img className="node-figure-img" src={figure} alt="" draggable={false} />}
        </div>
      )}
      {show.full && (data as any)._emitted && (
        <div className="store-node-emit">emitted</div>
      )}
      {(data as any).isGroup && (
        <div className="collapse-indicator">
          {isCollapsed ? "▶" : "▼"}
        </div>
      )}
    </div>
  );
}

export default memo(StoreNode);
