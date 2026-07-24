// @vitest-environment jsdom
//
// The rail is the browsable half of process-column mode: it names the clusters
// the canvas draws as bare bands, and lets a reader search/jump/pin.

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import type { Node } from '@xyflow/react';
import { ProcessRail } from '../panels/ProcessRail';
import type { GroupBand } from '../layouts/types';

afterEach(cleanup);

const bands: GroupBand[] = [
  {
    key: 'unique.RNA', label: 'unique.RNA', yStart: 0, yEnd: 100, keyStoreId: 'unique.RNA',
    nodeIds: ['transcript-initiation', 'rna-degradation'],
  },
  {
    key: 'boundary', label: 'boundary', yStart: 150, yEnd: 200, keyStoreId: 'boundary',
    nodeIds: ['media_update'],
  },
];

/** Mirrors the real layout's trailing bucket: every member is hidden by default. */
const bookkeepingBand: GroupBand = {
  key: '~unclustered', label: 'bookkeeping', yStart: 250, yEnd: 300, keyStoreId: null,
  nodeIds: ['mass_listener', 'allocator_1'],
};

const ALL_IDS = [
  'transcript-initiation', 'rna-degradation', 'media_update',
  'mass_listener', 'allocator_1',
];

const nodes = ALL_IDS.map((id) => ({
  id, type: 'process', position: { x: 0, y: 0 }, data: { label: id, address: 'a' },
})) as unknown as Node[];

function makeFocus(over: Record<string, unknown> = {}) {
  return {
    hovered: null,
    selected: null,
    pinned: new Set<string>(),
    hover: vi.fn(),
    select: vi.fn(),
    togglePin: vi.fn(),
    clear: vi.fn(),
    prunePins: vi.fn(),
    ctx: { focused: new Set<string>(), pinned: new Set<string>() },
    ...over,
  };
}

function setup(over: Partial<React.ComponentProps<typeof ProcessRail>> = {}) {
  const onNavigate = vi.fn();
  const focus = makeFocus();
  const utils = render(
    <ProcessRail
      bands={bands}
      nodes={nodes}
      focus={focus as never}
      granularity={0.5}
      onGranularityChange={vi.fn()}
      onNavigate={onNavigate}
      {...over}
    />,
  );
  return { onNavigate, focus, ...utils };
}

describe('ProcessRail', () => {
  it('renders every cluster label and process', () => {
    setup();
    expect(screen.getByText('unique.RNA')).toBeTruthy();
    expect(screen.getByText('boundary')).toBeTruthy();
    expect(screen.getByText('transcript-initiation')).toBeTruthy();
    expect(screen.getByText('rna-degradation')).toBeTruthy();
    expect(screen.getByText('media_update')).toBeTruthy();
  });

  it('filters processes by the search box', () => {
    setup();
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'degrad' } });
    expect(screen.queryByText('rna-degradation')).toBeTruthy();
    expect(screen.queryByText('transcript-initiation')).toBeNull();
  });

  it('hides a cluster whose members all filter out', () => {
    setup();
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'degrad' } });
    expect(screen.queryByText('boundary')).toBeNull();
  });

  it('navigates and selects when a row is clicked', () => {
    const { onNavigate, focus } = setup();
    fireEvent.click(screen.getByText('media_update'));
    expect(onNavigate).toHaveBeenCalledWith('media_update');
    expect(focus.select).toHaveBeenCalledWith('media_update');
  });

  it('reports granularity slider changes', () => {
    const onGranularityChange = vi.fn();
    // setup() seeds granularity=0.5, so move to a DIFFERENT value — React's
    // controlled-input value tracker suppresses onChange when the value is
    // unchanged, which would make this a false pass.
    setup({ onGranularityChange });
    fireEvent.change(screen.getByLabelText(/granularity/i), { target: { value: '0.7' } });
    expect(onGranularityChange).toHaveBeenCalledWith(0.7);
  });

  it('hovers a row without selecting it', () => {
    const { focus, onNavigate } = setup();
    fireEvent.mouseEnter(screen.getByText('media_update'));
    expect(focus.hover).toHaveBeenCalledWith('media_update');
    expect(focus.select).not.toHaveBeenCalled();
    expect(onNavigate).not.toHaveBeenCalled();
    fireEvent.mouseLeave(screen.getByText('media_update'));
    expect(focus.hover).toHaveBeenLastCalledWith(null);
  });

  it('pins from the row without navigating', () => {
    const { focus, onNavigate } = setup();
    fireEvent.click(screen.getAllByTitle('Pin')[0]);
    expect(focus.togglePin).toHaveBeenCalledWith('transcript-initiation');
    expect(onNavigate).not.toHaveBeenCalled();
  });

  // --- hidden processes (the ~unclustered bucket) --------------------------

  it('collapses an all-hidden cluster by default and says it is hidden', () => {
    setup({
      bands: [...bands, bookkeepingBand],
      hiddenIds: new Set(['mass_listener', 'allocator_1']),
    });
    // The band is still named — the reader can see the bucket exists...
    expect(screen.getByText('bookkeeping')).toBeTruthy();
    expect(screen.getByText(/hidden/i)).toBeTruthy();
    // ...but its members are not listed as if they were on the canvas.
    expect(screen.queryByText('mass_listener')).toBeNull();
  });

  it('expands the collapsed cluster when its header is clicked', () => {
    setup({
      bands: [...bands, bookkeepingBand],
      hiddenIds: new Set(['mass_listener', 'allocator_1']),
    });
    fireEvent.click(screen.getByText('bookkeeping'));
    expect(screen.getByText('mass_listener')).toBeTruthy();
  });

  it('searches inside a collapsed cluster', () => {
    setup({
      bands: [...bands, bookkeepingBand],
      hiddenIds: new Set(['mass_listener', 'allocator_1']),
    });
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'listener' } });
    expect(screen.getByText('mass_listener')).toBeTruthy();
    expect(screen.queryByText('transcript-initiation')).toBeNull();
  });

  it('marks an individual hidden row', () => {
    const { container } = setup({ hiddenIds: new Set(['media_update']) });
    const rows = container.querySelectorAll('.loom-rail-row.is-hidden');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain('media_update');
  });

  it('highlights focused and pinned rows', () => {
    const { container } = setup({
      focus: makeFocus({
        ctx: { focused: new Set(['media_update']), pinned: new Set(['rna-degradation']) },
      }) as never,
    });
    const active = [...container.querySelectorAll('.loom-rail-row.is-active')]
      .map((el) => el.textContent);
    expect(active).toHaveLength(2);
    expect(active.join(' ')).toContain('media_update');
    expect(active.join(' ')).toContain('rna-degradation');
  });

  it('reports when nothing matches', () => {
    setup();
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'zzzz' } });
    expect(screen.getByText(/no matching processes/i)).toBeTruthy();
  });
});
