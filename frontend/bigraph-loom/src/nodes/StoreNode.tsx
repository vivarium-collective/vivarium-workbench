import type React from "react";
import { memo, useContext } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { StoreNodeData } from "../types";
import { EmitContext } from "../EmitContext";

function StoreNode({ data }: NodeProps & { data: StoreNodeData }) {
  const hasValue = data.value !== undefined && data.value !== null;
  const isCollapsed = (data as any).isCollapsed;

  // Determine emit state from the broadcast context. A store emits if its own
  // path is in the explicit-emit set, or if any ancestor prefix is.
  const emitSet = useContext(EmitContext);
  const path: string[] = (data as any).path ?? [];
  const explicitEmit = emitSet.has(path.join('/'));
  let inheritedEmit = false;
  for (let i = 0; i < path.length - 1; i++) {
    if (emitSet.has(path.slice(0, i + 1).join('/'))) {
      inheritedEmit = true;
      break;
    }
  }
  const emit = explicitEmit || inheritedEmit;

  // Highlight emitting stores with a green ring; an explicit emit gets a
  // bolder dot in the corner, inherited gets a subtler one.
  const wrapperStyle: React.CSSProperties | undefined = emit
    ? { boxShadow: '0 0 0 2px #22c55e', borderRadius: '50%' }
    : undefined;

  return (
    <div className={`store-node ${isCollapsed ? "store-node-collapsed" : ""}`} style={wrapperStyle}>
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
      {emit && (
        <div
          title={explicitEmit ? 'Emitting' : 'Inherits emit from ancestor'}
          style={{
            position: 'absolute',
            top: 2,
            right: 4,
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: '#22c55e',
            opacity: explicitEmit ? 1 : 0.55,
            border: '1px solid #ffffff',
          }}
        />
      )}
      <Handle type="source" position={Position.Bottom} id="bottom-place" />
    </div>
  );
}

export default memo(StoreNode);
