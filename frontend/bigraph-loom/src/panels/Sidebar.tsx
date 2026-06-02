import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import type { ExploreInspectMsg } from '../api';
import { clampSidebarWidth } from './filterHidden';

type Selection = Omit<ExploreInspectMsg, 'type'> | null;
type SidebarTab = 'inspector' | 'processes' | 'nodes';

export interface SidebarProps {
  selection: Selection;
  /** ALL React Flow nodes (pre-visibility-filter) so hidden items can be re-shown. */
  nodes: any[];
  hidden: Set<string>;
  onToggleHidden: (id: string) => void;
  onShowAll: (kind: 'process' | 'store') => void;
  emitSet: Set<string>;
  onEmitToggle: (path: string[], emit: boolean) => void;
}

// --- emit helpers (ported from InspectorPanel) ---------------------------

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

// --- localStorage helpers -------------------------------------------------

const WIDTH_KEY = 'loom.sidebar.width';
const COLLAPSED_KEY = 'loom.sidebar.collapsed';
const TAB_KEY = 'loom.sidebar.tab';

// localStorage can throw (disabled cookies, quota, restricted test env), so all
// access is wrapped — persistence is a nice-to-have, never a hard failure.
function lsGet(key: string): string | null {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function lsSet(key: string, value: string): void {
  try { window.localStorage.setItem(key, value); } catch { /* ignore */ }
}

function readWidth(): number {
  const raw = lsGet(WIDTH_KEY);
  const n = raw ? Number(raw) : NaN;
  return Number.isFinite(n) ? clampSidebarWidth(n) : 320;
}
function readCollapsed(): boolean {
  return lsGet(COLLAPSED_KEY) === 'true';
}
function readTab(): SidebarTab {
  const raw = lsGet(TAB_KEY);
  return raw === 'inspector' || raw === 'processes' || raw === 'nodes' ? raw : 'inspector';
}

export function Sidebar(props: SidebarProps) {
  const [width, setWidth] = useState<number>(() => readWidth());
  const [collapsed, setCollapsed] = useState<boolean>(() => readCollapsed());
  const [tab, setTab] = useState<SidebarTab>(() => readTab());

  useEffect(() => { lsSet(WIDTH_KEY, String(width)); }, [width]);
  useEffect(() => { lsSet(COLLAPSED_KEY, String(collapsed)); }, [collapsed]);
  useEffect(() => { lsSet(TAB_KEY, tab); }, [tab]);

  // --- drag-to-resize (ported from bigraph-viz2 index.ts:91-107) ----------
  const draggingRef = useRef(false);
  const startXRef = useRef(0);
  const startWRef = useRef(0);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!draggingRef.current) return;
      // Handle sits at the panel's LEFT edge: dragging left widens it.
      const w = clampSidebarWidth(startWRef.current - (e.clientX - startXRef.current));
      setWidth(w);
    };
    const onUp = () => {
      if (draggingRef.current) {
        draggingRef.current = false;
        document.body.style.cursor = '';
      }
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, []);

  const onHandleDown = (e: React.MouseEvent) => {
    draggingRef.current = true;
    startXRef.current = e.clientX;
    startWRef.current = width;
    document.body.style.cursor = 'col-resize';
    e.preventDefault();
  };

  // --- collapsed rail -----------------------------------------------------
  if (collapsed) {
    return (
      <div style={{
        flex: '0 0 28px', width: 28,
        borderLeft: '1px solid #e5e7eb', background: '#fff',
        display: 'flex', justifyContent: 'center', alignItems: 'flex-start',
        paddingTop: 6,
      }}>
        <button
          onClick={() => setCollapsed(false)}
          title="Show sidebar"
          style={railBtnStyle}
        >
          ‹
        </button>
      </div>
    );
  }

  const tabs: SidebarTab[] = ['inspector', 'processes', 'nodes'];

  return (
    <div style={{
      position: 'relative',
      flex: `0 0 ${width}px`, width,
      borderLeft: '1px solid #e5e7eb', background: '#fff',
      display: 'flex', flexDirection: 'column',
      fontFamily: 'system-ui, sans-serif',
    }}>
      {/* left-edge resize handle */}
      <div
        onMouseDown={onHandleDown}
        style={{
          position: 'absolute', left: -3, top: 0, bottom: 0, width: 6,
          cursor: 'col-resize', zIndex: 5,
        }}
      />
      {/* header: tabs + collapse caret */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 4,
        padding: '4px 6px', borderBottom: '1px solid #e5e7eb',
        flex: '0 0 auto',
      }}>
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              background: 'transparent', border: 0,
              padding: '4px 6px', fontSize: 12, cursor: 'pointer',
              textTransform: 'capitalize',
              borderBottom: '2px solid ' + (tab === t ? '#2563eb' : 'transparent'),
              color: tab === t ? '#2563eb' : '#6b7280',
              fontWeight: tab === t ? 600 : 400,
            }}
          >
            {t}
          </button>
        ))}
        <button
          onClick={() => setCollapsed(true)}
          title="Hide sidebar"
          style={{ ...railBtnStyle, marginLeft: 'auto' }}
        >
          ›
        </button>
      </div>

      {/* body */}
      <div style={{ flex: 1, overflow: 'auto', padding: 10 }}>
        {tab === 'inspector' && (
          <InspectorTab
            selection={props.selection}
            emitSet={props.emitSet}
            onEmitToggle={props.onEmitToggle}
          />
        )}
        {tab === 'processes' && (
          <ToggleListTab
            kind="process"
            nodes={props.nodes}
            hidden={props.hidden}
            onToggleHidden={props.onToggleHidden}
            onShowAll={props.onShowAll}
          />
        )}
        {tab === 'nodes' && (
          <ToggleListTab
            kind="store"
            nodes={props.nodes}
            hidden={props.hidden}
            onToggleHidden={props.onToggleHidden}
            onShowAll={props.onShowAll}
          />
        )}
      </div>
    </div>
  );
}

