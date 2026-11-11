// Resolve where the explorer should fetch a composite document from, favouring
// serverless (static) sources so a standalone loom URL renders with no server and
// no opener. Used by App's id/stateUrl bootstrap effect.
//
//   ?stateUrl=<url> / ?src=<url>  — fetch a composite document straight from a URL.
//   ?id=<id> + ?manifest=<url>    — resolve <id> to its file via a static manifest
//                                   ({ composites: [{ id, file, … }] }); with
//                                   ?static=1 the conventional sibling
//                                   ./composites/manifest.json is tried.
//
// In a live workbench (?id= with neither ?manifest nor ?static) nothing changes:
// the id resolves against the server API via `apiFallback`.

export interface ManifestEntry {
  id?: string;
  file?: string;
  label?: string;
  description?: string;
}

export interface ResolveSrcOptions {
  params: URLSearchParams;
  compositeId: string | null;
  isStatic: boolean;
  /** Server URL for a composite id when no static source resolves (live mode). */
  apiFallback: (id: string) => string;
  /** Injectable for tests; defaults to global fetch. */
  fetchImpl?: typeof fetch;
}

/**
 * Resolve the manifest URL to try for a `?id=` lookup: an explicit `?manifest=`,
 * else the conventional sibling `composites/manifest.json` when in static mode,
 * else none.
 */
export function manifestUrlFor(params: URLSearchParams, isStatic: boolean): string | null {
  return params.get('manifest') || (isStatic ? 'composites/manifest.json' : null);
}

/** Resolve `?id=` to its composite file via a static manifest, or null. */
export async function resolveViaManifest(
  opts: Pick<ResolveSrcOptions, 'params' | 'compositeId' | 'isStatic' | 'fetchImpl'>,
): Promise<string | null> {
  const { params, compositeId, isStatic } = opts;
  if (!compositeId) return null;
  const manifestUrl = manifestUrlFor(params, isStatic);
  if (!manifestUrl) return null;
  const f = opts.fetchImpl ?? fetch;
  try {
    const r = await f(manifestUrl);
    if (!r.ok) return null;
    const m = await r.json();
    const entries: ManifestEntry[] = (m && m.composites) || [];
    const hit = entries.find((e) => e.id === compositeId || e.file === compositeId);
    if (!hit || !hit.file) return null;
    return manifestUrl.replace(/[^/]*$/, '') + hit.file; // sibling of the manifest
  } catch {
    return null;
  }
}

/** The composite document URL to fetch, static-first, or null if none applies. */
export async function resolveCompositeSrc(opts: ResolveSrcOptions): Promise<string | null> {
  const direct = opts.params.get('stateUrl') || opts.params.get('src');
  if (direct) return direct;
  const viaManifest = await resolveViaManifest(opts);
  if (viaManifest) return viaManifest;
  return opts.compositeId ? opts.apiFallback(opts.compositeId) : null;
}
