import { describe, it, expect } from 'vitest';
import { deriveContract, contractCompleteness } from '../contract';
import type { ProcessNodeData } from '../types';

const DOC = `TranscriptInitiation — distributes activated RNAPs across TUs by weighted multinomial sampling.

    n_to_activate = round(f_active · n_total_RNAP) - n_active
    p_i = max(0, basal_prob_i + ∑_j delta_prob[i,j] · bound_TF_j)
    initiations ~ Multinomial(n_to_activate, p_i / ∑_i p_i)
  f_active: media-dependent active RNAP fraction.`;

function data(over: Partial<ProcessNodeData> = {}): ProcessNodeData {
  return {
    label: 'ecoli-transcript-initiation', nodeType: 'process', processType: 'step',
    address: 'local:X', config: {}, path: ['a'], inputPorts: ['bulk', 'RNAs'],
    outputPorts: ['bulk'], ...over,
  } as ProcessNodeData;
}

describe('deriveContract', () => {
  it('takes the first line as the summary', () => {
    const c = deriveContract(data({ description: DOC }))!;
    expect(c.summary).toMatch(/distributes activated RNAPs/);
    expect(c.summary).not.toContain('\n');
  });

  it('extracts equation lines as math', () => {
    const c = deriveContract(data({ description: DOC }))!;
    expect(c.math).toHaveLength(3);
    expect(c.math[0]).toContain('n_to_activate =');
    expect(c.math[2]).toContain('Multinomial');
  });

  it('keeps remaining prose as the description', () => {
    const c = deriveContract(data({ description: DOC }))!;
    expect(c.description).toContain('media-dependent active RNAP fraction');
    expect(c.description).not.toContain('n_to_activate =');
  });

  it('prefers a declared contract over the docstring', () => {
    const declared = { summary: 'declared', math: ['x = 1'], inputs: { bulk: 'reads counts' } };
    const c = deriveContract(data({ description: DOC, contract: declared } as any))!;
    expect(c.summary).toBe('declared');
    expect(c.inputs.bulk).toBe('reads counts');
  });

  it('returns null when there is nothing to derive from', () => {
    expect(deriveContract(data({ description: undefined }))).toBeNull();
  });

  it('yields a summary-only contract for a doc with no math', () => {
    const c = deriveContract(data({ description: 'Just a plain description.' }))!;
    expect(c.summary).toBe('Just a plain description.');
    expect(c.math).toEqual([]);
  });
});

describe('contractCompleteness', () => {
  it('counts documented ports against the real port list', () => {
    const c = { summary: 's', description: '', math: [], symbols: {},
      inputs: { bulk: 'reads' }, outputs: {}, config: {}, assumptions: [], references: [] };
    const r = contractCompleteness(c, data());
    expect(r.documented).toBe(1);
    expect(r.total).toBe(3);   // bulk + RNAs in, bulk out
  });

  it('flags a contract entry naming a port that does not exist', () => {
    const c = { summary: 's', description: '', math: [], symbols: {},
      inputs: { ghost: 'gone' }, outputs: {}, config: {}, assumptions: [], references: [] };
    expect(contractCompleteness(c, data()).unknownPorts).toEqual(['ghost']);
  });

  it('reports zero documented for a null contract', () => {
    expect(contractCompleteness(null, data()).documented).toBe(0);
  });
});
