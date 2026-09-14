// src/panels/InspectorPanel.tsx — selected-node detail.
//
// Updates on canvas node click (App's handleNodeClick → setSelection). A store
// shows its schema; a process gets a COMPREHENSIVE, aesthetic reference panel:
// a header with type/interval, a prominent contract summary, then description,
// a full ports table (type + wired store + meaning), config (schema-driven, with
// types + which values are set), equations, symbols, assumptions, references —
// and the raw type/wiring schemas collapsed for completeness.

import type React from 'react';
import type { ExploreInspectMsg } from '../api';
import type { ProcessNodeData } from '../types';
import { deriveContract } from '../contract';
import { portInfo } from '../portInfo';
import { configParams } from '../configView';
import { KatexBlock } from '../Katex';
import { toLatex, MathText } from '../mathText';

type Selection = Omit<ExploreInspectMsg, 'type'> | null;

export interface InspectorPanelProps {
  selection: Selection;
  /** True when the selected node is the locked (clicked) one — shows a lock chip. */
  locked?: boolean;
}

export function InspectorPanel(props: InspectorPanelProps) {
  const sel = props.selection;
  if (!sel) {
    return <p className="insp-empty">Click a node to inspect.</p>;
  }
  const isProc = sel.kind === 'process';
  const d = sel.details as unknown as ProcessNodeData;
  const leaf = sel.path.length ? sel.path[sel.path.length - 1] : '<root>';

  return (
    <div className="insp">
      <div className="insp-header">
        <div className="insp-kindrow">
          {props.locked && <span title="Locked — click empty canvas to unlock">🔒</span>}
          <span className="insp-kind">{sel.kind}</span>
          {isProc && d?.isCompositeProcess && (
            <span className="insp-badge" title="A Composite Process — double-click its card to open the inner model">
              ⤢ composite process
            </span>
          )}
        </div>
        <div className="insp-title">{leaf}</div>
        <div className="insp-path mono">{sel.path.length ? sel.path.join('.') : '<root>'}</div>
        {isProc && (
          <div className="insp-sub">
            {d.processType}
            {d.interval != null && <> · every {d.interval}</>}
            {' · '}{(d.inputPorts?.length ?? 0)} in / {(d.outputPorts?.length ?? 0)} out
          </div>
        )}
      </div>

      {isProc ? (
        <ProcessDetail details={d} />
      ) : (
        <StoreDetail details={sel.details} />
      )}
    </div>
  );
}

/** Store inspector: description (if any) then the raw schema. */
function StoreDetail(props: { details: Record<string, unknown> }) {
  const description = (props.details as { description?: unknown })?.description;
  const hasDescription = typeof description === 'string' && description.trim().length > 0;
  return (
    <>
      {hasDescription && (
        <InspectorSection title="Description">
          <Prose text={description as string} />
        </InspectorSection>
      )}
      <InspectorSection title="Details">
        <SchemaBlock value={props.details} />
      </InspectorSection>
    </>
  );
}

/** A labeled inspector section — collapsible (open by default). */
function InspectorSection(props: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  return (
    <details className="inspector-section" open={props.defaultOpen !== false}>
      <summary>
        <span className="inspector-caret">▶</span>
        {props.title}
      </summary>
      <div className="inspector-section-body">{props.children}</div>
    </details>
  );
}

function Prose(props: { text: string }) {
  return <p className="insp-prose"><MathText text={props.text} /></p>;
}

/** A pretty-printed JSON block, or an em-dash when empty. */
function SchemaBlock(props: { value: unknown }) {
  const v = props.value;
  const empty = v == null
    || (typeof v === 'object' && v !== null && Object.keys(v as object).length === 0);
  if (empty) return <div className="insp-empty-val">—</div>;
  return <pre className="insp-schema">{JSON.stringify(v, null, 2)}</pre>;
}

/** One port row: name · direction · type · wired store · contract meaning. */
function PortDetail(props: {
  port: string;
  isOut: boolean;
  data: ProcessNodeData;
  semantic?: string;
}) {
  const { port, isOut, data, semantic } = props;
  const info = portInfo(port, isOut, {
    typeSchema: (isOut ? data.outputSchema : data.inputSchema) as Record<string, unknown> | undefined,
    portsSchema: isOut ? data.outputPortsSchema : data.inputPortsSchema,
    portsTarget: isOut ? data.outputPortsTarget : data.inputPortsTarget,
  });
  return (
    <div className="insp-port">
      <div className="insp-port-head">
        <span className={`insp-port-name ${isOut ? 'is-out' : 'is-in'}`}>{port}</span>
        <span className="insp-port-dir">{info.direction}</span>
        {info.type && <code className="insp-port-type" title={info.fullType}>{info.type}</code>}
      </div>
      {info.connectsTo && (
        <div className="insp-port-wire">
          <span className="insp-port-arrow">→ </span>
          <code>{info.connectsTo}</code>
        </div>
      )}
      {semantic && <div className="insp-port-sem">{semantic}</div>}
    </div>
  );
}

