import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ExploreInspectMsg } from '../api';
import { clampSidebarWidth, isHiddenByAncestor } from './filterHidden';

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

// --- store-node tree (for the hierarchical Nodes tab) --------------------
//
// Ported from bigraph-viz2's `buildNodeTree` / `renderNodes`. The loom's store
// node ids are `path.join('.')` (root is '<root>'); a node's parent is the
// store at `path.slice(0, -1)`. We build a tree from the FLAT list of store
// React Flow nodes, synthesizing intermediate group nodes for any path segment
// that has no explicit node so the nesting is always complete.

export interface NodeTreeNode {
  id: string;
  label: string;
  path: string[];
  /** Subtitle: the joined path, or a value-type hint. */
  sub: string;
  children: NodeTreeNode[];
}

function pathKey(path: string[]): string {
  return path.length ? path.join('.') : '<root>';
}

/**
 * Build a tree of the STORE nodes, nested by `data.path`. Processes are
 * excluded (they live in the Processes tab). Intermediate path segments with
 * no explicit node are synthesized as group nodes. Returns a synthetic root
 * whose `children` are the top-level stores.
 */
export function buildNodeTree(nodes: any[]): NodeTreeNode {
  const stores = nodes.filter((n) => n.type === 'store');
  const byId = new Map<string, NodeTreeNode>();

  const root: NodeTreeNode = { id: '<root>', label: '<root>', path: [], sub: '', children: [] };
  byId.set('<root>', root);

  // Ensure a tree node exists for every prefix of `path`, wiring parents.
  const ensure = (path: string[], label: string, sub: string): NodeTreeNode => {
    const id = pathKey(path);
    const existing = byId.get(id);
    if (existing) {
      // A previously-synthesized group may now get its real label/subtitle.
      if (label) existing.label = label;
      if (sub) existing.sub = sub;
      return existing;
    }
    const node: NodeTreeNode = { id, label, path, sub, children: [] };
    byId.set(id, node);
    if (path.length > 0) {
      const parent = ensure(path.slice(0, -1), '', '');
      parent.children.push(node);
    } else {
      // path === [] is the root, already seeded above.
    }
    return node;
  };

  for (const n of stores) {
    const path: string[] = n.data?.path ?? [];
    if (path.length === 0) continue;  // the root itself, already present
    const label: string = n.data?.label ?? path[path.length - 1];
    const sub: string = path.join('.');
    ensure(path, label, sub);
  }

  return root;
}

function countTree(node: NodeTreeNode): number {
  let n = 0;
  for (const c of node.children) n += 1 + countTree(c);
  return n;
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
          <InspectorTab selection={props.selection} />
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
          <NodesTreeTab
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
}) {
  const sel = props.selection;
  if (!sel) {
    return <p style={{ color: '#888', fontSize: 12 }}>Click a node to inspect.</p>;
  }

  const description = (sel.details as { description?: unknown })?.description;
  const hasDescription = typeof description === 'string' && description.trim().length > 0;

  return (
    <div>
      <h4 style={{ margin: 0, fontSize: 14, textTransform: 'capitalize' }}>{sel.kind}</h4>
      <p style={{ fontFamily: 'monospace', fontSize: 12, margin: '4px 0' }}>
        {sel.path.length ? sel.path.join('.') : '<root>'}
      </p>

      {hasDescription && (
        <InspectorSection title="Description">
          <pre style={{
            fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            background: '#f7f7f7', padding: 8, margin: 0, borderRadius: 4,
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', color: '#1f2937',
            lineHeight: 1.5,
          }}>
            {description as string}
          </pre>
        </InspectorSection>
      )}

      {sel.kind === 'process' ? (
        <ProcessSchemaSections details={sel.details as Record<string, unknown>} />
      ) : (
        <InspectorSection title="Details">
          <SchemaBlock value={sel.details} />
        </InspectorSection>
      )}
    </div>
  );
}

/** A labeled inspector section. */
function InspectorSection(props: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ margin: '10px 0' }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: '#374151', marginBottom: 4 }}>
        {props.title}
      </div>
      {props.children}
    </div>
  );
}

/** A pretty-printed JSON block, or an em-dash when empty. */
function SchemaBlock(props: { value: unknown }) {
  const v = props.value;
  const empty = v == null
    || (typeof v === 'object' && v !== null && Object.keys(v as object).length === 0);
  if (empty) return <div style={{ fontSize: 12, color: '#9ca3af' }}>—</div>;
  return (
    <pre style={{
      fontSize: 11, background: '#f7f7f7', padding: 8, margin: 0, borderRadius: 4,
      overflow: 'auto', maxHeight: 260, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', color: '#1f2937',
    }}>
      {JSON.stringify(v, null, 2)}
    </pre>
  );
}

