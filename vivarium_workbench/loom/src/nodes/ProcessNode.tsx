import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { ProcessNodeData } from "../types";
import { deriveContract, abbreviateType, contractCompleteness } from "../contract";

function _classifyStep(address: string | undefined, label: string | undefined): 'process' | 'emitter' | 'visualization' {
  const addr = address || '';
  const lbl = label || '';
  // Emitter convention: process-bigraph emitters end with 'Emitter' OR labeled emitter_*
  if (/Emitter\b/.test(addr) || /^(sqlite_)?emitter\b|^user_emitter\b/.test(lbl)) {
    return 'emitter';
  }
  // Visualization-class heuristic — by-class-name OR by viz_* convention on the step label.
  // Covers TestSuiteTimeSeries, FieldSnapshotsGrid, FieldAnimationGif, FieldHeatmap, DemoTimeSeriesPlot,
  // BondNetworkPlots, MembranePlots, Distribution, PhaseSpace, ParamVsObservable, TimeSeriesPlot, etc.
  if (/(Plot|Heatmap|Animation|Snapshots|Distribution|Viz|TimeSeries|Series|Chart|Trajectory|Histogram|PhaseSpace|ParamVs)/i.test(addr)
      || /^viz[_-]/i.test(lbl)) {
    return 'visualization';
  }
  return 'process';
}

/**
 * Legacy card body: centered label + type with flanking port labels. Used in
 * modes that do NOT drive semantic zoom (hierarchy). Preserved verbatim so
 * hierarchy renders exactly as before this task — semantic zoom is opt-in via a
 * stamped `_tier`, which only process-column mode sets.
 */
