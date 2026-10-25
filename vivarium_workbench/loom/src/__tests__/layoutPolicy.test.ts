// Adaptive layout policy — shape → {mode, collapseRedundant}. Pure unit tests
// over synthetic graph shapes, plus the threshold boundaries.
import { describe, it, expect } from 'vitest';
import { chooseInitialView, maxSiblingFan, POLICY } from '../layoutPolicy';

// Minimal composite-state builders (the policy reads via overviewGraph →
// stateToReactFlow, so we build real state trees, not React-Flow nodes).

/** A flat set of scalar stores under the root. */
function flatStores(n: number): Record<string, number> {
  const s: Record<string, number> = {};
  for (let i = 0; i < n; i++) s[`s_${i}`] = 0;
  return s;
}

/** One parent store holding `fan` scalar children. */
function oneParentFan(fan: number): Record<string, unknown> {
  const kids: Record<string, number> = {};
  for (let i = 0; i < fan; i++) kids[`child_${i}`] = 0;
  return { parent: kids };
}

describe('chooseInitialView', () => {
  it('tiny no-repeat graph → Tree, collapse off (today’s behavior)', () => {
    const v = chooseInitialView({ a: 0, b: 0, c: 0 });
    expect(v).toEqual({ modeId: 'flow-down', collapseRedundant: false });
  });

  it('many nodes (> BIG_N) → Grid + collapse', () => {
    const v = chooseInitialView(flatStores(POLICY.BIG_N + 5));
    expect(v).toEqual({ modeId: 'tree-grid', collapseRedundant: true });
  });

  it('wide sibling fan (> FAN) under one parent → Grid + collapse', () => {
    // A fan just over FAN but well under BIG_N total nodes: the fan alone trips it.
    const v = chooseInitialView(oneParentFan(POLICY.FAN + 2));
    expect(v.modeId).toBe('tree-grid');
    expect(v.collapseRedundant).toBe(true);
  });

  it('a small fan (<= FAN) and few nodes stays Tree', () => {
    const v = chooseInitialView(oneParentFan(POLICY.FAN - 2));
    expect(v).toEqual({ modeId: 'flow-down', collapseRedundant: false });
  });

  it('boundary: strict > on node count (root-level stores have no counted fan)', () => {
    // Root-level stores carry no place-edge PARENT (root is not a node), so they
    // contribute no sibling fan — the count rule alone governs here.
    // Exactly BIG_N → not big → Tree; one more → Grid.
    expect(chooseInitialView(flatStores(POLICY.BIG_N)).modeId).toBe('flow-down');
    expect(chooseInitialView(flatStores(POLICY.BIG_N + 1)).modeId).toBe('tree-grid');
  });

  it('never throws on junk input — falls back to Tree', () => {
    for (const junk of [null, undefined, 42, 'x', [], { _type: 'process' }]) {
      expect(() => chooseInitialView(junk)).not.toThrow();
    }
    expect(chooseInitialView(null)).toEqual({ modeId: 'flow-down', collapseRedundant: false });
  });
});

describe('maxSiblingFan', () => {
  it('counts the largest place-edge child group', () => {
    const edges = [
      { source: 'p', target: 'a', data: { edgeType: 'place' } },
      { source: 'p', target: 'b', data: { edgeType: 'place' } },
      { source: 'p', target: 'c', data: { edgeType: 'place' } },
      { source: 'q', target: 'd', data: { edgeType: 'place' } },
      // a non-place (wire) edge must not count
      { source: 'p', target: 'z', data: { edgeType: 'wire' } },
    ] as never;
    expect(maxSiblingFan(edges)).toBe(3);
  });

  it('is 0 with no place edges', () => {
    expect(maxSiblingFan([{ source: 'p', target: 'a', data: { edgeType: 'wire' } }] as never)).toBe(0);
  });
});
