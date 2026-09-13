// src/panels/OutputsPanel.tsx — the unified OUTPUTS area of the composite
// surface. One place for everything a run produces: Results (the trajectory),
// Visualizations, Report Cards, and Analyses. Replaces the separate Results /
// Visualizations top tabs so the surface reads Explore → Configure/Inputs →
// Run/Step → Outputs.
import { useState } from 'react';
import { ResultsPanel } from './ResultsPanel';
import { VisualizationsPanel } from './VisualizationsPanel';

type OutputTab = 'results' | 'visualizations' | 'cards' | 'analyses';

export interface OutputsPanelProps {
  trajectory: Array<{ step: number; time?: number; state: Record<string, unknown> }> | null;
  vizHtml: Record<string, { html: string }> | null;
  hasRun: boolean;
  runId: string | null;
  downloadable: boolean;
  readOnly?: boolean;
  /** Composite name/id — names the "↓ Visualizations" zip download. */
  baseName?: string;
  /** Run flags: the run produced a report / analyses (content may render out of
   *  band). Used to distinguish "none produced" from "produced, not embedded". */
  hasReport?: boolean;
  hasAnalyses?: boolean;
  /** Visualizations the composite declares up front (resolve payload) — shown as
   *  expected cards in the Visualizations tab before they render. */
  declaredViz?: Array<{ name?: string; config?: { title?: string } | null }> | null;
  /** Current run phase + whether a run is live, for the expected-card status. */
  runPhase?: string | null;
  isRunning?: boolean;
}

/** Split the run's HTML artifacts (viz_html) across the sub-tabs by name: a
 *  report-card viewer, an analysis viewer, or a plain visualization. Composites
 *  that emit named report-card/analysis HTML land in the right tab; everything
 *  else stays under Visualizations. */
function classifyArtifacts(vizHtml: Record<string, { html: string }> | null): {
  viz: Record<string, { html: string }>;
  cards: Record<string, { html: string }>;
  analyses: Record<string, { html: string }>;
} {
  const viz: Record<string, { html: string }> = {};
  const cards: Record<string, { html: string }> = {};
  const analyses: Record<string, { html: string }> = {};
  for (const [k, v] of Object.entries(vizHtml ?? {})) {
    if (/report[_-]?card|report[_-]?verdict|\bcard\b/i.test(k)) cards[k] = v;
    else if (/analy/i.test(k)) analyses[k] = v;
    else viz[k] = v;
  }
  return { viz, cards, analyses };
}

const TABS: Array<{ id: OutputTab; label: string }> = [
  { id: 'results', label: 'Results' },
  { id: 'visualizations', label: 'Visualizations' },
  { id: 'cards', label: 'Report Cards' },
  { id: 'analyses', label: 'Analyses' },
];

function _count(v: Record<string, unknown> | null | undefined): number {
  return v ? Object.keys(v).length : 0;
}

function EmptyArtifacts({ kind, produced }: { kind: string; produced?: boolean }) {
  return (
    <div className="outputs-empty">
      {produced
        ? `This run produced ${kind}, but none are embeddable here — open the run to view them.`
        : `No ${kind} for this run yet. They appear here after a run that produces them.`}
    </div>
  );
}

function HtmlArtifacts({ items }: { items: Record<string, { html?: string }> }) {
  return (
    <div className="outputs-artifacts">
      {Object.entries(items).map(([name, a]) => (
        <section className="outputs-artifact" key={name}>
          <h4 className="outputs-artifact-h">{name}</h4>
          {a.html
            ? <iframe className="outputs-artifact-frame" title={name} srcDoc={a.html} sandbox="allow-scripts allow-same-origin" />
            : <div className="outputs-empty">No preview.</div>}
        </section>
      ))}
    </div>
  );
}

export function OutputsPanel(props: OutputsPanelProps) {
  const [tab, setTab] = useState<OutputTab>('results');
  const { viz, cards, analyses } = classifyArtifacts(props.vizHtml);
  const counts: Record<OutputTab, number> = {
    results: props.trajectory ? props.trajectory.length : 0,
    visualizations: _count(viz),
    cards: _count(cards),
    analyses: _count(analyses),
  };
  // Results + Visualizations are always present; Report Cards / Analyses only
  // surface once a run produced them (or the flags say it did) — no empty tabs.
  const visibleTabs = TABS.filter((t) =>
    t.id === 'results' || t.id === 'visualizations'
    || counts[t.id] > 0
    || (t.id === 'cards' && props.hasReport)
    || (t.id === 'analyses' && props.hasAnalyses));
  const activeTab = visibleTabs.some((t) => t.id === tab) ? tab : 'results';

  return (
    <div className="outputs-panel">
      <nav className="outputs-tabs">
        {visibleTabs.map((t) => (
          <button
            key={t.id}
            className={'outputs-tab' + (activeTab === t.id ? ' active' : '')}
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {counts[t.id] > 0 && <span className="outputs-tab-count">{counts[t.id]}</span>}
          </button>
        ))}
      </nav>
      <div className="outputs-body">
        {activeTab === 'results' && (
          <ResultsPanel
            trajectory={props.trajectory}
            hasRun={props.hasRun}
            runId={props.runId}
            downloadable={props.downloadable}
            readOnly={props.readOnly}
          />
        )}
        {activeTab === 'visualizations' && (
          <VisualizationsPanel
            vizHtml={props.vizHtml ? viz : null}
            hasRun={props.hasRun}
            readOnly={props.readOnly}
            baseName={props.baseName}
            declared={props.declaredViz}
            phase={props.runPhase}
            isRunning={props.isRunning}
          />
        )}
        {activeTab === 'cards' && (
          _count(cards) > 0
            ? <HtmlArtifacts items={cards} />
            : <EmptyArtifacts kind="report cards" produced={props.hasReport} />
        )}
        {activeTab === 'analyses' && (
          _count(analyses) > 0
            ? <HtmlArtifacts items={analyses} />
            : <EmptyArtifacts kind="analyses" produced={props.hasAnalyses} />
        )}
      </div>
    </div>
  );
}
