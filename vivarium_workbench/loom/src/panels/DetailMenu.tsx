// src/panels/DetailMenu.tsx — toolbar "Detail" dropdown.
//
// Per-feature card-detail toggles, replacing the old single-tier <select>. Each
// feature is Auto (follow the zoom-driven tier) or forced independently:
//   Ports    — None / Ports / +Types        (process input/output ports)
//   Stores   — Name / +Value / +Type         (how much of a store shows)
//   Config   — Show / Hide                    (process config band)
//   Contract — Show / Hide / Full             (Full = the extended description)
// They layer on top of the zoom tier in the nodes (see ProcessNode/StoreNode).
import { useEffect, useRef, useState } from 'react';
import { DETAIL_AUTO } from '../App';
import type { DetailOverrides, PortsDetail, StoresDetail, TriDetail, ContractDetail } from '../App';

const PORTS: { id: PortsDetail; label: string }[] = [
  { id: 'auto', label: 'Auto' }, { id: 'none', label: 'None' },
  { id: 'plain', label: 'Ports' }, { id: 'types', label: '+Types' },
];
const STORES: { id: StoresDetail; label: string }[] = [
  { id: 'auto', label: 'Auto' }, { id: 'name', label: 'Name' },
  { id: 'value', label: '+Value' }, { id: 'type', label: '+Type' },
];
const TRI: { id: TriDetail; label: string }[] = [
  { id: 'auto', label: 'Auto' }, { id: 'on', label: 'Show' }, { id: 'off', label: 'Hide' },
];
const CONTRACT: { id: ContractDetail; label: string }[] = [
  { id: 'auto', label: 'Auto' }, { id: 'on', label: 'Show' },
  { id: 'off', label: 'Hide' }, { id: 'full', label: 'Full' },
];

export default function DetailMenu(props: {
  overrides: DetailOverrides;
  setOverrides: (o: DetailOverrides) => void;
  fontScale: number;
  bumpFont: (d: number) => void;
  setFontScale: (v: number) => void;
}) {
  const { overrides, setOverrides, fontScale, bumpFont, setFontScale } = props;
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const anyForced = overrides.ports !== 'auto' || overrides.stores !== 'auto'
    || overrides.config !== 'auto' || overrides.contract !== 'auto' || overrides.figures !== 'auto'
    || overrides.address !== 'auto';

  const seg = (opts: { id: string; label: string }[], value: string, onPick: (id: string) => void) => (
    <div style={{ display: 'inline-flex', border: '1px solid #d1d5db', borderRadius: 5, overflow: 'hidden' }}>
      {opts.map((o, i) => (
        <button
          key={o.id}
          onClick={() => onPick(o.id)}
          style={{
            padding: '3px 8px', fontSize: 11, border: 0, cursor: 'pointer', whiteSpace: 'nowrap',
            borderLeft: i === 0 ? 0 : '1px solid #e5e7eb',
            background: value === o.id ? '#0d6e6b' : '#fff',
            color: value === o.id ? '#fff' : '#374151',
            fontWeight: value === o.id ? 600 : 400,
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );

  const rowLabel: React.CSSProperties = { fontSize: 11.5, color: '#374151', width: 58, flexShrink: 0 };
  const row: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 10, padding: '5px 12px' };

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen((v) => !v)}
        title="Card detail — toggle ports / stores / config / contract independently, or follow the zoom (Auto)"
        style={{
          height: 28, padding: '0 10px', fontSize: 12, background: (open || anyForced) ? '#ecfdf9' : '#fff',
          display: 'inline-flex', alignItems: 'center', gap: 5,
          border: '1px solid #d1d5db', borderRadius: 4, cursor: 'pointer',
          color: (open || anyForced) ? '#0d6e6b' : '#374151', fontWeight: anyForced ? 600 : 400,
        }}
      >
        Detail ▾
      </button>

      {open && (
        <div style={{
          // Anchor to the RIGHT edge of the button so the (wide) segmented rows
          // open leftward and never clip off the window.
          position: 'absolute', top: '100%', right: 0, marginTop: 4,
          background: '#fff', border: '1px solid #d1d5db', borderRadius: 4,
          boxShadow: '0 2px 10px rgba(0,0,0,0.14)', overflow: 'hidden', zIndex: 20, minWidth: 292,
        }}>
          <div style={{ padding: '6px 12px 2px', fontSize: 10.5, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
            Card detail — Auto follows zoom
          </div>
          <div style={row}><span style={rowLabel}>Ports</span>{seg(PORTS, overrides.ports, (id) => setOverrides({ ...overrides, ports: id as PortsDetail }))}</div>
          <div style={row}><span style={rowLabel}>Stores</span>{seg(STORES, overrides.stores, (id) => setOverrides({ ...overrides, stores: id as StoresDetail }))}</div>
          <div style={row}><span style={rowLabel}>Config</span>{seg(TRI, overrides.config, (id) => setOverrides({ ...overrides, config: id as TriDetail }))}</div>
          <div style={row}><span style={rowLabel}>Contract</span>{seg(CONTRACT, overrides.contract, (id) => setOverrides({ ...overrides, contract: id as ContractDetail }))}</div>
          <div style={row}><span style={rowLabel}>Figures</span>{seg(TRI, overrides.figures, (id) => setOverrides({ ...overrides, figures: id as TriDetail }))}</div>
          <div style={row}><span style={rowLabel}>Address</span>{seg(TRI, overrides.address, (id) => setOverrides({ ...overrides, address: id as TriDetail }))}</div>
          <div style={{ height: 1, background: '#e5e7eb', margin: '4px 0' }} />
          <div style={row}>
            <span style={rowLabel}>Text size</span>
            <div style={{ display: 'inline-flex', border: '1px solid #d1d5db', borderRadius: 5, overflow: 'hidden' }}>
              <button onClick={() => bumpFont(-0.1)} title="Smaller text"
                style={{ padding: '3px 9px', fontSize: 12, fontWeight: 700, border: 0, borderRight: '1px solid #e5e7eb', background: '#fff', color: '#374151', cursor: 'pointer' }}>A−</button>
              <button onClick={() => setFontScale(1)} title="Reset text size"
                style={{ padding: '3px 9px', fontSize: 11, minWidth: 46, border: 0, background: '#fff', color: fontScale !== 1 ? '#0d6e6b' : '#6b7280', fontWeight: fontScale !== 1 ? 700 : 400, cursor: 'pointer' }}>{fontScale.toFixed(2)}×</button>
              <button onClick={() => bumpFont(0.1)} title="Larger text"
                style={{ padding: '3px 9px', fontSize: 15, fontWeight: 700, border: 0, borderLeft: '1px solid #e5e7eb', background: '#fff', color: '#374151', cursor: 'pointer' }}>A+</button>
            </div>
          </div>
          <div style={{ height: 1, background: '#e5e7eb', margin: '4px 0' }} />
          <button
            onClick={() => setOverrides(DETAIL_AUTO)}
            disabled={!anyForced}
            style={{
              display: 'flex', alignItems: 'center', gap: 8, width: '100%', textAlign: 'left',
              padding: '7px 12px', fontSize: 12, border: 0, background: '#fff',
              cursor: anyForced ? 'pointer' : 'default', color: anyForced ? '#374151' : '#b8bec9',
            }}
            onMouseEnter={(e) => { if (anyForced) e.currentTarget.style.background = '#f3f4f6'; }}
            onMouseLeave={(e) => (e.currentTarget.style.background = '#fff')}
          >
            <span style={{ width: 16, textAlign: 'center' }}>↺</span> Reset to Auto
          </button>
        </div>
      )}
    </div>
  );
}
