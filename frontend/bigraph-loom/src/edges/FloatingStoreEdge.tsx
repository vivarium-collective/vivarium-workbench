// src/edges/FloatingStoreEdge.tsx — custom edge for process<->store wires.
//
// The process end stays pinned to its port handle (inputs left, outputs
// right). The store end floats: it attaches at the point on the store circle
// nearest that port, instead of a fixed left/right handle.
import { memo } from 'react';
import {
  BaseEdge, getBezierPath, useInternalNode, Position,
  type EdgeProps,
} from '@xyflow/react';
import { circleAnchor, dominantSide, type Point, type Side } from './geometry';

type RFInternalNode = NonNullable<ReturnType<typeof useInternalNode>>;

const SIDE_TO_POSITION: Record<Side, Position> = {
  left: Position.Left,
  right: Position.Right,
  top: Position.Top,
  bottom: Position.Bottom,
};

/** Absolute center of a node from its measured box. */
function nodeCenter(n: RFInternalNode): Point {
  const p = n.internals.positionAbsolute;
  return {
    x: p.x + (n.measured.width ?? 0) / 2,
    y: p.y + (n.measured.height ?? 0) / 2,
  };
}

/** Absolute position of a node's handle by id; falls back to the node center. */
function handlePoint(n: RFInternalNode, handleId: string | null | undefined): Point {
  const bounds = [
    ...(n.internals.handleBounds?.source ?? []),
    ...(n.internals.handleBounds?.target ?? []),
  ];
  const h = handleId ? bounds.find((b) => b.id === handleId) : undefined;
  if (!h) return nodeCenter(n);
  const p = n.internals.positionAbsolute;
  return { x: p.x + h.x + h.width / 2, y: p.y + h.y + h.height / 2 };
}

function FloatingStoreEdge({
  source, target, sourceHandleId, targetHandleId, markerEnd, style, data,
}: EdgeProps) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  if (!sourceNode || !targetNode) return null;

  // edgeType 'input'  → source = store,   target = process
  // edgeType 'output' → source = process, target = store
  const storeIsSource = (data as { edgeType?: string } | undefined)?.edgeType === 'input';
  const storeNode = storeIsSource ? sourceNode : targetNode;
  const procNode = storeIsSource ? targetNode : sourceNode;
  const procHandleId = storeIsSource ? targetHandleId : sourceHandleId;

  // Process end: the exact port handle position (fixed).
  const procPoint = handlePoint(procNode, procHandleId);

  // Store end: nearest point on the store circle to that port.
  const center = nodeCenter(storeNode);
  const radius = (storeNode.measured.width ?? 0) / 2;
  if (radius <= 0) return null;  // not measured yet — RF re-renders when it is
  const storePoint = circleAnchor(center, radius, procPoint);

  const storePosition = SIDE_TO_POSITION[dominantSide(center, procPoint)];
  // Input ports sit on the process's left; output ports on its right.
  const procPosition = storeIsSource ? Position.Left : Position.Right;

  const [path] = getBezierPath({
    sourceX: storeIsSource ? storePoint.x : procPoint.x,
    sourceY: storeIsSource ? storePoint.y : procPoint.y,
    sourcePosition: storeIsSource ? storePosition : procPosition,
    targetX: storeIsSource ? procPoint.x : storePoint.x,
    targetY: storeIsSource ? procPoint.y : storePoint.y,
    targetPosition: storeIsSource ? procPosition : storePosition,
  });

  return <BaseEdge path={path} markerEnd={markerEnd} style={style} />;
}

export default memo(FloatingStoreEdge);
