// @vitest-environment jsdom
// Tests for the run-bar poll resilience (f1): a run bar must never freeze on
// "running" forever, and must never resume a dead run.
//
//   1. fetchRunStatus throws a RunStatusError carrying the HTTP status (and the
//      proxied phase), so the poll loop can tell 404 (run gone) from a transient
//      blip and from a 502 cloud-link outage.
//   2. On 404 the poll terminates: it stops, clears the active-run key, drops
//      the run, and shows a one-line "no longer tracked" notice.
//   3. On a transient failure the bar reads "reconnecting…" instead of a frozen
//      status, keeping the run id.
//   4. A stale active-run entry (>24h old) is not resumed on mount.
//
// Cases 2-4 drive the hook through ExploreRunBar's re-attach path (an active run
// recorded in sessionStorage), matching the existing ExploreRunBar tests.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { ExploreRunBar } from '../panels/ExploreRunBar';
import { fetchRunStatus, type RunStatusError } from '../api';

const ACTIVE_RUN_KEY = 'bigraph-loom:active-run';
const COMPOSITE_ID = 'some.composite.id';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); sessionStorage.clear(); });

const BASE_PROPS = {
  compositeId: COMPOSITE_ID,
  overrides: {},
  emitSet: new Set<string>(),
};

function setActiveRun(extra: Record<string, unknown> = {}) {
  sessionStorage.setItem(ACTIVE_RUN_KEY, JSON.stringify({
    run_id: 'r-1', composite_id: COMPOSITE_ID, started_at: Date.now(), ...extra,
  }));
}

describe('fetchRunStatus error shape', () => {
  it('throws a RunStatusError with the HTTP status on !ok', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: false, status: 404, json: async () => ({ error: 'run not found' }),
    })) as unknown as typeof fetch);
    await expect(fetchRunStatus('r-1')).rejects.toMatchObject({ status: 404 });
  });

  it('carries the proxied phase for a 502 cloud-link outage', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: false, status: 502, json: async () => ({ phase: 'unreachable' }),
    })) as unknown as typeof fetch);
    let err: RunStatusError | null = null;
    try { await fetchRunStatus('r-1'); } catch (e) { err = e as RunStatusError; }
    expect(err?.status).toBe(502);
    expect(err?.phase).toBe('unreachable');
  });

  it('reports status 0 for a network-level failure', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))) as unknown as typeof fetch);
    await expect(fetchRunStatus('r-1')).rejects.toMatchObject({ status: 0 });
  });
});

describe('run-bar poll resilience', () => {
  it('terminates the poll and shows a notice when the run 404s', async () => {
    setActiveRun();
    const spy = vi.fn((url: string) => {
      if (String(url).endsWith('/status')) {
        return Promise.resolve({ ok: false, status: 404, json: async () => ({ error: 'run not found' }) });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ run_id: 'r-1', trajectory: [] }) });
    });
    vi.stubGlobal('fetch', spy as unknown as typeof fetch);

    render(<ExploreRunBar {...BASE_PROPS} />);

    // The one-line terminal notice appears…
    await screen.findByText(/no longer tracked by the server/i);
    // …the active-run key is cleared so a remount does not re-resume it…
    expect(sessionStorage.getItem(ACTIVE_RUN_KEY)).toBeNull();
    // …and there is no Stop button (the run is no longer "running").
    expect(screen.queryByRole('button', { name: /Stop/i })).toBeNull();
  });

  it('shows "reconnecting…" on a transient failure and keeps polling', async () => {
    setActiveRun();
    const spy = vi.fn(() => Promise.reject(new TypeError('Failed to fetch')));
    vi.stubGlobal('fetch', spy as unknown as typeof fetch);

    render(<ExploreRunBar {...BASE_PROPS} />);

    await screen.findByText(/reconnecting/i);
    // The active run is kept (transient, not terminal), so it can recover.
    expect(sessionStorage.getItem(ACTIVE_RUN_KEY)).not.toBeNull();
  });

  it('does not resume an active run older than 24h', async () => {
    setActiveRun({ started_at: Date.now() - (25 * 60 * 60 * 1000) });
    const spy = vi.fn(() => Promise.resolve({
      ok: true, status: 200, json: async () => ({ run_id: 'r-1', status: 'running', progress_step: 1, n_steps: 5 }),
    }));
    vi.stubGlobal('fetch', spy as unknown as typeof fetch);

    render(<ExploreRunBar {...BASE_PROPS} />);

    // The stale entry is dropped without ever polling /status.
    await waitFor(() => expect(sessionStorage.getItem(ACTIVE_RUN_KEY)).toBeNull());
    expect(spy).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /Stop/i })).toBeNull();
  });
});