/** Comprehensive process inspector. */
function ProcessDetail(props: { details: ProcessNodeData }) {
  const d = props.details;
  const contract = deriveContract(d);
  const inputPorts = d.inputPorts ?? [];
  const outputPorts = d.outputPorts ?? [];
  const cfg = configParams(
    (d as { configSchema?: Record<string, unknown> }).configSchema,
    d.config,
  );
  // The prominent "Contract" callout is for a DECLARED structured contract
  // (e.g. EcoliWCM / PymunkProcess), not one merely derived from the docstring —
  // otherwise it would just duplicate the Description. Docstring-only processes
  // keep their Description section.
  const declared = (d as unknown as { contract?: { summary?: unknown } }).contract;
  const declaredSummary = declared && typeof declared === 'object'
    && typeof declared.summary === 'string' && declared.summary.trim()
      ? declared.summary : undefined;
  const rawDescription = d.description;
  const hasDescription = typeof rawDescription === 'string' && rawDescription.trim().length > 0
    && rawDescription !== declaredSummary;

  return (
    <>
      {/* Prominent contract summary — the headline "what this process does".
          Only for a declared structured contract (else the Description covers it). */}
      {declaredSummary && (
        <div className="insp-contract">
          <div className="insp-contract-label">Contract</div>
          <p className="insp-contract-text">{declaredSummary}</p>
        </div>
      )}

      {hasDescription && (
        <InspectorSection title="Description">
          <Prose text={rawDescription as string} />
        </InspectorSection>
      )}

      {(inputPorts.length > 0 || outputPorts.length > 0) && (
        <InspectorSection title={`Ports (${inputPorts.length} in / ${outputPorts.length} out)`}>
          {inputPorts.map((p) => (
            <PortDetail key={`i-${p}`} port={p} isOut={false} data={d} semantic={contract?.inputs?.[p]} />
          ))}
          {outputPorts.map((p) => (
            <PortDetail key={`o-${p}`} port={p} isOut data={d} semantic={contract?.outputs?.[p]} />
          ))}
        </InspectorSection>
      )}

      {cfg.length > 0 && (
        <InspectorSection title={`Config (${cfg.length})`}>
          <div className="insp-config">
            <div className="insp-config-head">
              <span>parameter</span><span>value</span><span>type</span>
            </div>
            {cfg.map((c) => (
              <div key={c.name} className={`insp-config-row${c.set ? ' is-set' : ''}`}>
                <span className="insp-config-key" title={c.set ? 'set by the composite' : 'default'}>
                  {c.set && <span className="insp-config-dot" />}
                  {c.name}
                </span>
                <span className="insp-config-val mono" title={c.value}>{c.value}</span>
                <span className="insp-config-type mono">{c.type || '—'}</span>
              </div>
            ))}
          </div>
        </InspectorSection>
      )}

      {contract && contract.math.length > 0 && (
        <InspectorSection title="Equations">
          <div className="insp-math">
            {contract.math.map((m, i) => <KatexBlock key={i} tex={toLatex(m)} />)}
          </div>
        </InspectorSection>
      )}

      {contract && Object.keys(contract.symbols).length > 0 && (
        <InspectorSection title="Symbols">
          <div className="insp-symbols">
            {Object.entries(contract.symbols).map(([s, meaning]) => (
              <div key={s}><em><MathText text={s} symbolKey /></em> — <MathText text={meaning} /></div>
            ))}
          </div>
        </InspectorSection>
      )}

      {contract && contract.assumptions.length > 0 && (
        <InspectorSection title="Assumptions">
          <ul className="insp-list">
            {contract.assumptions.map((a, i) => <li key={i}>{a}</li>)}
          </ul>
        </InspectorSection>
      )}

      {contract && contract.references.length > 0 && (
        <InspectorSection title="References">
          <ul className="insp-list">
            {contract.references.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </InspectorSection>
      )}

      {typeof d.address === 'string' && d.address && (
        <InspectorSection title="Address" defaultOpen={false}>
          <code className="insp-address">{d.address}</code>
        </InspectorSection>
      )}
      <InspectorSection title="Input type schema" defaultOpen={false}>
        <SchemaBlock value={d.inputSchema} />
      </InspectorSection>
      <InspectorSection title="Output type schema" defaultOpen={false}>
        <SchemaBlock value={d.outputSchema} />
      </InspectorSection>
      {(d.inputPortsSchema != null || d.outputPortsSchema != null) && (
        <InspectorSection title="Wiring (raw)" defaultOpen={false}>
          <SchemaBlock value={{ inputs: d.inputPortsSchema, outputs: d.outputPortsSchema }} />
        </InspectorSection>
      )}
    </>
  );
}
