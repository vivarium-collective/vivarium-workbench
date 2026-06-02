import type React from 'react';
import type { ExploreInspectMsg } from '../api';

export interface InspectorPanelProps {
  selection: Omit<ExploreInspectMsg, 'type'> | null;
  /** Set of explicit-emit paths (joined by '/'). Only stores can be emitted. */
  emitSet?: Set<string>;
  onEmitToggle?: (path: string[], emit: boolean) => void;
}

function isExplicitEmit(path: string[], explicit: Set<string>): boolean {
  return explicit.has(path.join('/'));
}

function findInheritedFrom(path: string[], explicit: Set<string>): string | null {
  for (let i = 0; i < path.length - 1; i++) {
    const prefix = path.slice(0, i + 1).join('/');
    if (explicit.has(prefix)) return prefix;
  }
  return null;
}

export function InspectorPanel(props: InspectorPanelProps) {
  const sel = props.selection;
  const emitSet = props.emitSet ?? new Set<string>();
  const panelStyle: React.CSSProperties = {
    position: 'absolute',
    top: 8,
    right: 8,
    width: 280,
    background: '#fff',
    border: '1px solid #ddd',
    borderRadius: 4,
    padding: 10,
    boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
    zIndex: 10,
    fontFamily: 'system-ui, sans-serif',
  };

  if (!sel) {
    return (
      <div style={panelStyle}>
        <h4 style={{ margin: 0, fontSize: 14 }}>Inspector</h4>
        <p style={{ color: '#888', fontSize: 12 }}>Click a node to inspect.</p>
      </div>
    );
  }

  const isStore = sel.kind === 'store';
  const explicit = isStore && isExplicitEmit(sel.path, emitSet);
  const inheritedFrom = isStore ? findInheritedFrom(sel.path, emitSet) : null;

  return (
    <div style={panelStyle}>
      <h4 style={{ margin: 0, fontSize: 14, textTransform: 'capitalize' }}>{sel.kind}</h4>
      <p style={{ fontFamily: 'monospace', fontSize: 12, margin: '4px 0' }}>
        {sel.path.length ? sel.path.join('.') : '<root>'}
      </p>

      {isStore && (
        <div style={{
          margin: '8px 0', padding: '8px 10px',
          background: explicit ? '#dcfce7' : inheritedFrom ? '#f3f4f6' : '#fafafa',
          border: '1px solid ' + (explicit ? '#86efac' : '#e5e7eb'),
          borderRadius: 4, fontSize: 12,
        }}>
          {inheritedFrom ? (
            <span>
              <strong>Emit:</strong> inherited from{' '}
              <code style={{ background: '#fff', padding: '1px 4px', borderRadius: 2 }}>
                {inheritedFrom.split('/').join('.')}
              </code>
            </span>
          ) : (
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={explicit}
                onChange={(ev) => props.onEmitToggle?.(sel.path, ev.target.checked)}
              />
              <strong>Emit this store</strong>
              <span style={{ color: '#6b7280' }}>(includes descendants)</span>
            </label>
          )}
        </div>
      )}

      <pre style={{
        fontSize: 11, background: '#f7f7f7', padding: 6,
        overflow: 'auto', maxHeight: 220, margin: 0,
      }}>
        {JSON.stringify(sel.details, null, 2)}
      </pre>
    </div>
  );
}
