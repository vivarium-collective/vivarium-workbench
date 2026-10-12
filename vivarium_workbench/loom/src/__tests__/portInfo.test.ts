import { describe, it, expect } from 'vitest';
import { portInfo } from '../portInfo';

describe('portInfo', () => {
  it('reports reads for inputs, writes for outputs', () => {
    expect(portInfo('a', false, {}).direction).toBe('reads');
    expect(portInfo('b', true, {}).direction).toBe('writes');
  });

  it('abbreviates a structured type but keeps the full form', () => {
    const info = portInfo('a', false, {
      typeSchema: { a: 'tree[float|integer|string]' },
    });
    expect(info.type).toBe('tree[3 fields]');
    expect(info.fullType).toBe('tree[float|integer|string]');
  });

  it('keeps a single-field container type literal', () => {
    const info = portInfo('a', false, { typeSchema: { a: 'map[float]' } });
    expect(info.type).toBe('map[float]');
    expect(info.fullType).toBe('map[float]');
  });

  it('prefers the resolved absolute target over the raw joined one', () => {
    const info = portInfo('a', false, {
      portsSchema: { a: 'unique.RNA' },
      portsTarget: { a: 'cell.molecules.unique.RNA' },
    });
    expect(info.connectsTo).toBe('cell.molecules.unique.RNA');
    expect(info.rawTarget).toBe('unique.RNA');
  });

  it('falls back to the raw target when no resolved path is present', () => {
    const info = portInfo('a', false, { portsSchema: { a: 'unique.RNA' } });
    expect(info.connectsTo).toBe('unique.RNA');
  });

  it('renders the composite root (resolved "") as <root>', () => {
    const info = portInfo('a', true, {
      portsSchema: { a: '' },
      portsTarget: { a: '' },
    });
    expect(info.connectsTo).toBe('<root>');
  });

  it('is empty-safe for an unwired, untyped port', () => {
    const info = portInfo('a', false, {});
    expect(info.type).toBe('');
    expect(info.fullType).toBe('');
    expect(info.connectsTo).toBe('');
    expect(info.rawTarget).toBe('');
  });

  it('labels a subtree-mapping port (no leaf _type) as tree[fields]', () => {
    const info = portInfo('a', false, {
      typeSchema: { a: { active_ribosome: 'rna', active_RNAP: 'rna', active_replisome: 'rna' } },
    });
    expect(info.type).toBe('tree[3 fields]');
    expect(info.fullType).toBe('tree[active_ribosome|active_RNAP|active_replisome]');
  });

  it('keeps a single-field subtree literal', () => {
    const info = portInfo('a', false, { typeSchema: { a: { media_id: 'string' } } });
    expect(info.type).toBe('tree[media_id]');
    expect(info.fullType).toBe('tree[media_id]');
  });

  it('reads a leaf _type schema dict', () => {
    const info = portInfo('a', false, { typeSchema: { a: { _type: 'rna', _default: [] } } });
    expect(info.type).toBe('rna');
    expect(info.fullType).toBe('rna');
  });

  it('is empty for a dict with only reserved keys', () => {
    const info = portInfo('a', false, { typeSchema: { a: { _default: 0 } as unknown } });
    expect(info.type).toBe('');
    expect(info.fullType).toBe('');
  });
});

describe('portInfo units', () => {
  it('surfaces _units from a schema dict', () => {
    const info = portInfo('temperature', false, { typeSchema: { temperature: { _type: 'float', _units: 'kelvin' } } });
    expect(info.type).toBe('float');
    expect(info.units).toBe('kelvin');
  });
  it('is empty when the port declares no units', () => {
    expect(portInfo('x', false, { typeSchema: { x: 'float' } }).units).toBe('');
  });
});