function LegacyBody({ data, stepKind }: {
  data: ProcessNodeData;
  stepKind: 'process' | 'emitter' | 'visualization';
}) {
  const inputPorts = data.inputPorts ?? [];
  const outputPorts = data.outputPorts ?? [];
  const portSchema = data.inputPortsSchema ?? {};
  const outSchema = data.outputPortsSchema ?? {};
  return (
    <div className={`process-node process-node-${stepKind}`}>
      {inputPorts.map((port, i) => {
        const typeStr = portSchema[port] ? String(portSchema[port]) : undefined;
        const top = `${((i + 1) / (inputPorts.length + 1)) * 100}%`;
        return (
          <div key={`in-${port}`}>
            <Handle
              type="target"
              position={Position.Left}
              id={port}
              className="port-handle port-handle-input"
              style={{ top }}
            />
            <div className="port-label port-label-left" style={{ top }}>
              <span className="port-label-name">{port}</span>
              {typeStr && (
                <span className="port-label-tooltip">{typeStr}</span>
              )}
            </div>
          </div>
        );
      })}

      <div className="process-body">
        <div className="process-label">{data.label}</div>
        <div className="process-type">{data.processType}</div>
      </div>

      {outputPorts.map((port, i) => {
        const typeStr = outSchema[port] ? String(outSchema[port]) : undefined;
        const top = `${((i + 1) / (outputPorts.length + 1)) * 100}%`;
        return (
          <div key={`out-${port}`}>
            <Handle
              type="source"
              position={Position.Right}
              id={port}
              className="port-handle port-handle-output"
              style={{ top }}
            />
            <div className="port-label port-label-right" style={{ top }}>
              <span className="port-label-name">{port}</span>
              {typeStr && (
                <span className="port-label-tooltip">{typeStr}</span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ProcessNode({ data }: NodeProps & { data: ProcessNodeData }) {
  const inputPorts = data.inputPorts ?? [];
  const outputPorts = data.outputPorts ?? [];
  const stepKind = _classifyStep((data as any).address, data.label);

  // Semantic zoom is opt-in: only process-column mode stamps `_tier` (or
  // `_pinnedOpen`). Absent both, render the legacy fixed card so hierarchy mode
  // is untouched.
  if ((data as any)._tier == null && !(data as any)._pinnedOpen) {
    return <LegacyBody data={data} stepKind={stepKind} />;
  }

  // Semantic zoom: the stamped tier decides WHICH rows exist. Font size is
  // constant across tiers (see App.css) — legibility at low zoom comes from
  // dropping content, never from shrinking text. A pinned-open card always
  // shows full detail regardless of the current zoom tier.
  const tier = ((data as any)._tier ?? 'ports') as
    'glyph' | 'ports' | 'types' | 'contract' | 'full';
  const t = (data as any)._pinnedOpen ? 'full' : tier;

  const show = {
    ports:    t !== 'glyph',
    types:    t === 'types' || t === 'contract' || t === 'full',
    contract: t === 'contract' || t === 'full',
    full:     t === 'full',
  };

  const contract = show.contract ? deriveContract(data) : null;
  const completeness = show.full ? contractCompleteness(contract, data) : null;
  const inTypes = ((data as any).inputSchema ?? {}) as Record<string, unknown>;
  const outTypes = ((data as any).outputSchema ?? {}) as Record<string, unknown>;
  const configEntries = Object.entries(data.config ?? {});

  const portRow = (port: string, types: Record<string, unknown>, isOut: boolean) => {
    const raw = typeof types[port] === 'string' ? (types[port] as string) : '';
    const semantic = isOut ? contract?.outputs?.[port] : contract?.inputs?.[port];
    return (
      <div key={`${isOut ? 'o' : 'i'}-${port}`}
           className={`process-node-port-row${isOut ? ' is-out' : ''}`}>
        <span className="process-node-port-name">{port}</span>
        {show.types && raw && (
          <span className="process-node-port-type" title={raw}>{abbreviateType(raw)}</span>
        )}
        {show.contract && semantic && (
          <span className="process-node-port-semantic">{semantic}</span>
        )}
      </div>
    );
  };

  return (
    <div className={`process-node process-node-${stepKind} process-node-${t}`}>
      {/* Handles anchor the wires at EVERY tier — they stay present even at the
          glyph tier where no port labels are drawn, so focused-process wiring
          (Task 6) keeps attaching by port id. */}
      {inputPorts.map((port, i) => (
        <Handle
          key={`h-in-${port}`}
          type="target"
          position={Position.Left}
          id={port}
          className="port-handle port-handle-input"
          style={{ top: `${((i + 1) / (inputPorts.length + 1)) * 100}%` }}
        />
      ))}
      {outputPorts.map((port, i) => (
        <Handle
          key={`h-out-${port}`}
          type="source"
          position={Position.Right}
          id={port}
          className="port-handle port-handle-output"
          style={{ top: `${((i + 1) / (outputPorts.length + 1)) * 100}%` }}
        />
      ))}

      <div className="process-node-title">{data.label}</div>

      {show.ports && (
        <>
          <div className="process-node-meta">
            {data.processType} · {inputPorts.length} in / {outputPorts.length} out
            {data.interval != null && <span> · every {data.interval}</span>}
          </div>
          <div className="process-node-portlist">
            {inputPorts.map((p) => portRow(p, inTypes, false))}
            {outputPorts.map((p) => portRow(p, outTypes, true))}
          </div>
        </>
      )}

      {show.types && (data as any).address && (
        <div className="process-node-address">{(data as any).address}</div>
      )}

      {show.types && configEntries.length > 0 && (
        <div className="process-node-config">
          {configEntries.map(([k, v]) => (
            <div key={k} className="process-node-config-row">
              <span>{k}</span>
              {show.contract && <span>{String(v).slice(0, 40)}</span>}
            </div>
          ))}
        </div>
      )}

      {show.contract && contract?.summary && (
        <div className="process-node-summary">{contract.summary}</div>
      )}

      {show.contract && contract && contract.math.length > 0 && (
        <div className="process-node-math">
          {contract.math.map((m, i) => <div key={i}>{m}</div>)}
        </div>
      )}

      {show.full && contract && Object.keys(contract.symbols).length > 0 && (
        <div className="process-node-symbols">
          {Object.entries(contract.symbols).map(([s, meaning]) => (
            <div key={s}><em>{s}</em> — {meaning}</div>
          ))}
        </div>
      )}

      {show.full && contract?.description && (
        <div className="process-node-description">{contract.description}</div>
      )}

      {show.full && completeness && completeness.total > 0 && (
        <div className="process-node-completeness">
          {completeness.documented}/{completeness.total} ports documented
          {completeness.unknownPorts.length > 0 && (
            <span className="is-warn"> · unknown: {completeness.unknownPorts.join(', ')}</span>
          )}
        </div>
      )}
    </div>
  );
}

export default memo(ProcessNode);
