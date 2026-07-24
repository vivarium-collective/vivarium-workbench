// @vitest-environment jsdom
import { describe, it, expect, beforeAll, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import App from '../App';
import { LAYOUT_MODES, DEFAULT_MODE_ID } from '../layouts/registry';
import { encodeView } from '../viewStore';

// React Flow relies on ResizeObserver, which jsdom doesn't provide.
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

function postCompositeLoad(metadata: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', {
      data: { type: 'composite:load', state: {}, metadata },
    }));
  });
}

describe('App static mode initial tab', () => {
  afterEach(() => {
    cleanup();
    // Restore URL to plain root after each test so subsequent tests start clean.
    window.history.pushState({}, '', '/');
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('static mode shows all tabs and defaults to Setup & Run', () => {
    window.history.pushState({}, '', '?static=1');
    render(<App />);
    postCompositeLoad({ id: 'test.composites.demo', name: 'demo' });

    // The tab strip is visible in static mode and includes every tab.
    expect(screen.getByRole('button', { name: /Setup & Run/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Results$/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Visualizations$/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Wiring$/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Document$/i })).toBeTruthy();
    // Default tab is Setup & Run → its read-only note renders.
    expect(screen.getByText(/read-only preview|live dashboard/i)).toBeTruthy();
  });

  it('static loader seeds parameters + steps from a resolve-dict stateUrl', async () => {
    const resolveDict = {
      id: 'test.composites.demo', name: 'demo',
      state: { top: {} },
      parameters: { seed: { type: 'int', default: 7, description: 'RNG seed' } },
      default_n_steps: 42,
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => resolveDict,
    }) as any);
    window.history.pushState({}, '', '?static=1&stateUrl=/x.json');
    render(<App />);
    // The Setup & Run form should show the published parameter + its default.
    // findAllByText: both the <code>seed</code> key and the "RNG seed" description render.
    expect((await screen.findAllByText((t) => t.includes('seed'))).length).toBeGreaterThan(0);
    const input = await screen.findByLabelText(/seed/i) as HTMLInputElement;
    expect(input.value).toBe('7');
  });
});

/** jsdom's localStorage here is partial (no clear(), not enumerable); install a
 *  Map-backed one and hand the map back so a test can inspect what was written. */
function installStorage(): Map<string, string> {
  const m = new Map<string, string>();
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k: string) => (m.has(k) ? (m.get(k) as string) : null),
      setItem: (k: string, v: string) => { m.set(k, String(v)); },
      removeItem: (k: string) => { m.delete(k); },
      clear: () => { m.clear(); },
      key: (i: number) => [...m.keys()][i] ?? null,
      get length() { return m.size; },
    } as Storage,
  });
  return m;
}

describe('App layout-mode switcher', () => {
  afterEach(() => {
    cleanup();
    window.history.pushState({}, '', '/');
  });

  it('renders one option per registered layout mode, defaulting to hierarchy', () => {
    render(<App />);
    postCompositeLoad({ id: 'test.composites.demo', name: 'demo' });
    const select = screen.getByTitle('Layout mode') as HTMLSelectElement;
    expect(select.value).toBe(DEFAULT_MODE_ID);
    expect([...select.options].map((o) => o.value)).toEqual(LAYOUT_MODES.map((m) => m.id));
  });

  it('a view naming an unregistered mode falls back to the default', async () => {
    // A ?view= link or .view.json from a build that had a mode this one does
    // not. `normalizeView` only checks the field is a string, so the id reaches
    // `applyView` unvalidated; the registry is what must reject it. Otherwise
    // state holds a phantom id, the <select> silently shows something else,
    // and positions persist under a localStorage key nothing ever reads back.
    const stored = installStorage();
    const encoded = encodeView({
      v: 1, positions: { x: { x: 1, y: 2 } }, collapsed: [], hidden: [],
      mode: 'mode-from-the-future', pins: [],
    } as any);
    window.history.pushState({}, '', `?view=${encoded}`);
    render(<App />);
    postCompositeLoad({ id: 'test.composites.demo', name: 'demo' });
    await act(async () => { await Promise.resolve(); });

    // The layout key is the observable proof of which mode state settled on:
    // the view's positions must land under the DEFAULT mode's (un-suffixed)
    // key, and no key may name the phantom mode. Asserting the whole set at
    // once also shows the view really WAS applied, so this can't pass vacuously.
    expect([...stored.keys()].filter((k) => k.startsWith('bigraph-loom:layout:')))
      .toEqual(['bigraph-loom:layout:test.composites.demo']);
    expect(stored.get('bigraph-loom:layout:test.composites.demo')).toContain('"x"');

    // The <select> agrees with state. On its own this assertion could NOT
    // catch the bug: React's controlled-select update leaves the previously
    // selected option selected when no option matches the value, so a phantom
    // modeId reads back as 'hierarchy' here while state holds the phantom.
    const select = screen.getByTitle('Layout mode') as HTMLSelectElement;
    expect(select.value).toBe(DEFAULT_MODE_ID);
  });
});

describe('App top bar', () => {
  it('shows the composite name and library from composite:load metadata', async () => {
    render(<App />);
    postCompositeLoad({
      id: 'pbg_biomodels.composites.compare-biomodel',
      name: 'compare-biomodel',
      library: 'pbg_biomodels',
    });
    expect(await screen.findByText('compare-biomodel')).toBeTruthy();
    expect(screen.getByText('pbg_biomodels')).toBeTruthy();
  });
});
