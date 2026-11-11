// @vitest-environment jsdom
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup, fireEvent, waitFor } from '@testing-library/react';
import App from '../App';
import { DEFAULT_MODE_ID } from '../layouts/registry';
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

// Rendering <App/> fires passive effects that fetch relative URLs (e.g.
// /api/composite-layout persistence). node's fetch rejects on a relative URL,
// which surfaces as an unhandled rejection (vitest fails the run) even though the
// assertions pass. Default fetch to a resolved no-op so unmocked renders are
// quiet; tests that need real responses override with their own vi.stubGlobal.
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true, status: 200, json: async () => ({}), text: async () => '',
  })) as unknown as typeof fetch);
});
afterEach(() => { vi.unstubAllGlobals(); });

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

  it('static mode renders the explorer read-only with a docked Outputs section', () => {
    window.history.pushState({}, '', '?static=1');
    render(<App />);
    postCompositeLoad({ id: 'test.composites.demo', name: 'demo' });

    // The old tab strip (Explore / Setup & Run / Results / …) was replaced by an
    // always-on graph plus a docked Outputs section. In static mode (no live
    // server) the UI renders read-only, with Outputs → Results + Visualizations
    // and a Run control present (disabled read-only, but mounted).
    expect(screen.getAllByText(/read-only|live dashboard/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Outputs/i)).toBeTruthy();
    expect(screen.getAllByText(/^Results$/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/^Visualizations$/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Run/i })).toBeTruthy();
  });

  it('static loader seeds parameters from a resolve-dict stateUrl', async () => {
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
    // The published parameter loads into the Configure form (its own side tab).
    // Wait for the tab to report its 1 input (params fetched) before opening it.
    const cfgTab = await screen.findByRole('tab', { name: /Configure.*1/ });
    fireEvent.click(cfgTab);
    let seed: HTMLInputElement | undefined;
    await waitFor(() => {
      seed = Array.from(document.querySelectorAll<HTMLInputElement>('input')).find(
        (i) => i.id.includes('seed') || (i.getAttribute('aria-label') || '').includes('seed'),
      );
      expect(seed).toBeTruthy();
    });
    // It carries the published default (7).
    expect(seed!.value).toBe('7');
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

describe('App layout modes', () => {
  afterEach(() => {
    cleanup();
    window.history.pushState({}, '', '/');
  });

  it('offers the registered layout modes via the Layout menu', () => {
    render(<App />);
    postCompositeLoad({ id: 'test.composites.demo', name: 'demo' });
    // The single-hierarchy design was replaced by a multi-mode registry exposed
    // through the Layout menu (flow-down is the default).
    fireEvent.click(screen.getByTitle(/Layout — direction/i));
    for (const label of ['Tree', 'Grid', 'Packing', 'Flow']) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(DEFAULT_MODE_ID).toBe('flow-down');
  });

  it('a view naming an unregistered mode falls back to the default', async () => {
    // A ?view= link or .view.json from a build that had a mode this one does
    // not. `normalizeView` only checks the field is a string, so the id reaches
    // `applyView` unvalidated; the registry is what must reject it. Otherwise
    // positions persist under a localStorage key nothing ever reads back.
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
    // the view's positions must land under the DEFAULT mode's key, and no key
    // may name the phantom mode. The default is now 'flow-down', which (not being
    // 'hierarchy') gets a ':<modeId>' suffix (see layoutStore.keyFor). Asserting
    // the whole set at once also shows the view really WAS applied, so this can't
    // pass vacuously.
    const defaultKey = 'bigraph-loom:layout:v2:test.composites.demo:flow-down';
    expect([...stored.keys()].filter((k) => k.startsWith('bigraph-loom:layout:')))
      .toEqual([defaultKey]);
    expect(stored.get(defaultKey)).toContain('"x"');
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
