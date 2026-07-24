// src/edges/FloatingStoreEdge.tsx — custom edge for process<->store wires.
//
// The process end stays pinned to its port handle (inputs left, outputs
// right). The store end floats: it attaches at the point on the store circle
// nearest that port, instead of a fixed left/right handle.
import { memo } from 'react';
import {
  BaseEdge, EdgeLabelRenderer, getBezierPath, useInternalNode, Position,
  type EdgeProps,
} from '@xyflow/react';
import { circleAnchor, dominantSide, type Point, type Side } from './geometry';
import { abbreviateType } from '../contract';
import type { ZoomTierId } from '../layouts/types';

type RFInternalNode = NonNullable<ReturnType<typeof useInternalNode>>;

export interface EdgeLabelParts {
  port: string;
  portType?: string;
  semantic?: string;
}

/** What a wire says about itself at a given tier. Empty string means no
 *  label at all — which is also the perf path, since ~400 edges labelled
 *  at low zoom costs text layout for glyphs nobody can read. */
export function edgeLabelFor(tier: ZoomTierId, parts: EdgeLabelParts): string {
  if (tier === 'glyph') return '';
  let label = parts.port;
  if (tier === 'ports') return label;
  if (parts.portType) label += `: ${abbreviateType(parts.portType)}`;
  if ((tier === 'contract' || tier === 'full') && parts.semantic) {
    label += ` — ${parts.semantic}`;
  }
  return label;
}

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

  const [path, labelX, labelY] = getBezierPath({
    sourceX: storeIsSource ? storePoint.x : procPoint.x,
    sourceY: storeIsSource ? storePoint.y : procPoint.y,
    sourcePosition: storeIsSource ? storePosition : procPosition,
    targetX: storeIsSource ? procPoint.x : storePoint.x,
    targetY: storeIsSource ? procPoint.y : storePoint.y,
    targetPosition: storeIsSource ? procPosition : storePosition,
  });

  // Semantic zoom is opt-in: only process-column mode stamps `_tier` onto the
  // edge data. Absent it (hierarchy mode, or the glyph tier which App leaves
  // unstamped), draw NO label at all — no EdgeLabelRenderer node, so there is
  // genuinely no text layout to pay for when the canvas is most crowded.
  const stampedTier = (data as { _tier?: ZoomTierId } | undefined)?._tier;
  const label = stampedTier
    ? edgeLabelFor(stampedTier, {
        port: (data as { port?: string } | undefined)?.port ?? '',
        portType: (data as { _portType?: string } | undefined)?._portType,
        semantic: (data as { _semantic?: string } | undefined)?._semantic,
      })
    : '';

  return (
    <>
      <BaseEdge path={path} markerEnd={markerEnd} style={style} />
      {label && (
        <EdgeLabelRenderer>
          <div
            className="loom-edge-label nodrag nopan"
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export default memo(FloatingStoreEdge);
