// src/viewStore.ts — named "views" for a composite: a saved arrangement +
// visibility, persisted per-composite in localStorage, plus portable encodings
// (compressed URL param + JSON file) so a view can be shared or featured from a
// README link.
//
// A VIEW captures everything that defines how the graph looks:
//   - positions: {nodeId -> {x,y}}   (the arrangement; see layoutStore)
//   - collapsed: group-store ids collapsed
//   - hidden:    node ids explicitly hidden via the sidebar toggles
// Viewport (zoom/pan) is intentionally NOT stored — it re-fits on apply.
//
// Persistence shape, one key per composite id:
//   localStorage["bigraph-loom:views:<composite-id>"] = JSON {
//     default: <name> | null,
//     views: { [name]: View }
//   }
//
// Portable forms:
//   - URL param   ?view=<lz>      lz-string-compressed JSON of a single View
//   - file / URL  ?viewUrl=<url>  fetches JSON (a single View) and applies it
// Both let a README link open the loom in a preset arrangement without the
// viewer's localStorage.

import { compressToEncodedURIComponent, decompressFromEncodedURIComponent } from 'lz-string';
import type { LayoutPositions } from './layoutStore';

export type View = {
  /** Schema version, for forward-compat. */
  v?: 1;
  positions: LayoutPositions;
  collapsed: string[];
  hidden: string[];
  /** Layout mode this view was captured in. Absent means 'hierarchy'. */
  mode?: string;
  /** Pinned process node ids (process-column mode). */
  pins?: string[];
};

export type ViewStore = {
  default: string | null;
  views: Record<string, View>;
};

const KEY_PREFIX = 'bigraph-loom:views:';

function keyFor(compositeId: string | null | undefined): string | null {
  return compositeId ? KEY_PREFIX + compositeId : null;
}

/** A fresh empty store. MUST construct new objects each call — a shared default
 *  would be mutated by saveView and leak entries across composites. */
function emptyStore(): ViewStore {
  return { default: null, views: {} };
}

/** Read the full view-store for a composite. Returns an empty store on miss/parse error. */
export function loadViewStore(compositeId: string | null | undefined): ViewStore {
  const k = keyFor(compositeId);
  if (!k) return emptyStore();
  try {
    const raw = window.localStorage.getItem(k);
    if (!raw) return emptyStore();
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return emptyStore();
    return {
      default: typeof parsed.default === 'string' ? parsed.default : null,
      views: (parsed.views && typeof parsed.views === 'object') ? parsed.views : {},
    };
  } catch {
    return emptyStore();
  }
}

function writeViewStore(compositeId: string | null | undefined, store: ViewStore): void {
  const k = keyFor(compositeId);
  if (!k) return;
  try {
    window.localStorage.setItem(k, JSON.stringify(store));
  } catch {
    // quota / disabled storage: persistence is best-effort, never throw.
  }
}

/** Sorted list of saved view names for a composite. */
export function listViews(compositeId: string | null | undefined): string[] {
  return Object.keys(loadViewStore(compositeId).views).sort((a, b) => a.localeCompare(b));
}

/** The name of the default view for a composite, or null. */
export function getDefaultName(compositeId: string | null | undefined): string | null {
  const s = loadViewStore(compositeId);
  return s.default && s.views[s.default] ? s.default : null;
}

/** The default View object for a composite (the one to load on open), or null. */
export function getDefaultView(compositeId: string | null | undefined): View | null {
  const s = loadViewStore(compositeId);
  return (s.default && s.views[s.default]) ? normalizeView(s.views[s.default]) : null;
}

/** Fetch a single named view. */
export function getView(compositeId: string | null | undefined, name: string): View | null {
  const v = loadViewStore(compositeId).views[name];
  return v ? normalizeView(v) : null;
}

/** Save (or overwrite) a named view. Returns the updated store. */
export function saveView(
  compositeId: string | null | undefined,
  name: string,
  view: View,
): ViewStore {
  const s = loadViewStore(compositeId);
  s.views[name] = normalizeView(view);
  // First saved view becomes the default automatically.
  if (!s.default || !s.views[s.default]) s.default = name;
  writeViewStore(compositeId, s);
  return s;
}

/** Delete a named view. Clears `default` if it pointed at the deleted view. */
export function deleteView(compositeId: string | null | undefined, name: string): ViewStore {
  const s = loadViewStore(compositeId);
  delete s.views[name];
  if (s.default === name) {
    const remaining = Object.keys(s.views);
    s.default = remaining.length ? remaining.sort()[0] : null;
  }
  writeViewStore(compositeId, s);
  return s;
}

/** Mark a view as the default (loaded on open). Pass null to clear. */
export function setDefault(compositeId: string | null | undefined, name: string | null): ViewStore {
  const s = loadViewStore(compositeId);
  s.default = (name && s.views[name]) ? name : null;
  writeViewStore(compositeId, s);
  return s;
}

/** Coerce an arbitrary object into a well-formed View (drops junk, fills gaps). */
export function normalizeView(v: any): View {
  return {
    v: 1,
    positions: (v && typeof v.positions === 'object' && v.positions) ? v.positions : {},
    collapsed: Array.isArray(v?.collapsed) ? v.collapsed.map(String) : [],
    hidden: Array.isArray(v?.hidden) ? v.hidden.map(String) : [],
    // A view saved before layout modes existed has no `mode` — it was captured
    // in the hierarchy layout, so default there and keep old ?view= links working.
    mode: typeof v?.mode === 'string' ? v.mode : 'hierarchy',
    pins: Array.isArray(v?.pins) ? v.pins.filter((p: unknown) => typeof p === 'string') : [],
  };
}

// ---- Portable encodings -----------------------------------------------------

/** Compress a View into a URL-safe string for `?view=`. */
export function encodeView(view: View): string {
  return compressToEncodedURIComponent(JSON.stringify(normalizeView(view)));
}

/** Decode a `?view=` string back into a View, or null on failure. */
export function decodeView(encoded: string | null | undefined): View | null {
  if (!encoded) return null;
  try {
    const json = decompressFromEncodedURIComponent(encoded);
    if (!json) return null;
    return normalizeView(JSON.parse(json));
  } catch {
    return null;
  }
}

/** Build a shareable URL for the current page that opens with `view` applied. */
export function shareableUrl(view: View): string {
  const url = new URL(window.location.href);
  url.searchParams.delete('viewUrl');
  url.searchParams.set('view', encodeView(view));
  return url.toString();
}

/** Fetch a View from a JSON file URL (for `?viewUrl=` / README links). */
export async function fetchView(url: string): Promise<View | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return normalizeView(await res.json());
  } catch {
    return null;
  }
}
