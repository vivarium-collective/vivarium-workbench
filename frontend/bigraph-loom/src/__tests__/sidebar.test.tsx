// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { filterHidden, clampSidebarWidth } from '../panels/filterHidden';
import { Sidebar } from '../panels/Sidebar';
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
