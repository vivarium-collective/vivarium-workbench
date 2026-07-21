// src/panels/ViewsMenu.tsx — toolbar control for saving / restoring named views.
//
// A "view" = the current arrangement + visibility (positions, collapsed, hidden).
// Saved views live in localStorage per composite (see viewStore). One view can
// be the DEFAULT, applied automatically when the composite opens. Views can also
// be shared as a compressed URL (?view=) or exported to a JSON file that a
// README can link to via ?viewUrl=.

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  listViews, getDefaultName, getView, saveView, deleteView, setDefault,
  shareableUrl, type View,
} from '../viewStore';

export default function ViewsMenu(props: {
  compositeId: string | null;
  captureCurrentView: () => View;
  applyView: (view: View) => void;
}) {
  const { compositeId, captureCurrentView, applyView } = props;
  const [open, setOpen] = useState(false);
  const [, force] = useState(0);          // bump to re-read localStorage after CRUD
  const [toast, setToast] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const refresh = () => force((n) => n + 1);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const flash = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 1800);
  };

  const names = compositeId ? listViews(compositeId) : [];
  const defaultName = compositeId ? getDefaultName(compositeId) : null;

  const onSave = useCallback(() => {
    if (!compositeId) return;
    const name = window.prompt('Save current view as:', '');
    if (!name || !name.trim()) return;
    saveView(compositeId, name.trim(), captureCurrentView());
    refresh();
    flash(`Saved "${name.trim()}"`);
  }, [compositeId, captureCurrentView]);

  const onApply = (name: string) => {
    if (!compositeId) return;
    const v = getView(compositeId, name);
    if (v) { applyView(v); setOpen(false); flash(`Applied "${name}"`); }
  };

  const onDelete = (name: string) => {
    if (!compositeId) return;
    deleteView(compositeId, name);
    refresh();
  };

  const onSetDefault = (name: string) => {
    if (!compositeId) return;
    setDefault(compositeId, name === defaultName ? null : name);
    refresh();
  };

  const onCopyLink = useCallback(async () => {
    const url = shareableUrl(captureCurrentView());
    try {
      await navigator.clipboard.writeText(url);
      flash('Shareable link copied');
    } catch {
      window.prompt('Copy this shareable link:', url);
    }
    setOpen(false);
  }, [captureCurrentView]);

  const onExportFile = useCallback(() => {
    const blob = new Blob([JSON.stringify(captureCurrentView(), null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${(compositeId || 'composite').replace(/[^\w.-]+/g, '_')}.view.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    setOpen(false);
  }, [captureCurrentView, compositeId]);

  const onLoadFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const v = JSON.parse(String(reader.result)) as View;
        applyView(v);
        flash(`Loaded ${file.name}`);
      } catch {
        flash('Invalid view file');
      }
    };
    reader.readAsText(file);
    setOpen(false);
  };

  const itemBtn: React.CSSProperties = {
    display: 'block', width: '100%', textAlign: 'left', padding: '6px 12px',
    fontSize: 12, border: 0, background: '#fff', cursor: 'pointer', color: '#374151',
  };
  const hover = {
    onMouseEnter: (e: React.MouseEvent<HTMLElement>) => (e.currentTarget.style.background = '#f3f4f6'),
    onMouseLeave: (e: React.MouseEvent<HTMLElement>) => (e.currentTarget.style.background = '#fff'),
  };

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((v) => !v)}
        title="Save / restore named views (arrangement + visibility)"
        style={{
          padding: '4px 10px', fontSize: 12, background: '#fff',
          border: '1px solid #d1d5db', borderRadius: 4, cursor: 'pointer', color: '#374151',
        }}
      >
        Views ▾
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: '100%', right: 0, marginTop: 4,
          background: '#fff', border: '1px solid #d1d5db', borderRadius: 4,
          boxShadow: '0 2px 10px rgba(0,0,0,0.14)', overflow: 'hidden',
          minWidth: 230, zIndex: 20,
        }}>
          {/* Saved views */}
          {names.length === 0 && (
            <div style={{ padding: '8px 12px', fontSize: 12, color: '#9ca3af' }}>
              No saved views yet.
            </div>
          )}
          {names.map((n) => (
            <div key={n} style={{
              display: 'flex', alignItems: 'center', gap: 4,
              borderBottom: '1px solid #f3f4f6',
            }}>
              <button
                onClick={() => onApply(n)}
                style={{ ...itemBtn, flex: 1, paddingRight: 4 }}
                {...hover}
                title={`Apply view "${n}"`}
              >
                {n}
              </button>
              <button
                onClick={() => onSetDefault(n)}
                title={n === defaultName ? 'Default view (opens on load) — click to unset' : 'Set as default (opens on load)'}
                style={{
                  border: 0, background: 'transparent', cursor: 'pointer',
                  fontSize: 13, padding: '0 2px',
                  color: n === defaultName ? '#f59e0b' : '#d1d5db',
                }}
              >
                {n === defaultName ? '★' : '☆'}
              </button>
              <button
                onClick={() => onDelete(n)}
                title={`Delete view "${n}"`}
                style={{ border: 0, background: 'transparent', cursor: 'pointer', fontSize: 13, padding: '0 8px 0 2px', color: '#9ca3af' }}
              >
                ×
              </button>
            </div>
          ))}

          <div style={{ height: 1, background: '#e5e7eb' }} />
          <button onClick={onSave} style={itemBtn} {...hover}>＋ Save current view…</button>
          <button onClick={onCopyLink} style={itemBtn} {...hover}>🔗 Copy shareable link</button>
          <button onClick={onExportFile} style={itemBtn} {...hover}>⭳ Export view file…</button>
          <button onClick={() => fileRef.current?.click()} style={itemBtn} {...hover}>⭱ Load view file…</button>
          <input
            ref={fileRef} type="file" accept="application/json,.json"
            style={{ display: 'none' }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) onLoadFile(f); e.currentTarget.value = ''; }}
          />
        </div>
      )}

      {toast && (
        <div style={{
          position: 'absolute', top: '100%', right: 0, marginTop: 4,
          background: '#111827', color: '#fff', fontSize: 11, padding: '4px 8px',
          borderRadius: 4, whiteSpace: 'nowrap', zIndex: 30, pointerEvents: 'none',
        }}>
          {toast}
        </div>
      )}
    </div>
  );
}