/** Process inspector: config / input schema / output schema as distinct sections. */
function ProcessSchemaSections(props: { details: Record<string, unknown> }) {
  const d = props.details;
  return (
    <>
      {typeof d.address === 'string' && d.address && (
        <InspectorSection title="Address">
          <code style={{ fontSize: 11, color: '#374151', wordBreak: 'break-all' }}>
            {d.address as string}
          </code>
        </InspectorSection>
      )}
      <InspectorSection title="Config schema">
        <SchemaBlock value={d.config} />
      </InspectorSection>
      <InspectorSection title="Input schema">
        <SchemaBlock value={d.inputSchema} />
      </InspectorSection>
      <InspectorSection title="Output schema">
        <SchemaBlock value={d.outputSchema} />
      </InspectorSection>
      {(d.inputPortsSchema != null || d.outputPortsSchema != null) && (
        <InspectorSection title="Wiring">
          <SchemaBlock value={{ inputs: d.inputPortsSchema, outputs: d.outputPortsSchema }} />
        </InspectorSection>
      )}
    </>
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

// --- Nodes tab: hierarchical store tree ----------------------------------
//
// Mirrors the store nesting (ported from bigraph-viz2's renderNodes/row). Each
// row indents by depth, gets a ▸/▾ caret when it has children, and a show/hide
// checkbox that reflects EFFECTIVE visibility — it reads OFF when the node or
// any ancestor is hidden (cascade). The big `bulk`/`listeners` subtrees start
// collapsed: only depth 0 is expanded by default.

function NodesTreeTab(props: {
  nodes: any[];
  hidden: Set<string>;
  onToggleHidden: (id: string) => void;
  onShowAll: (kind: 'process' | 'store') => void;
}) {
  const tree = useMemo(() => buildNodeTree(props.nodes), [props.nodes]);

  // Default expansion: top-level stores only (depth 0). This keeps deep stores
  // like `bulk` / `listeners` collapsed for performance on big composites.
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(tree.children.map((c) => c.id)),
  );

  // Re-seed the default expansion when the underlying composite changes (the
  // top-level store ids will differ). Keyed on the joined top-level ids.
  const topIds = tree.children.map((c) => c.id).join('|');
  useEffect(() => {
    setExpanded(new Set(tree.children.map((c) => c.id)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topIds]);

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const total = countTree(tree);

  // Flatten the visible (expanded) rows top-down into render specs.
  const rows: { node: NodeTreeNode; depth: number; hasChildren: boolean; open: boolean }[] = [];
  const walk = (node: NodeTreeNode, depth: number) => {
    for (const child of node.children) {
      const hasChildren = child.children.length > 0;
      const open = expanded.has(child.id);
      rows.push({ node: child, depth, hasChildren, open });
      if (hasChildren && open) walk(child, depth + 1);
    }
  };
  walk(tree, 0);

  return (
    <div>
      <div style={{ marginBottom: 8 }}>
        <button
          onClick={() => props.onShowAll('store')}
          style={{
            background: 'transparent', border: 0, padding: 0,
            color: '#2563eb', fontSize: 12, cursor: 'pointer',
            textDecoration: 'underline',
          }}
        >
          Show all
        </button>
      </div>
      {total === 0 && <p style={{ color: '#888', fontSize: 12 }}>None.</p>}
      {rows.map(({ node, depth, hasChildren, open }) => {
        // EFFECTIVE visibility: off when the node OR any ancestor is hidden.
        const ancHidden = isHiddenByAncestor(node.path.slice(0, -1), props.hidden);
        const effectivelyHidden = props.hidden.has(node.id) || ancHidden;
        return (
          <div
            key={node.id}
            style={{
              display: 'flex', alignItems: 'flex-start', gap: 4,
              padding: '3px 0', paddingLeft: depth * 14, fontSize: 12,
            }}
          >
            {/* expand caret (or a spacer to keep checkboxes aligned) */}
            {hasChildren ? (
              <span
                onClick={() => toggleExpand(node.id)}
                title={open ? 'Collapse' : 'Expand'}
                style={{
                  cursor: 'pointer', color: '#6b7280', width: 12,
                  textAlign: 'center', userSelect: 'none', lineHeight: '16px',
                }}
              >
                {open ? '▾' : '▸'}
              </span>
            ) : (
              <span style={{ width: 12, flex: '0 0 12px' }} />
            )}
            <input
              type="checkbox"
              checked={!effectivelyHidden}
              disabled={ancHidden}
              onChange={() => props.onToggleHidden(node.id)}
              title={ancHidden ? 'Hidden because an ancestor is hidden' : undefined}
              style={{ marginTop: 2, cursor: ancHidden ? 'default' : 'pointer' }}
            />
            <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
              <span style={{ color: effectivelyHidden ? '#9ca3af' : '#111827' }}>
                {node.label}
              </span>
              {node.sub && (
                <span style={{
                  fontFamily: 'monospace', fontSize: 11, color: '#9ca3af',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {node.sub}
                </span>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}
