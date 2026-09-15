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

  it('stopRun POSTs to the stop endpoint and returns the outcome', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ run_id: 'r-1', outcome: 'signalled', status: 'cancelled' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { stopRun } = await import('../api');
    const res = await stopRun('r-1');
    expect(fetchMock).toHaveBeenCalledWith('/api/composite-run/r-1/stop', expect.objectContaining({
      method: 'POST',
    }));
    expect(res).toEqual({ run_id: 'r-1', outcome: 'signalled', status: 'cancelled' });
  });

  it('stopRun surfaces a server error', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: 'not_found' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { stopRun } = await import('../api');
    await expect(stopRun('nope')).rejects.toThrow(/not_found/);
  });
});

describe('parseUrlOverrides', () => {
  it('parses a valid ?overrides= JSON object', async () => {
    const { parseUrlOverrides } = await import('../api');
    expect(parseUrlOverrides('?id=x&overrides=%7B%22n_generations%22%3A7%7D'))
      .toEqual({ n_generations: 7 });
  });
  it('returns {} for absent / invalid / non-object overrides', async () => {
    const { parseUrlOverrides } = await import('../api');
    expect(parseUrlOverrides('?id=x')).toEqual({});
    expect(parseUrlOverrides('?overrides=not-json')).toEqual({});
    expect(parseUrlOverrides('?overrides=%5B1%2C2%5D')).toEqual({}); // a JSON array
    expect(parseUrlOverrides('?overrides=42')).toEqual({});         // a scalar
  });
});

describe('cloud-run dispatch protocol', () => {
  // The embedding card owns robust tracking of a slow (~20s) Cloud dispatch; the
  // loom hands it the sms-api sim id and dispatch state via postMessage. Tests
  // capture via a mock opener (the embedding-target branch _embeddingTarget hits
  // first — same pattern as the postMessage-protocol suite above).
  const mockOpener = { postMessage: vi.fn() };
  beforeEach(() => {
    mockOpener.postMessage.mockReset();
    Object.defineProperty(window, 'opener', { value: mockOpener, configurable: true, writable: true });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    Object.defineProperty(window, 'opener', { value: null, configurable: true, writable: true });
    window.history.replaceState({}, '', '/');
  });

  it('the three posters hit the embedding target with typed payloads', async () => {
    const api = await import('../api');
    api.postRemoteDispatching(212);
    expect(mockOpener.postMessage).toHaveBeenCalledWith(
      { type: 'explore:remote-dispatching', build_sim: 212 }, '*');
    api.postRemoteDispatched({ run_id: 'remote-sim-9', simulation_id: 9, experiment_id: 'exp1' });
    expect(mockOpener.postMessage).toHaveBeenCalledWith(
      { type: 'explore:remote-dispatched', run_id: 'remote-sim-9', simulation_id: 9, experiment_id: 'exp1' }, '*');
    api.postRemoteDispatchFailed('boom');
    expect(mockOpener.postMessage).toHaveBeenCalledWith(
      { type: 'explore:remote-dispatch-failed', error: 'boom' }, '*');
  });

  it('startRun hands the parent the sms-api sim id on a remote 202', async () => {
    // No Cloud scope in the URL → the pinned/materialized path; the backend still
    // returns remote:true, and the card must get explore:remote-dispatched.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, status: 202,
      json: async () => ({ run_id: 'remote-sim-77', status: 'running',
        remote: true, simulation_id: 77, experiment_id: 'e1' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { startRun } = await import('../api');
    const res = await startRun({ id: 'pkg.composites.demo', steps: 5, emit_paths: [] });
    expect(res.remote).toBe(true);
    expect(mockOpener.postMessage).toHaveBeenCalledWith(
      { type: 'explore:remote-dispatched', run_id: 'remote-sim-77', simulation_id: 77, experiment_id: 'e1' }, '*');
  });

  it('Cloud-scoped startRun posts dispatching, then dispatch-failed on error', async () => {
    window.history.replaceState({}, '', '/?run_target=deployment&build_sim=212&build_repo=r&build_commit=c');
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false, status: 502, json: async () => ({ error: 'cloud dispatch failed: boom' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { startRun } = await import('../api');
    await expect(startRun({ id: 'x', steps: 1, emit_paths: [] })).rejects.toThrow(/cloud dispatch failed/);
    expect(mockOpener.postMessage).toHaveBeenCalledWith(
      { type: 'explore:remote-dispatching', build_sim: 212 }, '*');
    expect(mockOpener.postMessage).toHaveBeenCalledWith(
      { type: 'explore:remote-dispatch-failed', error: 'cloud dispatch failed: boom' }, '*');
  });
});
