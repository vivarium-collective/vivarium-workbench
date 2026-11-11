// @vitest-environment jsdom
// SKIPPED — tests behavior that was REMOVED, not merely drifted (tracking: #1595).
// The old design had a tab strip and auto-advanced to a 'Results' TAB on run
// completion (`onCompleted={() => setTab('results')}`). The redesign removed the
// tab strip: there is no 'results' tab (TabId is 'setup'|'outputs'|'wiring'|
// 'document'), the Outputs section is permanently docked (its 'Results' heading
// is always present), and `onCompleted` is now a no-op (App.tsx ~L2879) — on
// completion `setOutputsOpen(true)` just OPENS the docked Outputs.
// The successor behavior worth covering is "a completed run opens the docked
// Outputs and populates Results", but driving the full SetupRunPanel poll loop
// to a terminal status is flaky under jsdom. Left skipped for a dedicated
// rewrite rather than a fragile or assert-to-pass test.
import { describe, it, expect, beforeAll, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act, cleanup } from '@testing-library/react';
import App from '../App';

// React Flow (rendered in the always-mounted Wiring tab) relies on
// ResizeObserver, which jsdom doesn't provide.
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState({}, '', '/');
});

function postCompositeLoad(metadata: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', {
      data: { type: 'composite:load', state: {}, metadata },
    }));
  });
}

/** Same sequenced-fetch mock helper as RunPanel.test.tsx. */
function mockFetchSequence(handlers: Record<string, () => any>) {
  return vi.fn((url: string, _opts?: any) => {
    for (const [pattern, fn] of Object.entries(handlers)) {
      if (url.includes(pattern)) {
        const { status = 200, body } = fn();
        return Promise.resolve({
          ok: status >= 200 && status < 300,
          status,
          json: async () => body,
        });
      }
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
}

describe('App auto-advance to Results on run completion', () => {
  it.skip('switches the active tab to Results once the polled run status reaches "completed"', async () => {
    let statusCalls = 0;
    const fetchMock = mockFetchSequence({
      '/api/composite-test-run': () => ({ status: 202, body: { run_id: 'r-1', status: 'running' } }),
      '/api/composite-run/r-1/status': () => {
        statusCalls += 1;
        return statusCalls < 2
          ? { body: { run_id: 'r-1', status: 'running', progress_step: 1, n_steps: 3 } }
          : { body: { run_id: 'r-1', status: 'completed', progress_step: 3, n_steps: 3 } };
      },
      '/api/composite-run/r-1': () => ({ body: { run_id: 'r-1', trajectory: [] } }),
      '/api/composite-runs': () => ({ body: { runs: [] } }),
      '/api/run-complete': () => ({ body: {} }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    postCompositeLoad({ id: 'pkg.composites.demo', name: 'demo' });

    // Starts on the Setup & Run tab.
    expect(screen.getByRole('button', { name: /^Run$/i })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Results' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /^Run$/i }));

    // Once the poll loop observes `status: 'completed'`, App should have
    // flipped `tab` to 'results': the Results panel (unique "Results"
    // heading) mounts and the Setup panel's Run button unmounts.
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Results' })).toBeTruthy(),
      { timeout: 5000 });
    expect(screen.queryByRole('button', { name: /^Run$/i })).toBeNull();

    // The Results tab strip button reflects the active tab too.
    const resultsTabBtn = screen.getByRole('button', { name: /^Results$/i });
    expect(resultsTabBtn.style.color).toBe('rgb(37, 99, 235)');
  });
});
