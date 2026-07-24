import { describe, it, expect } from 'vitest';
import { edgeLabelFor } from '../edges/FloatingStoreEdge';

const base = { port: 'RNAs', portType: 'unique_array[a:integer|b:float]',
  semantic: 'appends newly initiated transcripts' };

describe('edgeLabelFor', () => {
  it('renders nothing at glyph', () => {
    expect(edgeLabelFor('glyph', base)).toBe('');
  });

  it('renders the port name at ports', () => {
    expect(edgeLabelFor('ports', base)).toBe('RNAs');
  });

  it('adds the abbreviated type at types', () => {
    expect(edgeLabelFor('types', base)).toBe('RNAs: unique_array[2 fields]');
  });

  it('adds the contract semantic at contract', () => {
    expect(edgeLabelFor('contract', base))
      .toBe('RNAs: unique_array[2 fields] — appends newly initiated transcripts');
  });

  it('degrades when the contract has no semantic for the port', () => {
    expect(edgeLabelFor('contract', { ...base, semantic: undefined }))
      .toBe('RNAs: unique_array[2 fields]');
  });

  it('degrades when no type is known', () => {
    expect(edgeLabelFor('types', { port: 'x' })).toBe('x');
  });
});
