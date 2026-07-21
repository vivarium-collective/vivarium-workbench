// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Reset module cache between tests to get a fresh import (important for the
// postMessage spy tests which import after spying).
beforeEach(() => {
  vi.resetModules();
});

describe('postMessage protocol', () => {
  // The api helpers post to window.opener (popup mode) or window.parent (iframe
  // mode). In jsdom both default to `window` itself, which the helper treats as
  // "no embedding target" and silently no-ops. Install a mock opener so the
  // spy captures the call.
  const mockOpener = { postMessage: vi.fn() };

  beforeEach(() => {
    mockOpener.postMessage.mockReset();
    Object.defineProperty(window, 'opener', {
      value: mockOpener,
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'opener', {
      value: null,
      configurable: true,
      writable: true,
    });
  });

  it('postReady fires the embedding-target message', async () => {
    const { postReady } = await import('../api');
    postReady();
    expect(mockOpener.postMessage).toHaveBeenCalledWith({ type: 'explore:ready' }, '*');
  });

  it('postInspect includes path, kind, details', async () => {
    const { postInspect } = await import('../api');
    postInspect({ path: ['a', 'b'], kind: 'store', details: { foo: 1 } });
    expect(mockOpener.postMessage).toHaveBeenCalledWith(
      { type: 'explore:inspect', path: ['a', 'b'], kind: 'store', details: { foo: 1 } },
      '*'
    );
  });

  it('postReady is a no-op when there is no embedding target', async () => {
    Object.defineProperty(window, 'opener', { value: null, configurable: true, writable: true });
    const { postReady } = await import('../api');
    expect(() => postReady()).not.toThrow();
    expect(mockOpener.postMessage).not.toHaveBeenCalled();
  });

  it('onCompositeLoad invokes handler for matching messages', async () => {
    const { onCompositeLoad } = await import('../api');
    const handler = vi.fn();
    const off = onCompositeLoad(handler);
    window.dispatchEvent(new MessageEvent('message', {
      data: { type: 'composite:load', state: { foo: 1 } },
    }));
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].state).toEqual({ foo: 1 });
    off();
  });

  it('onCompositeLoad ignores non-matching messages', async () => {
    const { onCompositeLoad } = await import('../api');
    const handler = vi.fn();
    const off = onCompositeLoad(handler);
    window.dispatchEvent(new MessageEvent('message', { data: { type: 'something-else' } }));
    expect(handler).not.toHaveBeenCalled();
    off();
  });
});

describe('run lifecycle fetch helpers', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('startRun POSTs to composite-test-run and returns run_id', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ run_id: 'r-1', status: 'running' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { startRun } = await import('../api');
    const res = await startRun({ id: 'pkg.composites.demo', steps: 5, emit_paths: [] });
    expect(fetchMock).toHaveBeenCalledWith('/api/composite-test-run', expect.objectContaining({
      method: 'POST',
    }));
    expect(res).toEqual({ run_id: 'r-1', status: 'running' });
  });

  it('startRun surfaces a 429 cap error', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({ error: 'too many runs in progress' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { startRun } = await import('../api');
    await expect(startRun({ id: 'x', steps: 1, emit_paths: [] }))
      .rejects.toThrow(/too many runs/);
  });

  it('fetchRunStatus GETs the status endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ run_id: 'r-1', status: 'completed', progress_step: 5, n_steps: 5 }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { fetchRunStatus } = await import('../api');
    const res = await fetchRunStatus('r-1');
    expect(fetchMock).toHaveBeenCalledWith('/api/composite-run/r-1/status');
    expect(res.status).toBe('completed');
  });

  it('fetchRunTrajectory GETs the run endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ run_id: 'r-1', trajectory: [{ step: 0, state: {} }] }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { fetchRunTrajectory } = await import('../api');
    const res = await fetchRunTrajectory('r-1');
    expect(fetchMock).toHaveBeenCalledWith('/api/composite-run/r-1');
    expect(res.trajectory).toHaveLength(1);
  });
});
