import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import { tierForZoom } from '../layouts/processColumn';
import type { ZoomTierId } from '../layouts/types';
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

  // ---- UPWARD transitions (zoom-in) — the direction that was broken ---------
  // The old hysteresis guard only modelled resisting a DOWNWARD departure, so
  // with a `current` tier every zoom-in unconditionally returned `current` and
  // the tier never advanced. These cover the reverse direction, which had zero
  // coverage. The single-step assertions below FAIL against the buggy impl
  // (e.g. tierForZoom(0.5, 'glyph') returned 'glyph' instead of 'types').
  it('advances upward as soon as the target tier threshold is reached', () => {
    expect(tierForZoom(0.25, 'glyph')).toBe('ports');
    expect(tierForZoom(0.5, 'glyph')).toBe('types');
    expect(tierForZoom(0.9, 'ports')).toBe('contract');
    expect(tierForZoom(1.6, 'types')).toBe('full');
  });

  it('steps through every tier on a rising zoom sweep from glyph', () => {
    // Thread the returned tier back in as `current`, exactly as onMove does.
    const sweep = [0.1, 0.25, 0.35, 0.5, 0.7, 0.9, 1.2, 1.6, 2.0];
    let cur: ZoomTierId = 'glyph';
    const seen: ZoomTierId[] = [];
    for (const z of sweep) { cur = tierForZoom(z, cur); seen.push(cur); }
    expect(seen).toEqual([
      'glyph', 'ports', 'ports', 'types', 'types',
      'contract', 'contract', 'full', 'full',
    ]);
    // Every one of the five tiers must be reachable on the way up.
    expect(new Set(seen)).toEqual(new Set(['glyph', 'ports', 'types', 'contract', 'full']));
  });

  it('steps down through tiers on a falling zoom sweep from full', () => {
    const sweep = [1.5, 1.0, 0.7, 0.4, 0.2, 0.1];
    let cur: ZoomTierId = 'full';
    const seen: ZoomTierId[] = [];
    for (const z of sweep) { cur = tierForZoom(z, cur); seen.push(cur); }
    // 0.2 holds at 'ports' (== ports.minZoom - hysteresis), then drops at 0.1.
    expect(seen).toEqual(['contract', 'contract', 'types', 'ports', 'ports', 'glyph']);
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
  return render(
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

  it('omits the config container entirely when config is empty', () => {
    // The component renders key names, never a literal "config" label, so the
    // old queryByText(/config/) check passed vacuously. Assert the container
    // element itself is absent (the `.length > 0` guard in ProcessNode).
    const { container } = renderAt('full');
    expect(container.querySelector('.process-node-config')).toBeNull();
  });

  it('renders the config row when config is present', () => {
    const { container } = renderAt('full', { config: { width_um: 1.1 } });
    expect(container.querySelector('.process-node-config')).not.toBeNull();
    expect(screen.getByText('width_um')).toBeTruthy();
  });

  it('pinned-open renders full detail regardless of tier', () => {
    renderAt('glyph', { _pinnedOpen: true });
    expect(screen.getByText(/p_i = max/)).toBeTruthy();
  });

  it('keeps the port handles at every tier so wires still attach', () => {
    // Edges connect to handles by port id (convert.ts writes targetHandle=port);
    // dropping them would break focused-process wiring. Even the glyph tier,
    // which shows no port labels, must keep every input+output handle.
    for (const t of ['glyph', 'ports', 'types', 'contract', 'full']) {
      const { container } = renderAt(t);
      const ids = Array.from(container.querySelectorAll('[data-handleid]'))
        .map((el) => el.getAttribute('data-handleid'))
        .sort();
      // inputs bulk, RNAs + output bulk (one handle per port slot).
      expect(ids).toEqual(['RNAs', 'bulk', 'bulk']);
      cleanup();
    }
  });
});

// Hierarchy mode never stamps a tier; ProcessNode must then render its legacy
// fixed card unchanged, so that mode is entirely unaffected by semantic zoom.
describe('ProcessNode without a tier (hierarchy mode)', () => {
  it('renders the legacy body, not the tiered one', () => {
    const { container } = render(
      <ReactFlowProvider>
        <ProcessNode id="p" type="process" selected={false} zIndex={0}
          isConnectable={false} dragging={false}
          data={{ ...data }} {...({} as any)} />
      </ReactFlowProvider>,
    );
    expect(container.querySelector('.process-label')?.textContent)
      .toBe('ecoli-transcript-initiation');
    expect(container.querySelector('.process-type')?.textContent).toBe('step');
    // The tiered rows must be absent entirely.
    expect(container.querySelector('.process-node-title')).toBeNull();
    expect(container.querySelector('.process-node-meta')).toBeNull();
    // Handles still present for wiring.
    expect(container.querySelectorAll('[data-handleid]').length).toBe(3);
  });
});
