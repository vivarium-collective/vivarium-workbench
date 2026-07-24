import { describe, it, expect, afterEach } from 'vitest';
import type { Edge } from '@xyflow/react';
import { readersAndWriters } from '../storeFacts';

const edges = [
  { id: 'e1', source: 'unique.RNA', target: 'transcript-init', data: { edgeType: 'input' } },
  { id: 'e2', source: 'unique.RNA', target: 'rna-degradation', data: { edgeType: 'input' } },
  { id: 'e3', source: 'transcript-init', target: 'unique.RNA', data: { edgeType: 'output' } },
  { id: 'e4', source: 'other', target: 'bulk', data: { edgeType: 'output' } },
  { id: 'e5', source: 'unique', target: 'unique.RNA', data: { edgeType: 'place' } },
] as unknown as Edge[];

describe('readersAndWriters', () => {
  it('lists processes that read the store', () => {
    expect(readersAndWriters('unique.RNA', edges).readers.sort())
      .toEqual(['rna-degradation', 'transcript-init']);
  });

  it('lists processes that write the store', () => {
    expect(readersAndWriters('unique.RNA', edges).writers).toEqual(['transcript-init']);
  });

  it('ignores structural place edges', () => {
    const r = readersAndWriters('unique.RNA', edges);
    expect(r.readers).not.toContain('unique');
    expect(r.writers).not.toContain('unique');
  });

  it('deduplicates a process wired through several ports', () => {
    const many = [
      { id: 'a', source: 'S', target: 'p', data: { edgeType: 'input' } },
      { id: 'b', source: 'S', target: 'p', data: { edgeType: 'input' } },
    ] as unknown as Edge[];
    expect(readersAndWriters('S', many).readers).toEqual(['p']);
  });

  it('returns empty lists for an unwired store', () => {
    expect(readersAndWriters('nope', edges)).toEqual({ readers: [], writers: [] });
  });
});

import { render, screen, cleanup } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import StoreNode from '../nodes/StoreNode';

// No `globals: true` in vitest config — unmount between cases explicitly.
afterEach(cleanup);

const storeData = {
  label: 'RNA', nodeType: 'store', path: ['agents', '0', 'unique', 'RNA'],
  value: 'Array(8)', valueType: 'unique_array[a:integer|b:float|c:boolean]',
  isGroup: false,
} as any;

function renderStore(tier: string, over: Record<string, unknown> = {}) {
  render(
    <ReactFlowProvider>
      <StoreNode id="unique.RNA" type="store" selected={false} zIndex={0}
        isConnectable={false} dragging={false}
        data={{ ...storeData, ...over, _tier: tier }} {...({} as any)} />
    </ReactFlowProvider>,
  );
}

describe('StoreNode tiers', () => {
  it('glyph shows only the name', () => {
    renderStore('glyph');
    expect(screen.getByText('RNA')).toBeTruthy();
    expect(screen.queryByText('Array(8)')).toBeNull();
  });

  it('ports adds the value summary', () => {
    renderStore('ports');
    expect(screen.getByText('Array(8)')).toBeTruthy();
    expect(screen.queryByText(/3 fields/)).toBeNull();
  });

  it('types adds the abbreviated declared type', () => {
    renderStore('types');
    expect(screen.getByText('unique_array[3 fields]')).toBeTruthy();
  });

  it('contract adds the reader/writer summary', () => {
    renderStore('contract', { _readers: ['a', 'b'], _writers: ['a'] });
    expect(screen.getByText(/2 read/)).toBeTruthy();
    expect(screen.getByText(/1 write/)).toBeTruthy();
  });

  it('omits the reader/writer row when the store is unwired', () => {
    renderStore('contract', { _readers: [], _writers: [] });
    expect(screen.queryByText(/read/)).toBeNull();
  });
});

// Hierarchy mode never stamps a tier; StoreNode must then render its legacy
// circle unchanged, so that mode is entirely unaffected by semantic zoom.
describe('StoreNode without a tier (hierarchy mode)', () => {
  it('renders the legacy circle, not the tiered one', () => {
    const { container } = render(
      <ReactFlowProvider>
        <StoreNode id="unique.RNA" type="store" selected={false} zIndex={0}
          isConnectable={false} dragging={false}
          data={{ ...storeData }} {...({} as any)} />
      </ReactFlowProvider>,
    );
    // Legacy markup: store-label + store-value (truncated with a title).
    expect(container.querySelector('.store-label')?.textContent).toBe('RNA');
    expect(container.querySelector('.store-value')).not.toBeNull();
    // The tiered rows must be absent entirely.
    expect(container.querySelector('.store-node-value')).toBeNull();
    expect(container.querySelector('.store-node-type')).toBeNull();
    // Handles still present for wiring (top-place, left-out, right-in, bottom-place).
    expect(container.querySelectorAll('[data-handleid]').length).toBe(4);
  });
});
