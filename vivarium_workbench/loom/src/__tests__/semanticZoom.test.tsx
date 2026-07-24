import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import { tierForZoom } from '../layouts/processColumn';
import ProcessNode from '../nodes/ProcessNode';

// No `globals: true` in vitest config, so testing-library's auto-cleanup is not
// registered — unmount between cases explicitly, as the other render tests do.
afterEach(cleanup);

describe('tierForZoom', () => {
  it('maps zoom onto the five tiers', () => {
    expect(tierForZoom(0.1)).toBe('glyph');
    expect(tierForZoom(0.3)).toBe('ports');
    expect(tierForZoom(0.7)).toBe('types');
    expect(tierForZoom(1.2)).toBe('contract');
    expect(tierForZoom(2.0)).toBe('full');
  });

  it('holds the current tier inside the hysteresis margin', () => {
    expect(tierForZoom(0.88, 'contract')).toBe('contract');
    expect(tierForZoom(0.80, 'contract')).toBe('types');
  });

  it('is stable when no current tier is supplied', () => {
    expect(tierForZoom(0.88)).toBe('types');
  });
});

const DOC = `Distributes activated RNAPs across TUs.

    p_i = max(0, basal_i + ∑_j dp[i,j] · TF_j)`;

const data = {
  label: 'ecoli-transcript-initiation', nodeType: 'process', processType: 'step',
  address: 'local:v2ecoli.processes.transcript_initiation.TranscriptInitiation',
  config: {}, interval: 2, path: ['agents', '0', 'ecoli-transcript-initiation'],
  inputPorts: ['bulk', 'RNAs'], outputPorts: ['bulk'],
  inputPortsSchema: { bulk: 'bulk', RNAs: 'unique.RNA' },
  outputPortsSchema: { bulk: 'bulk' },
  inputSchema: { bulk: 'bulk_array', RNAs: 'unique_array[a:integer|b:float|c:boolean]' },
  outputSchema: { bulk: 'bulk_array' },
  description: DOC,
} as any;

function renderAt(tier: string, over: Record<string, unknown> = {}) {
  render(
    <ReactFlowProvider>
      <ProcessNode id="p" type="process" selected={false} zIndex={0}
        isConnectable={false} dragging={false}
        data={{ ...data, ...over, _tier: tier }} {...({} as any)} />
    </ReactFlowProvider>,
  );
}

describe('ProcessNode tiers', () => {
  it('glyph shows only the name', () => {
    renderAt('glyph');
    expect(screen.getByText('ecoli-transcript-initiation')).toBeTruthy();
    expect(screen.queryByText(/2 in \/ 1 out/)).toBeNull();
  });

  it('ports adds port counts and port names', () => {
    renderAt('ports');
    expect(screen.getByText(/2 in \/ 1 out/)).toBeTruthy();
    expect(screen.getByText('RNAs')).toBeTruthy();
    expect(screen.queryByText(/3 fields/)).toBeNull();
  });

  it('types adds abbreviated port types and the address', () => {
    renderAt('types');
    expect(screen.getByText('unique_array[3 fields]')).toBeTruthy();
    expect(screen.getByText(/TranscriptInitiation/)).toBeTruthy();
    expect(screen.queryByText(/p_i = max/)).toBeNull();
  });

  it('contract adds the math lines', () => {
    renderAt('contract');
    expect(screen.getByText(/p_i = max/)).toBeTruthy();
  });

  it('full adds the completeness indicator', () => {
    renderAt('full');
    expect(screen.getByText(/0\/3 ports documented/)).toBeTruthy();
  });

  it('omits the config row entirely when config is empty', () => {
    renderAt('full');
    expect(screen.queryByText(/^config$/i)).toBeNull();
  });

  it('renders the config row when config is present', () => {
    renderAt('full', { config: { width_um: 1.1 } });
    expect(screen.getByText('width_um')).toBeTruthy();
  });

  it('pinned-open renders full detail regardless of tier', () => {
    renderAt('glyph', { _pinnedOpen: true });
    expect(screen.getByText(/p_i = max/)).toBeTruthy();
  });
});
