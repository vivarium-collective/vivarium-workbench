// src/panels/VisualizationsPanel.tsx — rendered Visualization step output
// from the most recent run. Each entry is the HTML produced by one viz step
// (Plotly + inline JS); we drop it into an iframe with `srcDoc` so its
// <script> blocks execute and don't leak into the bigraph-loom document.
import { zipSync, strToU8 } from 'fflate';

type VizPayload = string | { html: string };

/** One visualization the composite generator DECLARES it will produce
 *  (from the resolved composite's `visualizations`), shown as an expected card
 *  before the run renders it. */
export type DeclaredViz = { name?: string; config?: { title?: string } | null };

export interface VisualizationsPanelProps {
  vizHtml: Record<string, VizPayload> | null;
  hasRun: boolean;
  readOnly?: boolean;
  /** Composite name/id — used to name the downloaded .zip. */
  baseName?: string;
  /** Visualizations the composite declares up front — rendered as expected
   *  cards (pending → rendering → rendered) even before any output exists. */
  declared?: DeclaredViz[] | null;
  /** Current run phase (backend `/status.phase`), for the status badge. */
  phase?: string | null;
  /** Whether a run is live right now. */
  isRunning?: boolean;
}

function _payloadHtml(p: VizPayload): string {
  return typeof p === 'string' ? p : (p?.html || '');
}

function _declTitle(d: DeclaredViz): string {
  return (d.config?.title || d.name || 'visualization');
}

/** Match a declared viz to a rendered output key (keys are paths/names that
 *  usually contain the declared name). Returns the rendered key or null. */
function _matchRendered(d: DeclaredViz, renderedKeys: string[]): string | null {
  const name = (d.name || '').toLowerCase();
  const title = (d.config?.title || '').toLowerCase();
  for (const k of renderedKeys) {
    const kl = k.toLowerCase();
    if (name && kl.includes(name)) return k;
    if (title && kl.includes(title)) return k;
  }
  return null;
}

const _BADGE: Record<string, { bg: string; fg: string; label: string }> = {
  rendered:  { bg: '#f0fdf4', fg: '#166534', label: '● rendered' },
  rendering: { bg: '#eff6ff', fg: '#1d4ed8', label: '◐ rendering…' },
  pending:   { bg: '#fffbeb', fg: '#b45309', label: '○ pending — renders after the run' },
  missing:   { bg: '#f9fafb', fg: '#6b7280', label: '– not produced by this run' },
};

function _StatusBadge({ status }: { status: keyof typeof _BADGE }) {
  const b = _BADGE[status];
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999,
      background: b.bg, color: b.fg, whiteSpace: 'nowrap',
    }}>{b.label}</span>
  );
}

/** Zip every rendered visualization (one self-contained .html each) and hand it
 *  to the browser as a single download. Uses fflate (already vendored) so it is
 *  purely client-side — no run-data round-trip to the server. */
