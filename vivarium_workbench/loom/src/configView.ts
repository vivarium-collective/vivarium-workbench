// src/configView.ts — shared config-parameter view model for a process, used by
// both the ProcessNode card (config band) and the Inspector (Config section).
// A process's `config` holds only EXPLICITLY-set values (usually {}); the
// declared `config_schema` carries every parameter's type + default. We merge
// them so a reader sees the full parameter surface with types, defaults, and
// which values the composite actually set.

/** A config value's inferred type, when there's no declared schema type — read
 *  the JS runtime shape. */
export function configValueType(v: unknown): string {
  if (v === null || v === undefined) return 'null';
  if (Array.isArray(v)) return `list[${v.length}]`;
  switch (typeof v) {
    case 'number':  return Number.isInteger(v) ? 'integer' : 'float';
    case 'boolean': return 'boolean';
    case 'string':  return 'string';
    case 'object':  return `dict[${Object.keys(v as object).length}]`;
    default:        return typeof v;
  }
}

/** A config value rendered for display (objects/lists as compact JSON). */
export function fmtConfigValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'object') { try { return JSON.stringify(v); } catch { return String(v); } }
  return String(v);
}

/** A scalar (single-value) config type worth showing inline in the card band.
 *  The ParCa-derived arrays/maps/matrices/functions are noise kept to the
 *  expandable detail, not the always-on band. */
export function isScalarConfigType(type: string): boolean {
  return /^(integer|float|number|boolean|string)$/.test(type) || /^quantity\[/.test(type);
}

export interface ConfigParam {
  name: string; type: string; value: string; set: boolean; scalar: boolean;
}

/** The process's config parameters: every declared parameter (from
 *  `config_schema`) with its type and default, overlaid with any value the
 *  composite EXPLICITLY set. Set parameters sort first, then alphabetically. */
export function configParams(
  schema: Record<string, unknown> | undefined,
  config: Record<string, unknown> | undefined,
): ConfigParam[] {
  const out: ConfigParam[] = [];
  const seen = new Set<string>();
  const typeOf = (decl: unknown): string =>
    typeof decl === 'string' ? decl
      : (decl && typeof decl === 'object' && typeof (decl as { _type?: unknown })._type === 'string')
        ? String((decl as { _type: string })._type) : '';
  const defOf = (decl: unknown): unknown =>
    (decl && typeof decl === 'object' && '_default' in (decl as object))
      ? (decl as { _default?: unknown })._default : undefined;

  for (const [name, decl] of Object.entries(schema ?? {})) {
    seen.add(name);
    const type = typeOf(decl);
    const set = !!config && Object.prototype.hasOwnProperty.call(config, name);
    const raw = set ? config![name] : defOf(decl);
    out.push({ name, type, value: fmtConfigValue(raw), set, scalar: isScalarConfigType(type) });
  }
  for (const [name, v] of Object.entries(config ?? {})) {
    if (seen.has(name)) continue;
    if (name.startsWith('_')) continue;  // internal metadata (e.g. _inner_view) — not a param
    out.push({ name, type: configValueType(v), value: fmtConfigValue(v), set: true, scalar: true });
  }
  // Salient (explicitly-set, non-default) params lead, then scalars, then name —
  // so a truncated band keeps the parameters the reader most needs.
  return out.sort((a, b) =>
    (Number(b.set) - Number(a.set)) || (Number(b.scalar) - Number(a.scalar)) || a.name.localeCompare(b.name));
}
