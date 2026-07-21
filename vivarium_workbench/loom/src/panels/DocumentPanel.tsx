import { useState } from 'react';
import { JsonTree } from './JsonNode';

export interface DocumentPanelProps {
  state: any;
  compositeId?: string | null;
}

function _downloadFilename(id?: string | null): string {
  const slug = (id || 'composite')
    .replace(/[^a-zA-Z0-9_.-]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return `${slug || 'composite'}.json`;
}

export function DocumentPanel(props: DocumentPanelProps) {
  const [mode, setMode] = useState<'tree' | 'raw'>('tree');

  if (!props.state) {
    return <p style={{ padding: 16, color: '#888' }}>No composite loaded.</p>;
  }

  const json = JSON.stringify(props.state, null, 2);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(json);
    } catch {
      // Clipboard access can fail in sandboxed iframes; silently ignore.
    }
  }

  function handleDownload() {
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = _downloadFilename(props.compositeId);
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  const btnStyle: React.CSSProperties = {
    padding: '4px 10px', fontSize: 13,
    background: '#fff', border: '1px solid #d1d5db',
    borderRadius: 4, cursor: 'pointer',
  };
  const btnStylePrimary: React.CSSProperties = {
    ...btnStyle,
    background: '#2563eb', color: '#fff', border: '1px solid #2563eb',
  };
  const segStyle = (active: boolean): React.CSSProperties => ({
    padding: '4px 10px', fontSize: 13,
    background: active ? '#eff6ff' : '#fff',
    border: '1px solid ' + (active ? '#2563eb' : '#d1d5db'),
    borderRadius: 4, cursor: 'pointer',
    color: active ? '#1e40af' : '#1f2937',
    fontWeight: active ? 600 : 400,
  });

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, flex: 1 }}>Resolved document</h3>
        <div style={{ display: 'inline-flex', gap: 4 }}>
          <button onClick={() => setMode('tree')} style={segStyle(mode === 'tree')}>Tree</button>
          <button onClick={() => setMode('raw')}  style={segStyle(mode === 'raw')}>Raw</button>
        </div>
        <button onClick={handleCopy} style={btnStyle}>Copy</button>
        <button onClick={handleDownload} style={btnStylePrimary}>Download JSON ↓</button>
      </div>

      {mode === 'tree' ? (
        <div style={{
          background: '#fafafa',
          border: '1px solid #e5e7eb',
          borderRadius: 4,
          padding: '10px 14px',
          overflow: 'auto',
          maxHeight: 'calc(100vh - 140px)',
        }}>
          <JsonTree value={props.state} />
        </div>
      ) : (
        <pre style={{
          background: '#f8f8f8',
          padding: 12,
          borderRadius: 4,
          overflow: 'auto',
          fontSize: 12,
          maxHeight: 'calc(100vh - 140px)',
          margin: 0,
        }}>
          {json}
        </pre>
      )}
    </div>
  );
}
