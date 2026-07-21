// @vitest-environment jsdom
import { describe, it, expect, beforeAll, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import App from '../App';

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
