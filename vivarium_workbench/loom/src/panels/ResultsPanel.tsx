// src/panels/ResultsPanel.tsx — emitter results from the most recent run.
//
// Reads a trajectory (one row per step) and groups it into per-observable
// time series; each row is expandable to scrub through the captured values.
import { useState } from 'react';
import { JsonTree } from './JsonNode';
import { runDownloadUrl } from '../api';

type TrajectoryRow = { step: number; time?: number; state: Record<string, unknown> };

export interface ResultsPanelProps {
  trajectory: TrajectoryRow[] | null;  // null = no run yet (or run in flight)
  hasRun: boolean;                     // a completed run exists
  runId?: string | null;
  downloadable?: boolean;
  readOnly?: boolean;
}

function _trajectoryToObservables(
  trajectory: TrajectoryRow[],
): Record<string, any[]> {
  const out: Record<string, any[]> = {};
  for (const row of trajectory) {
    for (const [k, v] of Object.entries(row.state || {})) {
      (out[k] ||= []).push(v);
    }
  }
  return out;
}

function ObservableRow({ name, entries }: { name: string; entries: any[] }) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(entries.length ? entries.length - 1 : 0);
  const total = entries.length;
  const current = (entries[step] || {}) as Record<string, unknown>;

  const visible: Record<string, unknown> = {};
  Object.entries(current).forEach(([k, v]) => {
    if (k === 'time' || k.startsWith('_')) return;
    visible[k] = v;
  });

  const previewKv = Object.entries(visible).slice(0, 1)[0];
  const previewStr = previewKv
    ? (() => {
        const v = previewKv[1];
        if (v === null || typeof v !== 'object') return String(v);
        if (Array.isArray(v)) return `list[${v.length}]`;
        return `{${Object.keys(v as object).length} keys}`;
      })()
    : '—';

  return (
    <>
      <tr style={{ borderBottom: '1px solid #f3f4f6', cursor: 'pointer' }}
          onClick={() => setOpen((o) => !o)}>
        <td style={{ padding: '6px 8px' }}>
          <span style={{ display: 'inline-block', width: 14, color: '#6b7280' }}>
            {open ? '▾' : '▸'}
          </span>
          <code>{name}</code>
        </td>
        <td style={{ padding: '6px 8px' }}>{total}</td>
        <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 12, color: '#4b5563' }}>
          {previewStr}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={3} style={{ background: '#fafafa', padding: 0 }}>
            <div style={{ padding: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, fontSize: 13 }}>
                <button onClick={() => setStep((s) => Math.max(0, s - 1))}
                        disabled={step === 0} style={{ padding: '2px 8px' }}>‹ Prev</button>
                <span style={{ color: '#374151' }}>
                  Step <strong>{step + 1}</strong> of {total}
                </span>
                <input type="range" min={0} max={Math.max(0, total - 1)} value={step}
                       onChange={(e) => setStep(parseInt(e.target.value, 10) || 0)}
                       style={{ flex: 1, maxWidth: 320 }} />
                <button onClick={() => setStep((s) => Math.min(total - 1, s + 1))}
                        disabled={step >= total - 1} style={{ padding: '2px 8px' }}>Next ›</button>
                {current.time !== undefined && (
                  <small style={{ color: '#6b7280' }}>time = {String(current.time)}</small>
                )}
              </div>
              <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 4,
                            padding: '8px 12px', maxHeight: 400, overflow: 'auto' }}>
                {Object.keys(visible).length === 0 ? (
                  <p style={{ color: '#9ca3af', fontSize: 13, margin: 0 }}>
                    No emitted fields at this step.
                  </p>
                ) : (
                  <JsonTree value={visible} />
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function ResultsPanel({ trajectory, hasRun, runId, downloadable, readOnly }: ResultsPanelProps) {
  const wrap: React.CSSProperties = { padding: 16, fontFamily: 'system-ui, sans-serif' };

  const downloadLink = downloadable && runId ? (
    <a
      href={runDownloadUrl(runId)}
      download
      style={{
        display: 'inline-block', margin: '4px 0 12px', padding: '6px 14px',
        fontSize: 13, fontWeight: 600, background: '#6366f1', color: '#fff',
        borderRadius: 6, textDecoration: 'none',
      }}
    >
      ⬇ Download results
    </a>
  ) : null;

  if (!trajectory) {
    return (
      <div style={wrap}>
        <h3 style={{ marginTop: 0 }}>Results</h3>
        {downloadLink}
        <p style={{ color: '#6b7280' }}>
          {readOnly
            ? 'The read-only mirror does not include run data — run this composite in a live dashboard to see results.'
            : hasRun ? 'Loading trajectory…' : 'No run yet. Go to the Run tab to start one.'}
        </p>
      </div>
    );
  }

  const observables = _trajectoryToObservables(trajectory);
  const keys = Object.keys(observables);

  return (
    <div style={wrap}>
      <h3 style={{ marginTop: 0 }}>Results</h3>
      {downloadLink}
      {keys.length === 0 ? (
        <p style={{ color: '#6b7280' }}>
          Run complete — no observables emitted. Toggle stores in the View
          tab to capture their values.
        </p>
      ) : (
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: '#f3f4f6' }}>
              <th style={{ textAlign: 'left', padding: '6px 8px' }}>Observable</th>
              <th style={{ textAlign: 'left', padding: '6px 8px', width: 80 }}>Steps</th>
              <th style={{ textAlign: 'left', padding: '6px 8px' }}>Latest preview</th>
            </tr>
          </thead>
          <tbody>
            {keys.sort().map((k) => (
              <ObservableRow key={k} name={k} entries={observables[k]} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
