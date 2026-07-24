import { describe, it, expect } from 'vitest';
import { LAYOUT_MODES, getMode, DEFAULT_MODE_ID } from '../layouts/registry';

describe('layout registry', () => {
  it('exposes hierarchy as the default mode', () => {
    expect(DEFAULT_MODE_ID).toBe('hierarchy');
    expect(getMode(DEFAULT_MODE_ID).id).toBe('hierarchy');
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
