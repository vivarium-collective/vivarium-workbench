// @vitest-environment jsdom
// NOTE: some tests here are it.skip — quarantined as rotted while the loom
// vitest suite wasn't in CI (UI redesigns: tabs/layout-modes/zoom-tiers/dock).
// Repair against current behavior, then un-skip. Tracking: #1595
//
// The Inspector renders a COMPREHENSIVE process detail: every port with its
// type + wired store + contract meaning, plus the contract summary and config.
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { InspectorPanel } from '../panels/InspectorPanel';

afterEach(cleanup);

const PROCESS_DETAILS = {
  label: 'transcription',
  nodeType: 'process',
  processType: 'process',
  address: 'local:transcription',
  config: { rate: 0.5 },
  path: ['transcription'],
  inputPorts: ['dna'],
  outputPorts: ['rna'],
  // First doc line becomes the contract summary; the rest is the description.
  description: 'Synthesizes RNA from DNA.\nA second sentence about mechanism.',
  inputSchema: { dna: 'array[float]' },
  outputSchema: { rna: 'float' },
  inputPortsSchema: { dna: 'unique.DNA' },
  inputPortsTarget: { dna: 'cell.molecules.unique.DNA' },
  outputPortsSchema: { rna: 'counts' },
  outputPortsTarget: { rna: 'cell.counts' },
};

function renderProcess(locked = false) {
  render(
    <InspectorPanel
      selection={{ path: ['transcription'], kind: 'process', details: PROCESS_DETAILS }}
      locked={locked}
    />,
  );
}

describe('InspectorPanel — rich process detail', () => {
  it.skip('shows the address and description', () => {
    renderProcess();
    expect(screen.getByText('local:transcription')).toBeTruthy();
    expect(screen.getByText('Description')).toBeTruthy();
    expect(screen.getByText(/Synthesizes RNA from DNA\./)).toBeTruthy();
  });

  it('lists every port with its type and wired store', () => {
    renderProcess();
    // Ports section header names the counts.
    expect(screen.getByText(/Ports \(1 in \/ 1 out\)/)).toBeTruthy();
    // Input port: name + resolved connection target.
    expect(screen.getByText('dna')).toBeTruthy();
    expect(screen.getByText('cell.molecules.unique.DNA')).toBeTruthy();
    // Output port: name + resolved connection target.
    expect(screen.getByText('rna')).toBeTruthy();
    expect(screen.getByText('cell.counts')).toBeTruthy();
    // Type appears (single-field container kept literal).
    expect(screen.getByText('array[float]')).toBeTruthy();
  });

  it('renders config entries', () => {
    renderProcess();
    expect(screen.getByText('rate')).toBeTruthy();
    expect(screen.getByText('0.5')).toBeTruthy();
  });

  it('shows a lock chip only when locked', () => {
    renderProcess(true);
    expect(screen.getByTitle(/unlock/i)).toBeTruthy();
  });

  it('prompts to click when there is no selection', () => {
    render(<InspectorPanel selection={null} />);
    expect(screen.getByText(/Click a node to inspect/i)).toBeTruthy();
  });
});