function downloadVizZip(vizHtml: Record<string, VizPayload>, baseName?: string): void {
  const files: Record<string, Uint8Array> = {};
  const seen = new Set<string>();
  for (const [path, payload] of Object.entries(vizHtml)) {
    let name = (path.replace(/[^a-zA-Z0-9._-]/g, '_') || 'viz');
    if (!name.toLowerCase().endsWith('.html')) name += '.html';
    let unique = name;
    let i = 2;
    while (seen.has(unique)) { unique = name.replace(/\.html$/i, `_${i}.html`); i++; }
    seen.add(unique);
    files[unique] = strToU8(_payloadHtml(payload));
  }
  const zipped = zipSync(files, { level: 6 });
  const blob = new Blob([zipped as BlobPart], { type: 'application/zip' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${(baseName || 'composite').replace(/[^a-zA-Z0-9._-]/g, '_')}-visualizations.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function _VizIframe({ label, html }: { label: string; html: string }) {
  return (
    <div style={{ marginBottom: 16, border: '1px solid #e5e7eb', borderRadius: 4 }}>
      <div style={{ padding: '6px 10px', background: '#f3f4f6', fontFamily: 'monospace', fontSize: 12 }}>
        {label}
      </div>
      <iframe
        srcDoc={html || '<p style="font-family:system-ui;color:#888;padding:12px">No HTML</p>'}
        style={{ width: '100%', height: '70vh', minHeight: 400, border: 0 }}
        sandbox="allow-scripts"
        title={`viz-${label}`}
      />
    </div>
  );
}

function _ExpectedCard({ title, status }: { title: string; status: keyof typeof _BADGE }) {
  return (
    <div style={{
      marginBottom: 12, border: '1px dashed #d1d5db', borderRadius: 6,
      padding: '12px 14px', display: 'flex', alignItems: 'center',
      justifyContent: 'space-between', gap: 12, background: '#fcfcfd',
    }}>
      <span style={{ fontSize: 14, color: '#374151' }}>{title}</span>
      <_StatusBadge status={status} />
    </div>
  );
}

export function VisualizationsPanel(
  { vizHtml, hasRun, readOnly, baseName, declared, phase, isRunning }: VisualizationsPanelProps,
) {
  const wrap: React.CSSProperties = { padding: 16, fontFamily: 'system-ui, sans-serif' };
  const declaredList = declared ?? [];
  const rendered = vizHtml ?? {};
  const renderedKeys = Object.keys(rendered);
  const renderingPhase = !!phase && /render|visualiz/i.test(phase);

  // Nothing declared AND nothing rendered → the original guidance messages.
  if (declaredList.length === 0 && renderedKeys.length === 0) {
    return (
      <div style={wrap}>
        <h3 style={{ marginTop: 0 }}>Visualizations</h3>
        <p style={{ color: '#6b7280' }}>
          {readOnly
            ? 'The read-only mirror does not include run data — run this composite in a live dashboard to see visualizations.'
            : vizHtml
              ? 'Run complete — no visualizations declared by this composite.'
              : hasRun ? 'Loading visualizations…' : 'No run yet — press ▶ Run above.'}
        </p>
      </div>
    );
  }

  // Which rendered outputs are claimed by a declared viz (the rest show as extras).
  const claimed = new Set<string>();
  const cards = declaredList.map((d, i) => {
    const key = _matchRendered(d, renderedKeys);
    if (key) claimed.add(key);
    const status: keyof typeof _BADGE = key
      ? 'rendered'
      : isRunning
        ? (renderingPhase ? 'rendering' : 'pending')
        : (vizHtml ? 'missing' : 'pending');
    return { title: _declTitle(d), key, status, id: `decl-${i}` };
  });
  const extras = renderedKeys.filter(k => !claimed.has(k));

  return (
    <div style={wrap}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        gap: 12, marginBottom: 10,
      }}>
        <h3 style={{ margin: 0 }}>
          Visualizations{declaredList.length ? ` · ${declaredList.length} expected` : ''}
        </h3>
        {renderedKeys.length > 0 && (
          <button
            type="button"
            onClick={() => downloadVizZip(rendered, baseName)}
            title={`Download all ${renderedKeys.length} rendered visualization${renderedKeys.length === 1 ? '' : 's'} as a .zip`}
            style={{
              fontSize: 13, fontWeight: 600, padding: '4px 11px', cursor: 'pointer',
              color: '#0d6e6b', background: '#fff',
              border: '1px solid #0d6e6b', borderRadius: 6, whiteSpace: 'nowrap',
            }}
          >
            ↓ Visualizations
          </button>
        )}
      </div>
      {declaredList.length > 0 && (
        <p style={{ color: '#6b7280', fontSize: 12, margin: '0 0 12px' }}>
          Declared by the composite generator{isRunning ? ' — rendering as the run produces data.' : '.'}
        </p>
      )}
      {/* Declared visualizations: rendered inline once available, else an expected card. */}
      {cards.map(c => (
        c.key
          ? <_VizIframe key={c.id} label={c.title} html={_payloadHtml(rendered[c.key])} />
          : <_ExpectedCard key={c.id} title={c.title} status={c.status} />
      ))}
      {/* Rendered outputs not matched to a declared viz. */}
      {extras.map(k => <_VizIframe key={`x-${k}`} label={k} html={_payloadHtml(rendered[k])} />)}
    </div>
  );
}
