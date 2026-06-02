import { useState } from 'react';

/** Heuristic: how should a collection node display by default?
 * Top-level dicts expand. Long arrays start collapsed. Deeply nested
 * arrays-of-arrays (numpy-array-style) start collapsed to keep the view light.
 */
function _defaultOpen(value: unknown, depth: number): boolean {
  if (depth === 0) return true;
  if (Array.isArray(value)) return value.length <= 6 && depth < 2;
  if (value && typeof value === 'object') return depth < 1;
  return false;
}

function _previewLeaf(v: unknown): string {
  if (typeof v === 'string') return JSON.stringify(v);
  if (typeof v === 'number' || typeof v === 'boolean' || v == null) return String(v);
  return String(v);
}

/** Cheap shape hint, e.g. "list[5]" or "list[3 × 4]" for nested arrays. */
function _arrayPreview(arr: any[]): string {
  if (arr.length === 0) return 'list[0]';
  const first = arr[0];
  if (Array.isArray(first)) {
    const rows = arr.length;
    const cols = first.length;
    const allSameLen = arr.every((r) => Array.isArray(r) && r.length === cols);
    if (allSameLen) return `list[${rows} × ${cols}]`;
    return `list[${rows}] (ragged)`;
  }
  return `list[${arr.length}]`;
}

export interface JsonNodeProps {
  k: string;
  value: unknown;
  depth: number;
  path: string;
}

/** Collapsible JSON node — click row to toggle. Used by Document tab + Run-tab
 * step viewer. Renders strings/numbers/booleans as colored leaf values; arrays
 * and objects show a shape summary that expands into child nodes on click. */
export function JsonNode({ k, value, depth, path }: JsonNodeProps) {
  const isCollection = (value !== null) && (typeof value === 'object');
  const [open, setOpen] = useState(_defaultOpen(value, depth));

  const indent = depth * 14;
  const rowStyle: React.CSSProperties = {
    paddingLeft: indent,
    fontFamily: 'ui-monospace, Menlo, monospace',
    fontSize: 12.5,
    lineHeight: 1.45,
  };

  if (!isCollection) {
    return (
      <div style={rowStyle}>
        <span style={{ color: '#7c3aed' }}>{k}</span>
        <span style={{ color: '#6b7280' }}>: </span>
        <span style={
          typeof value === 'string' ? { color: '#059669' }
          : typeof value === 'number' ? { color: '#2563eb' }
          : typeof value === 'boolean' ? { color: '#d97706' }
          : { color: '#6b7280' }
        }>{_previewLeaf(value)}</span>
      </div>
    );
  }

  const isArray = Array.isArray(value);
  const entries: [string, unknown][] = isArray
    ? (value as any[]).map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, unknown>);

  const summary = isArray
    ? _arrayPreview(value as any[])
    : `{${entries.length} key${entries.length === 1 ? '' : 's'}}`;

  return (
    <div>
      <div
        style={{ ...rowStyle, cursor: 'pointer', userSelect: 'none' }}
        onClick={() => setOpen((o) => !o)}
      >
        <span style={{ color: '#6b7280', display: 'inline-block', width: 12 }}>
          {open ? '▾' : '▸'}
        </span>
        <span style={{ color: '#7c3aed' }}>{k}</span>
        <span style={{ color: '#6b7280' }}>: </span>
        <span style={{ color: '#9ca3af' }}>{summary}</span>
      </div>
      {open && (
        <div>
          {entries.map(([childK, childV]) => (
            <JsonNode
              key={path + '/' + childK}
              k={childK}
              value={childV}
              depth={depth + 1}
              path={path + '/' + childK}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** Render the root of a JSON value as a tree. Convenience wrapper that
 * iterates the top-level entries (without a parent key). */
export function JsonTree({ value }: { value: any }) {
  if (value === null || typeof value !== 'object') {
    return (
      <div style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12.5 }}>
        {_previewLeaf(value)}
      </div>
    );
  }
  const entries: [string, unknown][] = Array.isArray(value)
    ? (value as any[]).map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, unknown>);
  return (
    <div>
      {entries.map(([k, v]) => (
        <JsonNode key={k} k={k} value={v} depth={0} path={k} />
      ))}
    </div>
  );
}