const railBtnStyle: React.CSSProperties = {
  background: 'transparent', border: 0,
  fontSize: 16, lineHeight: 1, cursor: 'pointer', color: '#6b7280',
  padding: '2px 4px',
};

// --- Inspector tab --------------------------------------------------------

function InspectorTab(props: {
  selection: Selection;
  emitSet: Set<string>;
  onEmitToggle: (path: string[], emit: boolean) => void;
}) {
  const sel = props.selection;
  if (!sel) {
    return <p style={{ color: '#888', fontSize: 12 }}>Click a node to inspect.</p>;
  }

  const isStore = sel.kind === 'store';
  const explicit = isStore && isExplicitEmit(sel.path, props.emitSet);
  const inheritedFrom = isStore ? findInheritedFrom(sel.path, props.emitSet) : null;
  const description = (sel.details as { description?: unknown })?.description;
  const hasDescription = typeof description === 'string' && description.trim().length > 0;

  return (
    <div>
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
                onChange={(ev) => props.onEmitToggle(sel.path, ev.target.checked)}
              />
              <strong>Emit this store</strong>
              <span style={{ color: '#6b7280' }}>(includes descendants)</span>
            </label>
          )}
        </div>
      )}

      {hasDescription && (
        <div style={{ margin: '8px 0' }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#374151', marginBottom: 4 }}>
            Description
          </div>
          <pre style={{
            fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            background: '#f7f7f7', padding: 6, margin: 0, borderRadius: 4,
            fontFamily: 'system-ui, sans-serif', color: '#374151',
          }}>
            {description as string}
          </pre>
        </div>
      )}

      <pre style={{
        fontSize: 11, background: '#f7f7f7', padding: 6,
        overflow: 'auto', maxHeight: 320, margin: 0,
      }}>
        {JSON.stringify(sel.details, null, 2)}
      </pre>
    </div>
  );
}

// --- Processes / Nodes toggle list ---------------------------------------

function ToggleListTab(props: {
  kind: 'process' | 'store';
  nodes: any[];
  hidden: Set<string>;
  onToggleHidden: (id: string) => void;
  onShowAll: (kind: 'process' | 'store') => void;
}) {
  const items = props.nodes.filter((n) => n.type === props.kind);

  return (
    <div>
      <div style={{ marginBottom: 8 }}>
        <button
          onClick={() => props.onShowAll(props.kind)}
          style={{
            background: 'transparent', border: 0, padding: 0,
            color: '#2563eb', fontSize: 12, cursor: 'pointer',
            textDecoration: 'underline',
          }}
        >
          Show all
        </button>
      </div>
      {items.length === 0 && (
        <p style={{ color: '#888', fontSize: 12 }}>None.</p>
      )}
      {items.map((n) => {
        const visible = !props.hidden.has(n.id);
        const sub = props.kind === 'process'
          ? (n.data?.address || (n.data?.path ?? []).join('.'))
          : (n.data?.path ?? []).join('.');
        return (
          <label
            key={n.id}
            style={{
              display: 'flex', alignItems: 'flex-start', gap: 6,
              padding: '3px 0', cursor: 'pointer', fontSize: 12,
            }}
          >
            <input
              type="checkbox"
              checked={visible}
              onChange={() => props.onToggleHidden(n.id)}
              style={{ marginTop: 2 }}
            />
            <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
              <span style={{ color: '#111827' }}>{n.data?.label ?? n.id}</span>
              {sub && (
                <span style={{
                  fontFamily: 'monospace', fontSize: 11, color: '#9ca3af',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {sub}
                </span>
              )}
            </span>
          </label>
        );
      })}
    </div>
  );
}
