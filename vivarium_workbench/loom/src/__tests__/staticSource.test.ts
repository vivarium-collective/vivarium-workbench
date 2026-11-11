import { describe, it, expect, vi } from 'vitest';
import { resolveCompositeSrc, resolveViaManifest, manifestUrlFor } from '../staticSource';

const api = (id: string) => `/api/composite-state?ref=${encodeURIComponent(id)}`;

function fetchStub(routes: Record<string, unknown>, fail = new Set<string>()) {
  return vi.fn(async (url: unknown) => {
    const u = String(url);
    const key = Object.keys(routes).find((k) => u.includes(k));
    if (!key || fail.has(key)) return { ok: false, status: 404, json: async () => ({}) } as Response;
    return { ok: true, status: 200, json: async () => routes[key] } as Response;
  }) as unknown as typeof fetch;
}

const manifest = { composites: [{ id: 'whole-cell-ecoli', file: 'whole-cell-ecoli.json' }] };

describe('resolveCompositeSrc — serverless-first', () => {
  it('?src= is used directly (alias for ?stateUrl=)', async () => {
    const src = await resolveCompositeSrc({
      params: new URLSearchParams('?static=1&src=/data/x.json'),
      compositeId: null, isStatic: true, apiFallback: api, fetchImpl: fetchStub({}),
    });
    expect(src).toBe('/data/x.json');
  });

  it('?stateUrl= still works and takes precedence over ?id=', async () => {
    const src = await resolveCompositeSrc({
      params: new URLSearchParams('?static=1&id=whole-cell-ecoli&stateUrl=/snap.json'),
      compositeId: 'whole-cell-ecoli', isStatic: true, apiFallback: api,
      fetchImpl: fetchStub({ 'manifest.json': manifest }),
    });
    expect(src).toBe('/snap.json');
  });

  it('?id= resolves against the conventional sibling manifest under ?static=1', async () => {
    const f = fetchStub({ 'composites/manifest.json': manifest });
    const src = await resolveCompositeSrc({
      params: new URLSearchParams('?static=1&id=whole-cell-ecoli'),
      compositeId: 'whole-cell-ecoli', isStatic: true, apiFallback: api, fetchImpl: f,
    });
    // Resolved to the file sibling to the manifest — never the server API.
    expect(src).toBe('composites/whole-cell-ecoli.json');
    expect((f as any).mock.calls.map((c: any[]) => String(c[0]))).toContain('composites/manifest.json');
  });

  it('?id= + explicit ?manifest= resolves the file relative to that manifest', async () => {
    const src = await resolveCompositeSrc({
      params: new URLSearchParams('?id=whole-cell-ecoli&manifest=/catalog/m.json'),
      compositeId: 'whole-cell-ecoli', isStatic: false, apiFallback: api,
      fetchImpl: fetchStub({ '/catalog/m.json': manifest }),
    });
    expect(src).toBe('/catalog/whole-cell-ecoli.json');
  });

  it('live workbench (?id=, no manifest, not static) falls back to the server API unchanged', async () => {
    const f = fetchStub({});
    const src = await resolveCompositeSrc({
      params: new URLSearchParams('?id=test.composites.demo'),
      compositeId: 'test.composites.demo', isStatic: false, apiFallback: api, fetchImpl: f,
    });
    expect(src).toBe('/api/composite-state?ref=test.composites.demo');
    // No manifest is consulted in live mode.
    expect((f as any).mock.calls.length).toBe(0);
  });

  it('a static ?id= missing from the manifest falls back to the API (does not hang)', async () => {
    const src = await resolveCompositeSrc({
      params: new URLSearchParams('?static=1&id=not-there'),
      compositeId: 'not-there', isStatic: true, apiFallback: api,
      fetchImpl: fetchStub({ 'composites/manifest.json': manifest }),
    });
    expect(src).toBe('/api/composite-state?ref=not-there');
  });

  it('a failed manifest fetch falls back to the API', async () => {
    const src = await resolveCompositeSrc({
      params: new URLSearchParams('?static=1&id=whole-cell-ecoli'),
      compositeId: 'whole-cell-ecoli', isStatic: true, apiFallback: api,
      fetchImpl: fetchStub({ 'composites/manifest.json': manifest }, new Set(['composites/manifest.json'])),
    });
    expect(src).toBe('/api/composite-state?ref=whole-cell-ecoli');
  });

  it('no id and no direct url resolves to null', async () => {
    const src = await resolveCompositeSrc({
      params: new URLSearchParams('?static=1'),
      compositeId: null, isStatic: true, apiFallback: api, fetchImpl: fetchStub({}),
    });
    expect(src).toBeNull();
  });
});

describe('manifestUrlFor / resolveViaManifest', () => {
  it('explicit ?manifest= wins over the static default', () => {
    expect(manifestUrlFor(new URLSearchParams('?static=1&manifest=/m.json'), true)).toBe('/m.json');
  });
  it('no manifest in live mode', () => {
    expect(manifestUrlFor(new URLSearchParams('?id=x'), false)).toBeNull();
  });
  it('matches an entry by file name too', async () => {
    const src = await resolveViaManifest({
      params: new URLSearchParams('?manifest=/m.json'),
      compositeId: 'whole-cell-ecoli.json', isStatic: false,
      fetchImpl: fetchStub({ '/m.json': manifest }),
    });
    expect(src).toBe('/whole-cell-ecoli.json');
  });
});
