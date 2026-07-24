import { beforeEach, describe, expect, it } from 'vitest';
import {
  loadViewStore, listViews, saveView, deleteView, setDefault,
  getDefaultName, getDefaultView, getView,
  encodeView, decodeView, normalizeView, type View,
} from '../viewStore';

const CID = 'demo.composite';
const sampleView: View = {
  v: 1,
  positions: { a: { x: 1, y: 2 }, b: { x: 3, y: 4 } },
  collapsed: ['grp.0'],
  hidden: ['a'],
};

// jsdom's localStorage here is partial (no clear()); install a Map-backed one.
function makeStorage(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k) => (m.has(k) ? (m.get(k) as string) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); },
    clear: () => { m.clear(); },
    key: (i) => [...m.keys()][i] ?? null,
    get length() { return m.size; },
  } as Storage;
}

beforeEach(() => {
  Object.defineProperty(window, 'localStorage', { value: makeStorage(), configurable: true });
});

describe('viewStore CRUD', () => {
  it('starts empty', () => {
    expect(listViews(CID)).toEqual([]);
    expect(getDefaultName(CID)).toBeNull();
    expect(getDefaultView(CID)).toBeNull();
  });

  it('saves a view and makes the first one the default', () => {
    saveView(CID, 'overview', sampleView);
    expect(listViews(CID)).toEqual(['overview']);
    expect(getDefaultName(CID)).toBe('overview');           // first save -> default
    expect(getView(CID, 'overview')?.collapsed).toEqual(['grp.0']);
  });

  it('lists views sorted and keeps the first default', () => {
    saveView(CID, 'overview', sampleView);
    saveView(CID, 'wiring', sampleView);
    expect(listViews(CID)).toEqual(['overview', 'wiring']);
    expect(getDefaultName(CID)).toBe('overview');           // unchanged by 2nd save
  });

  it('setDefault switches and clears', () => {
    saveView(CID, 'overview', sampleView);
    saveView(CID, 'wiring', sampleView);
    setDefault(CID, 'wiring');
    expect(getDefaultName(CID)).toBe('wiring');
    setDefault(CID, null);
    expect(getDefaultName(CID)).toBeNull();
  });

  it('deleting the default reassigns to the first remaining', () => {
    saveView(CID, 'overview', sampleView);
    saveView(CID, 'wiring', sampleView);
    setDefault(CID, 'wiring');
    deleteView(CID, 'wiring');
    expect(listViews(CID)).toEqual(['overview']);
    expect(getDefaultName(CID)).toBe('overview');           // reassigned, not dangling
  });

  it('is isolated per composite id', () => {
    saveView(CID, 'overview', sampleView);
    expect(listViews('other.composite')).toEqual([]);
  });

  it('survives corrupt storage', () => {
    window.localStorage.setItem('bigraph-loom:views:' + CID, '{not json');
    expect(loadViewStore(CID)).toEqual({ default: null, views: {} });
  });
});

describe('view encode/decode', () => {
  it('round-trips a view through the URL encoding', () => {
    const enc = encodeView(sampleView);
    expect(typeof enc).toBe('string');
    expect(decodeView(enc)).toEqual(normalizeView(sampleView));
  });

  it('decode returns null on garbage', () => {
    expect(decodeView('!!!not-lz!!!')).toBeNull();
    expect(decodeView('')).toBeNull();
    expect(decodeView(null)).toBeNull();
  });

  it('normalizeView fills gaps and drops junk', () => {
    expect(normalizeView({})).toEqual({
      v: 1, positions: {}, collapsed: [], hidden: [], mode: 'hierarchy', pins: [],
    });
    expect(normalizeView({ collapsed: ['x'], extra: 'nope' })).toEqual({
      v: 1, positions: {}, collapsed: ['x'], hidden: [], mode: 'hierarchy', pins: [],
    });
  });

  it('normalizes a legacy view with no mode to hierarchy', () => {
    const v = normalizeView({ positions: {}, collapsed: [], hidden: [] } as any);
    expect(v.mode).toBe('hierarchy');
    expect(v.pins).toEqual([]);
  });

  it('preserves an explicit mode and pins', () => {
    const v = normalizeView({
      positions: {}, collapsed: [], hidden: [],
      mode: 'process-column', pins: ['p1'],
    } as any);
    expect(v.mode).toBe('process-column');
    expect(v.pins).toEqual(['p1']);
  });
});
