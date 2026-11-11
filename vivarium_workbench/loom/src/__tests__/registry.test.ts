import { describe, it, expect } from 'vitest';
import { LAYOUT_MODES, getMode, DEFAULT_MODE_ID } from '../layouts/registry';

describe('layout registry', () => {
  it('registers the expected layout modes with flow-down as default', () => {
    expect(DEFAULT_MODE_ID).toBe('flow-down');
    expect(getMode(DEFAULT_MODE_ID).id).toBe('flow-down');
    expect(LAYOUT_MODES.map((m) => m.id)).toEqual([
      'hierarchy', 'flow-down', 'tree-grid', 'flow-right',
    ]);
  });

  it('every registered mode satisfies the interface', () => {
    expect(LAYOUT_MODES.length).toBeGreaterThan(0);
    for (const m of LAYOUT_MODES) {
      expect(typeof m.id).toBe('string');
      expect(m.id.length).toBeGreaterThan(0);
      expect(typeof m.label).toBe('string');
      expect(typeof m.run).toBe('function');
    }
  });

  it('mode ids are unique', () => {
    const ids = LAYOUT_MODES.map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('falls back to the default mode for an unknown id', () => {
    expect(getMode('does-not-exist').id).toBe(DEFAULT_MODE_ID);
  });
});
