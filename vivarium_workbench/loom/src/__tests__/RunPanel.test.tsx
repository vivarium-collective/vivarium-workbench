// @vitest-environment jsdom
// Tests for run lifecycle — migrated to SetupRunPanel when RunPanel was merged
// into SetupRunPanel (Tasks 5+6).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { SetupRunPanel } from '../panels/SetupRunPanel';

beforeEach(() => {
  vi.resetModules();
  sessionStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

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

/** Minimum extra props needed by SetupRunPanel beyond the run-specific ones. */
const PANEL_EXTRAS = {
  parameters: {},
  overrides: {},
  onApplied: () => {},
  onCompleted: () => {},
};

describe('SetupRunPanel start-then-poll', () => {
  it('starts a run, polls status, and shows completion', async () => {
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
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <SetupRunPanel
        {...PANEL_EXTRAS}
        compositeId="pkg.composites.demo"
        emitSet={new Set()}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Run' }));

    await waitFor(() => expect(screen.getByText(/run complete/i)).toBeTruthy(),
      { timeout: 5000 });
    expect(sessionStorage.getItem('bigraph-loom:active-run')).toBeNull();
  });

  it('re-attaches to a running run from sessionStorage on mount', async () => {
    sessionStorage.setItem('bigraph-loom:active-run',
      JSON.stringify({ run_id: 'r-prev', composite_id: 'pkg.composites.demo' }));
    const fetchMock = mockFetchSequence({
      '/api/composite-run/r-prev/status': () => ({
        body: { run_id: 'r-prev', status: 'completed', progress_step: 5, n_steps: 5 },
      }),
      '/api/composite-run/r-prev': () => ({ body: { run_id: 'r-prev', trajectory: [] } }),
      '/api/composite-runs': () => ({ body: { runs: [] } }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <SetupRunPanel
        {...PANEL_EXTRAS}
        compositeId="pkg.composites.demo"
        emitSet={new Set()}
      />
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/composite-run/r-prev/status'),
      { timeout: 5000 });
  });
});

describe('SetupRunPanel run-kind (temporal vs workflow)', () => {
  it('temporal shows a Duration field; workflow hides it and just Runs', () => {
    const { rerender } = render(
      <SetupRunPanel {...PANEL_EXTRAS} compositeId="pkg.composites.temporal"
        emitSet={new Set()} runKind="temporal" />
    );
    expect(screen.getByText('Duration')).toBeTruthy();

    rerender(
      <SetupRunPanel {...PANEL_EXTRAS} compositeId="pkg.composites.flow"
        emitSet={new Set()} runKind="workflow" />
    );
    expect(screen.queryByText('Duration')).toBeNull();
    expect(screen.getByRole('button', { name: /^run$/i })).toBeTruthy();
  });

  it('a workflow run submits a single tick (steps: 1)', async () => {
    let body: any = null;
    const fetchMock = vi.fn((url: string, opts?: any) => {
      if (url.includes('/api/composite-test-run')) {
        body = JSON.parse(opts.body);
        return Promise.resolve({ ok: true, status: 202, json: async () => ({ run_id: 'r-w', status: 'running' }) });
      }
      if (url.includes('/api/composite-run/r-w/status'))
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ run_id: 'r-w', status: 'completed', progress_step: 1, n_steps: 1 }) });
      if (url.includes('/api/composite-run/r-w'))
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ run_id: 'r-w', trajectory: [] }) });
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ runs: [] }) });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <SetupRunPanel {...PANEL_EXTRAS} compositeId="pkg.composites.flow"
        emitSet={new Set()} runKind="workflow" />
    );
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
    await waitFor(() => expect(body).not.toBeNull(), { timeout: 5000 });
    expect(body.steps).toBe(1);
  });

  // Falsifies: the page caps a run's Duration (a multi-generation compose-api run needs ~2,527 per generation). How
  // long a run may be is the backend's to say (compose-api: its long-runs role); the page sends what was typed.
  it('a long Duration is sent as typed, not capped', async () => {
    let body: any = null;
    const fetchMock = vi.fn((url: string, opts?: any) => {
      if (url.includes('/api/composite-test-run')) {
        body = JSON.parse(opts.body);
        return Promise.resolve({ ok: true, status: 202, json: async () => ({ run_id: 'r-l', status: 'running' }) });
      }
      if (url.includes('/api/composite-run/r-l/status'))
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ run_id: 'r-l', status: 'running', progress_step: 0, n_steps: 20000 }) });
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ runs: [] }) });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<SetupRunPanel {...PANEL_EXTRAS} compositeId="pkg.composites.long" emitSet={new Set()} />);
    fireEvent.change(screen.getByLabelText(/Duration/), { target: { value: '20000' } });
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }));
    await waitFor(() => expect(body).not.toBeNull(), { timeout: 5000 });
    expect(body.steps).toBe(20000);   // past the input's max=10000 too: that attribute only bounds the stepper
  });
});
