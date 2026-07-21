import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { ProcessNodeData } from "../types";

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

function ProcessNode({ data }: NodeProps & { data: ProcessNodeData }) {
  const inputPorts = data.inputPorts ?? [];
  const outputPorts = data.outputPorts ?? [];
  const portSchema = (data as any).inputPortsSchema ?? {};
  const outSchema = (data as any).outputPortsSchema ?? {};
  const stepKind = _classifyStep((data as any).address, data.label);

  return (
    <div className={`process-node process-node-${stepKind}`}>
      {/* Input ports on the left */}
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

      {/* Output ports on the right */}
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

export default memo(ProcessNode);
