// src/storeColor.ts — a deterministic, book-harmonious color per STORE.
//
// Every process↔store wire, the port dot it leaves from, and the store it
// arrives at are painted the SAME hue, so a reader can trace source→target by
// color even where wires are long or cross (loom problem #1). The ramp is the
// book palette's accent set (mirrors the --bk-wire-* tokens in App.css); pure
// concrete hex (never a CSS var) so the values survive inline into the SVG/PNG
// figure export.

// Teal, gold, slate-navy, terracotta, muted, plum — chosen to sit together as a
// keyed diagram rather than a rainbow, and to stay distinct at print scale.
const WIRE_RAMP = [
  '#1f7a72', // teal
  '#a9781f', // gold
  '#4a6f8a', // slate-navy
  '#8a5a3c', // terracotta
  '#7a5a86', // plum
  '#5f7a4a', // moss
] as const;

/** Root/empty ids collapse to one key so the composite root is one color. */
function normId(id: string): string {
  return id === '' ? '<root>' : id;
}

/** Stable 32-bit string hash (FNV-1a) → an even spread over the ramp. */
function hash(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** The wire/port/store color for a store id (`pathKey` form, `.`-joined path). */
export function storeColor(id: string): string {
  return WIRE_RAMP[hash(normId(id)) % WIRE_RAMP.length];
}
