import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { StoreNodeData } from "../types";
import type { ZoomTierId } from "../layouts/types";
import { abbreviateType } from "../contract";

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

  // Hierarchy mode never stamps a tier — render the legacy circle unchanged so
  // that mode is byte-identical to before semantic zoom existed.
  if (tierRaw == null) {
    const hasValue = data.value !== undefined && data.value !== null;
    return (
      <div className={`store-node ${isCollapsed ? "store-node-collapsed" : ""}`}>
        <Handle type="target" position={Position.Top} id="top-place" />
        <Handle type="source" position={Position.Left} id="left-out" />
        <Handle type="target" position={Position.Right} id="right-in" />
        <div className="store-label">{data.label}</div>
        {hasValue && (
          <div className="store-value" title={String(data.value)}>
            {String(data.value).slice(0, 20)}
          </div>
        )}
        {(data as any).isGroup && (
          <div className="collapse-indicator">
            {isCollapsed ? "▶" : "▼"}
          </div>
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
  const readers: string[] = (data as any)._readers ?? [];
  const writers: string[] = (data as any)._writers ?? [];
  const rawType = typeof (data as any).valueType === "string" ? (data as any).valueType : "";

  const show = {
    value: tier !== "glyph",
    type: tier === "types" || tier === "contract" || tier === "full",
    wiring: tier === "contract" || tier === "full",
    full: tier === "full",
  };

  return (
    <div className={`store-node store-node-${tier} ${isCollapsed ? "store-node-collapsed" : ""}`}>
      <StoreHandles />
      <div className="store-label">{data.label}</div>
      {show.value && data.value != null && (
        <div className="store-node-value">{String(data.value)}</div>
      )}
      {show.type && rawType && (
        <div className="store-node-type" title={rawType}>{abbreviateType(rawType)}</div>
      )}
      {show.wiring && (readers.length > 0 || writers.length > 0) && (
        <div className="store-node-wiring">
          {readers.length > 0 && <span>{readers.length} read</span>}
          {readers.length > 0 && writers.length > 0 && <span> · </span>}
          {writers.length > 0 && <span>{writers.length} write</span>}
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
