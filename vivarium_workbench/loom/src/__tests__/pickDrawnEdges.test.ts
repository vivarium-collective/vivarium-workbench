import { describe, it, expect } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import { pickDrawnEdges } from '../layouts/pickDrawnEdges';
import { hierarchyMode } from '../layouts/hierarchy';
import { processColumnMode } from '../layouts/processColumn';

const nodes: Node[] = [
  { id: 'p1', type: 'process', position: { x: 0, y: 0 }, data: { label: 'p1' } },
] as unknown as Node[];

const edges: Edge[] = [
  { id: 'e1', source: 'unique.RNA', target: 'p1', data: { edgeType: 'input' } },
  { id: 'place--r--c', source: 'unique', target: 'unique.RNA', data: { edgeType: 'place' } },
] as unknown as Edge[];

const emptyFocus = { focused: new Set<string>(), pinned: new Set<string>() };

describe('pickDrawnEdges', () => {
  it('hierarchy mode (no edgeVisibility) returns the SAME array identity', () => {
    // This is the strongest form of "hierarchy is unaffected": not just equal
    // contents (toEqual), but literally the same object — proof nothing was
    // filtered, copied, or otherwise touched.
    expect(pickDrawnEdges(hierarchyMode, edges, emptyFocus, nodes)).toBe(edges);
  });

  it('hierarchy mode ignores focus/pins entirely — still identity', () => {
    const busyFocus = { focused: new Set(['p1']), pinned: new Set(['p1']) };
    expect(pickDrawnEdges(hierarchyMode, edges, busyFocus, nodes)).toBe(edges);
  });

  it('process-column mode culls wire edges when nothing is focused', () => {
    const out = pickDrawnEdges(processColumnMode, edges, emptyFocus, nodes);
    expect(out.some((e) => (e.data as any).edgeType === 'input')).toBe(false);
    // structural place edges always survive
    expect(out.find((e) => e.id === 'place--r--c')).toBeTruthy();
  });

  it('process-column mode reveals wires touching a focused node', () => {
    const out = pickDrawnEdges(
      processColumnMode, edges, { focused: new Set(['p1']), pinned: new Set() }, nodes,
    );
    expect(out.find((e) => e.id === 'e1')).toBeTruthy();
  });

  it('process-column mode returns the SAME identity when nothing is culled', () => {
    const placeOnly = edges.filter((e) => (e.data as any).edgeType === 'place');
    expect(pickDrawnEdges(processColumnMode, placeOnly, emptyFocus, nodes)).toBe(placeOnly);
  });
});
