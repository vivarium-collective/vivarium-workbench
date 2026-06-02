import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { StoreNodeData } from "../types";

function StoreNode({ data }: NodeProps & { data: StoreNodeData }) {
  const hasValue = data.value !== undefined && data.value !== null;
  const isCollapsed = (data as any).isCollapsed;

  return (
    <div className={`store-node ${isCollapsed ? "store-node-collapsed" : ""}`}>
      {/* Wiring convention:
       *   LEFT  source — store value flows OUT to a process's input (left side of process)
       *   RIGHT target — store RECEIVES a process's output (right side of process)
       *   TOP   target — place edge IN from parent store
       *   BOTTOM source — place edge OUT to child store
       */}
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

export default memo(StoreNode);
