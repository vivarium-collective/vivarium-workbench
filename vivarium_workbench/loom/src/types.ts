export interface StoreNodeData {
  label: string;
  nodeType: "store";
  value?: string | number | boolean | null;
  valueType?: string;
  isGroup?: boolean;
  path: string[];
}

export interface ProcessNodeData {
  label: string;
  nodeType: "process";
  processType: string;
  address: string;
  config: Record<string, unknown>;
  interval?: number;
  path: string[];
  inputPorts: string[];
  outputPorts: string[];
  description?: string;
  /** port -> RAW wire target joined with '.', exactly as authored and relative
   *  to the process's parent store. Display only (port tooltips, Inspector):
   *  the join is lossy — `['..','bulk']` becomes `'...bulk'` — so this string
   *  must never be re-split to recover the path. Use `*PortsTarget` for that. */
  inputPortsSchema?: Record<string, string>;
  outputPortsSchema?: Record<string, string>;
  /** port -> RESOLVED ABSOLUTE dotted store path (relative `.`/`..` navigation
   *  already applied with push/pop semantics). `''` is the composite root.
   *  This is the unambiguous form; use it for any path reasoning. */
  inputPortsTarget?: Record<string, string>;
  outputPortsTarget?: Record<string, string>;
  /** Structured contract, serialized as `_contract`. Absent means derive
   *  it from `description` (the process docstring). */
  contract?: Record<string, unknown>;
}

export type BigraphNodeData = StoreNodeData | ProcessNodeData;

export interface EdgeData {
  edgeType: "input" | "output" | "bidirectional" | "place";
  port?: string;
}

export interface GraphResponse {
  nodes: Array<{
    id: string;
    type: string;
    position: { x: number; y: number };
    data: BigraphNodeData;
    parentId?: string;
    extent?: string;
    style?: Record<string, unknown>;
  }>;
  edges: Array<{
    id: string;
    source: string;
    target: string;
    sourceHandle?: string;
    targetHandle?: string;
    label?: string;
    type?: string;
    animated?: boolean;
    data?: EdgeData;
    style?: Record<string, unknown>;
  }>;
}
