// src/panels/VisualizationsPanel.tsx — rendered Visualization step output
// from the most recent run. Each entry is the HTML produced by one viz step
// (Plotly + inline JS); we drop it into an iframe with `srcDoc` so its
// <script> blocks execute and don't leak into the bigraph-loom document.
type VizPayload = string | { html: string };

export interface VisualizationsPanelProps {
  vizHtml: Record<string, VizPayload> | null;
  hasRun: boolean;
  readOnly?: boolean;
}

function _payloadHtml(p: VizPayload): string {
  return typeof p === 'string' ? p : (p?.html || '');
}

export function VisualizationsPanel({ vizHtml, hasRun, readOnly }: VisualizationsPanelProps) {
  const wrap: React.CSSProperties = { padding: 16, fontFamily: 'system-ui, sans-serif' };

  if (!vizHtml) {
    return (
      <div style={wrap}>
        <h3 style={{ marginTop: 0 }}>Visualizations</h3>
        <p style={{ color: '#6b7280' }}>
          {readOnly
            ? 'The read-only mirror does not include run data — run this composite in a live dashboard to see visualizations.'
            : hasRun ? 'Loading visualizations…' : 'No run yet. Go to the Run tab to start one.'}
        </p>
      </div>
    );
  }

  const entries = Object.entries(vizHtml);
  if (entries.length === 0) {
    return (
      <div style={wrap}>
        <h3 style={{ marginTop: 0 }}>Visualizations</h3>
        <p style={{ color: '#6b7280' }}>
          Run complete — no visualizations declared by this composite.
        </p>
      </div>
    );
  }

  return (
    <div style={wrap}>
      <h3 style={{ marginTop: 0 }}>Visualizations</h3>
      {entries.map(([path, payload]) => {
        const html = _payloadHtml(payload);
        return (
          <div key={path} style={{
            marginBottom: 16,
            border: '1px solid #e5e7eb', borderRadius: 4,
          }}>
            <div style={{
              padding: '6px 10px', background: '#f3f4f6',
              fontFamily: 'monospace', fontSize: 12,
            }}>
              {path}
            </div>
            <iframe
              srcDoc={html || '<p style="font-family:system-ui;color:#888;padding:12px">No HTML</p>'}
              style={{ width: '100%', height: '70vh', minHeight: 400, border: 0 }}
              sandbox="allow-scripts"
              title={`viz-${path}`}
            />
          </div>
        );
      })}
    </div>
  );
}
