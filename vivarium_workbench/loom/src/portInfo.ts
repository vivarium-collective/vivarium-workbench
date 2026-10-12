// src/portInfo.ts — what one process port advertises: its TYPE and WHERE it wires.
//
// A pure function that folds a port's several data sources (the process's
// _inputs/_outputs type schema, the raw authored wire target, and the resolved
// absolute store path) into the small shape the port hover tooltip and the
// Inspector render. No React, no DOM — unit-tested in isolation.

import { abbreviateType } from './contract';

export interface PortInfo {
  port: string;
  /** A port on `_inputs` READS its store; one on `_outputs` WRITES it. */
  direction: 'reads' | 'writes';
  /** Abbreviated type (structured shapes collapse to `Base[N fields]`); '' when
   *  the process declares no type for this port. */
  type: string;
  /** Full, unabbreviated type string; '' when none. Goes in a title / expanded row. */
  fullType: string;
  /** The port's physical unit (`_units` on the schema, e.g. "kelvin", "molar",
   *  "volt"); '' when the port declares none. Shown beside the type so a port
   *  reads as "float · molar", not a bare "float". */
  units: string;
  /** The store the port connects to, for display: the RESOLVED absolute dotted
   *  path when known (the composite root shown as `<root>`), else the raw
   *  authored target. '' when the port is unwired. */
  connectsTo: string;
  /** The raw authored wire target, joined verbatim (lossy — never re-split). */
  rawTarget: string;
}

export interface PortSchemas {
  /** port -> type string (from the process spec's _inputs/_outputs). */
  typeSchema?: Record<string, unknown>;
  /** port -> raw authored wire target, joined with '.'. */
  portsSchema?: Record<string, string>;
  /** port -> resolved absolute dotted store path ('' = composite root). */
  portsTarget?: Record<string, string>;
}

/** A port's declared type as a string. Handles the authored forms:
 *  - bare type string (how `outputs()` writes it): `"rna"`
 *  - schema dict (how `inputs()` writes it): `{_type: "rna", _default: []}`
 *  - a compound port with no top-level `_type` that maps a SUBTREE of stores
 *    (e.g. `unique → {active_ribosome, active_RNAP, active_replisome}` or
 *    `listeners → {rna_counts, monomer_counts, …}`). Such a port has no single
 *    leaf type, but it is not untyped — it is a tree. Report its immediate
 *    field names as `tree[a|b|c]`; `abbreviateType` collapses ≥2 fields to
 *    `tree[N fields]` for the caption while the popover keeps the full list. */
export function portType(v: unknown): string {
  if (typeof v === 'string') return v;
  if (v && typeof v === 'object') {
    const t = (v as { _type?: unknown })._type;
    if (typeof t === 'string') return t;
    const fields = Object.keys(v as object).filter((k) => !k.startsWith('_'));
    if (fields.length) return `tree[${fields.join('|')}]`;
  }
  return '';
}

/** A port's physical unit from its `_units` schema key, '' when absent. */
export function portUnits(v: unknown): string {
  if (v && typeof v === 'object') {
    const u = (v as { _units?: unknown })._units;
    if (typeof u === 'string') return u;
  }
  return '';
}

/** Fold a single port's type + wiring into its display shape. */
export function portInfo(port: string, isOutput: boolean, s: PortSchemas): PortInfo {
  const rawType = s.typeSchema ? portType(s.typeSchema[port]) : '';
  const units = s.typeSchema ? portUnits(s.typeSchema[port]) : '';
  const rawTarget = s.portsSchema?.[port] ?? '';
  const resolved = s.portsTarget?.[port];
  // The resolved absolute path is unambiguous, so prefer it; '' is the valid
  // composite-root path (falsy but meaningful), rendered as <root>. Fall back to
  // the raw authored target when no resolution is available.
  const connectsTo = resolved != null
    ? (resolved === '' ? '<root>' : resolved)
    : rawTarget;
  return {
    port,
    direction: isOutput ? 'writes' : 'reads',
    type: abbreviateType(rawType),
    fullType: rawType,
    units,
    connectsTo,
    rawTarget,
  };
}
