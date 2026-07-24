// src/panels/ProcessRail.tsx — browse the process inventory by cluster.
//
// Process-column mode draws the clusters on the canvas as bare bands: the
// grouping is visible but unnamed. This rail is the legible, browsable half —
// it names every cluster, lets a reader search/jump/pin, and drives the SAME
// focus state the canvas culls edges by, so the two stay in sync.
//
// Performance note: the rail re-renders on every canvas hover (focus.ctx
// changes identity when hover/selection moves). So the expensive derivations —
// the id→label map and the search-filtered band list — memoize on genuinely
// stable inputs (nodes, bands, query, hiddenIds), NEVER on focus.ctx. The only
// per-render, per-row work is two Set.has() reads against focus.ctx; no fresh
// Set or node map is built during a hover.

import { useCallback, useMemo, useState } from 'react';
import type { Node } from '@xyflow/react';
import type { GroupBand } from '../layouts/types';
import type { UseFocus } from '../hooks/useFocus';

export interface ProcessRailProps {
  bands: GroupBand[];
  nodes: Node[];
  focus: UseFocus;
  granularity: number;
  onGranularityChange: (g: number) => void;
  onNavigate: (nodeId: string) => void;
  /**
   * Node ids currently hidden from the canvas (the sidebar toggles + the
   * default bookkeeping hide). The rail uses this to decide what to do with the
   * `~unclustered` bucket: a terminal bucket whose every member is hidden is
   * collapsed by default (named, counted, but not listed as if it were on the
   * canvas) rather than dumping 19 off-canvas bookkeeping rows in among the
   * visible ones. Still expandable and always searchable. Optional — treated as
   * "nothing hidden" when omitted.
   */
  hiddenIds?: Set<string>;
}

const EMPTY: Set<string> = new Set();

export function ProcessRail({
  bands, nodes, focus, granularity, onGranularityChange, onNavigate, hiddenIds = EMPTY,
}: ProcessRailProps) {
  const [query, setQuery] = useState('');
  // Per-band manual expand/collapse overrides, keyed by band.key. Absent = use
  // the band's default (collapsed only for an all-hidden terminal bucket).
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});

  const labelById = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of nodes) m.set(n.id, String((n.data as { label?: unknown })?.label ?? n.id));
    return m;
  }, [nodes]);

  const q = query.trim().toLowerCase();

  // A terminal bucket (key starts with '~') whose members are ALL hidden — the
  // `~unclustered` bookkeeping bucket — collapses by default. A real cluster
  // with an incidentally-all-hidden membership does NOT collapse; its rows still
  // render (marked hidden), because the user hid them explicitly and expects to
  // see them listed.
  const isDefaultCollapsed = useCallback(
    (band: GroupBand) =>
      band.key.startsWith('~')
      && band.nodeIds.length > 0
      && band.nodeIds.every((id) => hiddenIds.has(id)),
    [hiddenIds],
  );

  // Search-filter each band; drop bands with no surviving members. Memoized on
  // stable inputs only (never focus.ctx) so a hover does not re-filter.
  const filtered = useMemo(
    () => bands
      .map((band) => ({
        band,
        ids: band.nodeIds.filter(
          (id) => !q || (labelById.get(id) ?? id).toLowerCase().includes(q),
        ),
      }))
      .filter((g) => g.ids.length > 0),
    [bands, q, labelById],
  );

  const searching = q.length > 0;

  return (
    <div className="loom-process-rail">
      <input
        className="loom-rail-search"
        placeholder="Search processes…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      <label className="loom-rail-granularity">
        <span>Granularity</span>
        <input
          type="range" min={0} max={1} step={0.05}
          aria-label="Cluster granularity"
          value={granularity}
          onChange={(e) => onGranularityChange(parseFloat(e.target.value))}
        />
      </label>

      <div className="loom-rail-list">
        {filtered.map(({ band, ids }) => {
          // Searching always reveals matches, even inside a collapsed bucket.
          const expanded = searching
            || (overrides[band.key] ?? !isDefaultCollapsed(band));
          const collapsible = isDefaultCollapsed(band);
          return (
            <div key={band.key} className="loom-rail-cluster">
              <div
                className={`loom-cluster-band loom-rail-cluster-label${collapsible ? ' is-collapsible' : ''}`}
                onClick={collapsible
                  ? () => setOverrides((o) => ({ ...o, [band.key]: !expanded }))
                  : undefined}
                role={collapsible ? 'button' : undefined}
              >
                {collapsible && <span className="loom-rail-caret">{expanded ? '▾' : '▸'}</span>}
                {band.label}
              </div>
              {expanded
                ? ids.map((id) => {
                  const active = focus.ctx.focused.has(id) || focus.ctx.pinned.has(id);
                  const hidden = hiddenIds.has(id);
                  const pinned = focus.ctx.pinned.has(id);
                  const cls = 'loom-rail-row'
                    + (active ? ' is-active' : '')
                    + (hidden ? ' is-hidden' : '');
                  return (
                    <div
                      key={id}
                      className={cls}
                      onMouseEnter={() => focus.hover(id)}
                      onMouseLeave={() => focus.hover(null)}
                      onClick={() => { focus.select(id); onNavigate(id); }}
                    >
                      <span className="loom-rail-row-label">{labelById.get(id) ?? id}</span>
                      <button
                        type="button"
                        className={`loom-rail-pin${pinned ? ' is-pinned' : ''}`}
                        title={pinned ? 'Unpin' : 'Pin'}
                        onClick={(e) => { e.stopPropagation(); focus.togglePin(id); }}
                      >
                        📌
                      </button>
                    </div>
                  );
                })
                : (
                  <div className="loom-rail-hidden-note">{ids.length} hidden · click to show</div>
                )}
            </div>
          );
        })}
        {filtered.length === 0 && <div className="loom-rail-empty">No matching processes</div>}
      </div>
    </div>
  );
}
