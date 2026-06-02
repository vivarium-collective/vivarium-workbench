// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { filterHidden, clampSidebarWidth, isHiddenByAncestor } from '../panels/filterHidden';
import { Sidebar, buildNodeTree } from '../panels/Sidebar';
import type { ExploreInspectMsg } from '../api';

describe('filterHidden', () => {
  const nodes = [
    { id: 'a' }, { id: 'b' }, { id: 'c' },
  ];
  const edges = [
    { source: 'a', target: 'b' },
    { source: 'b', target: 'c' },
    { source: 'a', target: 'c' },
  ];

  it('drops hidden nodes and edges touching them', () => {
    const out = filterHidden(nodes, edges, new Set(['b']));
    expect(out.nodes.map((n) => n.id)).toEqual(['a', 'c']);
    // edges a-b and b-c are dropped; a-c survives
    expect(out.edges).toEqual([{ source: 'a', target: 'c' }]);
  });

  it('returns everything when nothing is hidden', () => {
    const out = filterHidden(nodes, edges, new Set());
    expect(out.nodes).toHaveLength(3);
    expect(out.edges).toHaveLength(3);
  });

  it('does not mutate its inputs', () => {
    filterHidden(nodes, edges, new Set(['a']));
    expect(nodes).toHaveLength(3);
    expect(edges).toHaveLength(3);
  });
});

describe('isHiddenByAncestor', () => {
  it('hides a node when its own id is in the set', () => {
    expect(isHiddenByAncestor(['bulk'], new Set(['bulk']))).toBe(true);
  });

  it('hides a node when a strict ancestor id is in the set (cascade)', () => {
    // hiding `bulk` cascade-hides `bulk.ATP`
    expect(isHiddenByAncestor(['bulk', 'ATP'], new Set(['bulk']))).toBe(true);
    expect(isHiddenByAncestor(['a', 'b', 'c'], new Set(['a']))).toBe(true);
    expect(isHiddenByAncestor(['a', 'b', 'c'], new Set(['a.b']))).toBe(true);
  });

  it('does not hide a node when neither it nor an ancestor is hidden', () => {
    expect(isHiddenByAncestor(['bulk', 'ATP'], new Set(['listeners']))).toBe(false);
    expect(isHiddenByAncestor(['a', 'b'], new Set(['a.b.c']))).toBe(false);
    expect(isHiddenByAncestor(['a'], new Set())).toBe(false);
  });

  it('treats the empty path as the <root> node id', () => {
    expect(isHiddenByAncestor([], new Set(['<root>']))).toBe(true);
    expect(isHiddenByAncestor([], new Set(['bulk']))).toBe(false);
  });
});

describe('buildNodeTree', () => {
  const store = (path: string[], extra: Record<string, unknown> = {}) => ({
    id: path.length ? path.join('.') : '<root>',
    type: 'store',
    data: { label: path[path.length - 1] ?? '<root>', path, ...extra },
  });

  it('nests store nodes by their path', () => {
    const nodes = [
      store(['bulk']),
      store(['bulk', 'ATP']),
      store(['bulk', 'GTP']),
      store(['listeners']),
    ];
    const root = buildNodeTree(nodes);
    expect(root.children.map((c) => c.id).sort()).toEqual(['bulk', 'listeners']);
    const bulk = root.children.find((c) => c.id === 'bulk')!;
    expect(bulk.children.map((c) => c.id).sort()).toEqual(['bulk.ATP', 'bulk.GTP']);
  });

  it('synthesizes intermediate group nodes for paths with no explicit node', () => {
    // Only the leaf is present; `a` and `a.b` are implied and must be created.
    const nodes = [store(['a', 'b', 'c'])];
    const root = buildNodeTree(nodes);
    expect(root.children.map((c) => c.id)).toEqual(['a']);
    const a = root.children[0];
    expect(a.children.map((c) => c.id)).toEqual(['a.b']);
    expect(a.children[0].children.map((c) => c.id)).toEqual(['a.b.c']);
  });

  it('excludes process nodes from the tree', () => {
    const nodes = [
      store(['stores']),
      { id: 'proc', type: 'process', data: { label: 'proc', path: ['proc'] } },
    ];
    const root = buildNodeTree(nodes);
    expect(root.children.map((c) => c.id)).toEqual(['stores']);
  });
});

describe('clampSidebarWidth', () => {
  it('clamps to [200, 760]', () => {
    expect(clampSidebarWidth(50)).toBe(200);
    expect(clampSidebarWidth(400)).toBe(400);
    expect(clampSidebarWidth(9999)).toBe(760);
  });
});

describe('Sidebar Description block', () => {
  afterEach(() => cleanup());

  // With localStorage unavailable/empty in the test env, the Sidebar falls back
  // to its defaults: the 'inspector' tab and an expanded (non-collapsed) panel,
  // which is exactly what these assertions need.
  const baseProps = {
    nodes: [],
    hidden: new Set<string>(),
    onToggleHidden: () => {},
    onShowAll: () => {},
    emitSet: new Set<string>(),
    onEmitToggle: () => {},
  };

  function selection(details: Record<string, unknown>): Omit<ExploreInspectMsg, 'type'> {
    return { path: ['proc'], kind: 'process', details };
  }

  it('renders a Description block when description is present', () => {
    const { container, getByText } = render(
      <Sidebar {...baseProps} selection={selection({ description: 'Hydrolyzes ATP.' })} />,
    );
    expect(getByText('Description')).toBeTruthy();
    expect(container.textContent).toContain('Hydrolyzes ATP.');
  });

  it('omits the Description block when description is absent', () => {
    const { queryByText } = render(
      <Sidebar {...baseProps} selection={selection({ label: 'proc' })} />,
    );
    expect(queryByText('Description')).toBeNull();
  });

  it('omits the Description block when description is blank', () => {
    const { queryByText } = render(
      <Sidebar {...baseProps} selection={selection({ description: '   ' })} />,
    );
    expect(queryByText('Description')).toBeNull();
  });
});
