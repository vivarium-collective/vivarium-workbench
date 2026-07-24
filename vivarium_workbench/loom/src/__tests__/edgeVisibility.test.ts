import { describe, it, expect } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import { processColumnMode } from '../layouts/processColumn';
import { hierarchyMode } from '../layouts/hierarchy';

const nodes: Node[] = [
  { id: 'p1', type: 'process', position: { x: 0, y: 0 },
    data: { label: 'p1', inputPortsSchema: { a: 'unique.RNA' }, outputPortsSchema: {} } },
  { id: 'p2', type: 'process', position: { x: 0, y: 0 },
    data: { label: 'p2', inputPortsSchema: { a: 'unique.RNA' }, outputPortsSchema: {} } },
  { id: 'unique.RNA', type: 'store', position: { x: 0, y: 0 }, data: { label: 'RNA' } },
] as unknown as Node[];

const edges: Edge[] = [
  { id: 'p1--in--a', source: 'unique.RNA', target: 'p1', data: { edgeType: 'input' } },
  { id: 'p1--in--b', source: 'unique.RNA', target: 'p1', data: { edgeType: 'input' } },
  { id: 'p2--in--a', source: 'unique.RNA', target: 'p2', data: { edgeType: 'input' } },
  { id: 'place--r--c', source: 'unique', target: 'unique.RNA', data: { edgeType: 'place' } },
] as unknown as Edge[];

const vis = processColumnMode.edgeVisibility!;

describe('process-column edge visibility', () => {
  it('drops wire edges when nothing is focused', () => {
    const out = vis(edges, { focused: new Set(), pinned: new Set() }, nodes);
    expect(out.some((e) => (e.data as any).edgeType === 'input')).toBe(false);
  });

  it('always keeps structural place edges', () => {
    const out = vis(edges, { focused: new Set(), pinned: new Set() }, nodes);
    expect(out.find((e) => e.id === 'place--r--c')).toBeTruthy();
  });

  it('shows only the focused process wires at full strength', () => {
    const out = vis(edges, { focused: new Set(['p1']), pinned: new Set() }, nodes);
    const ids = out.filter((e) => (e.data as any).edgeType === 'input').map((e) => e.id);
    expect(ids).toEqual(expect.arrayContaining(['p1--in--a', 'p1--in--b']));
    expect(ids).not.toContain('p2--in--a');
  });

  it('unions pinned processes with focused ones', () => {
    const out = vis(edges, { focused: new Set(['p1']), pinned: new Set(['p2']) }, nodes);
    const ids = out.filter((e) => (e.data as any).edgeType === 'input').map((e) => e.id);
    expect(ids).toContain('p1--in--a');
    expect(ids).toContain('p2--in--a');
  });

  it('keeps output wires of a focused process too', () => {
    const withOut = [
      ...edges,
      { id: 'p1--out--z', source: 'p1', target: 'unique.RNA',
        data: { edgeType: 'output' } } as unknown as Edge,
    ];
    const out = vis(withOut, { focused: new Set(['p1']), pinned: new Set() }, nodes);
    expect(out.find((e) => e.id === 'p1--out--z')).toBeTruthy();
  });

  it('returns the SAME array identity when nothing is culled', () => {
    // Cheap-render guarantee: a pass-through must not mint a new array, or
    // React Flow re-derives its whole edge store on every hover/drag frame.
    const placeOnly = edges.filter((e) => (e.data as any).edgeType === 'place');
    expect(vis(placeOnly, { focused: new Set(), pinned: new Set() }, nodes)).toBe(placeOnly);
  });

  it('leaves hierarchy mode with no edge culling at all', () => {
    // hierarchy mode must keep drawing every edge exactly as before this feature.
    expect(hierarchyMode.edgeVisibility).toBeUndefined();
  });
});
