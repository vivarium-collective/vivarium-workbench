import { describe, it, expect } from 'vitest';
import { abbreviateType } from '../contract';

describe('abbreviateType', () => {
  it('collapses a long structured type to a field count', () => {
    const t = 'unique_array[TU_index:integer|transcript_length:integer|is_mRNA:boolean]';
    expect(abbreviateType(t)).toBe('unique_array[3 fields]');
  });

  it('leaves short scalar types alone', () => {
    expect(abbreviateType('string')).toBe('string');
    expect(abbreviateType('bulk_array')).toBe('bulk_array');
    expect(abbreviateType('quantity[g/L]')).toBe('quantity[g/L]');
  });

  it('keeps a single-field structured type readable rather than counting it', () => {
    expect(abbreviateType('map[float]')).toBe('map[float]');
  });

  it('handles the real 17-field transcript type', () => {
    const fields = Array.from({ length: 17 }, (_, i) => `f${i}:integer`).join('|');
    expect(abbreviateType(`unique_array[${fields}]`)).toBe('unique_array[17 fields]');
  });

  it('is a no-op on empty or non-string input', () => {
    expect(abbreviateType('')).toBe('');
    expect(abbreviateType(undefined as unknown as string)).toBe('');
  });
});
