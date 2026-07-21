import { describe, it, expect } from 'vitest';

import { applyLayout, applyCompactLayout } from '../layout';

describe('applyLayout (async ELK)', () => {
  it('runs without throwing on a single node', async () => {
    const nodes = [{ id: 'a', type: 'store', data: {} as any, position: { x: 0, y: 0 } }];
    const out = await applyLayout(nodes, []);
    expect(out.length).toBe(1);
    expect(out[0].position).toBeDefined();
  });

  it('separates two unconnected nodes (assigns distinct positions)', async () => {
    const nodes = [
      { id: 'a', type: 'store', data: {} as any, position: { x: 0, y: 0 } },
      { id: 'b', type: 'store', data: {} as any, position: { x: 0, y: 0 } },
    ];
    const out = await applyLayout(nodes, []);
    expect(out.length).toBe(2);
    // After layered layout the two nodes should not be on top of each other.
    const samePosition = out[0].position.x === out[1].position.x
                      && out[0].position.y === out[1].position.y;
    expect(samePosition).toBe(false);
  });

  it('places connected nodes top-to-bottom along the place-edge flow', async () => {
    // direction: DOWN with a place edge → target ends up BELOW source.
    // Use store nodes with a place edge so the layout treats it as ranking input.
    const nodes = [
      { id: 'outer', type: 'store', data: { path: ['outer'] } as any, position: { x: 0, y: 0 } },
      { id: 'inner', type: 'store', data: { path: ['outer', 'inner'] } as any, position: { x: 0, y: 0 } },
    ];
    const edges = [{ id: 'p', source: 'outer', target: 'inner', data: { edgeType: 'place' } as any }];
    const out = await applyLayout(nodes as any, edges as any);
    const outer = out.find((n) => n.id === 'outer')!;
    const inner = out.find((n) => n.id === 'inner')!;
    // Inner store should be below the outer (direction DOWN).
    expect(inner.position.y).toBeGreaterThan(outer.position.y);
  });

  it('returns [] for empty input without invoking ELK', async () => {
    const out = await applyLayout([], []);
    expect(out).toEqual([]);
  });
});

describe('applyCompactLayout (sync grid fallback)', () => {
  it('positions N nodes in a roughly-square grid', () => {
    const nodes = Array.from({ length: 4 }, (_, i) => ({
      id: `n${i}`, type: 'store', data: {} as any, position: { x: 0, y: 0 },
    }));
    const out = applyCompactLayout(nodes as any);
    expect(out.length).toBe(4);
    // 4 nodes → 2 columns, 2 rows. Top-left should be (0,0).
    expect(out[0].position).toEqual({ x: 0, y: 0 });
  });
});
