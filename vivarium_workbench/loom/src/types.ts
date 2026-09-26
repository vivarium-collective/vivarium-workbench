export interface StoreNodeData {
  label: string;
  nodeType: "store";
  value?: string | number | boolean | null;
  valueType?: string;
  isGroup?: boolean;
  path: string[];
  /** Optional illustrative figure — a data-URI image or inline SVG string from
   *  the spec's `_figure`. Rendered on the card when Detail → Figures allows. */
  figure?: string;
  /** Book-palette hue for this store, shared with its wires + the port dots that
   *  bind it, so source→target is traceable by color (see storeColor.ts). */
  storeColor?: string;
}

export interface ProcessNodeData {
  label: string;
  nodeType: "process";
  processType: string;
  address: string;
  config: Record<string, unknown>;
  /** Declared config parameter surface: name -> {_type, _default} (from the
   *  process's `config_schema`). `config` above holds only EXPLICITLY-set
   *  values; the card reads this to show each parameter's type + default. */
  configSchema?: Record<string, unknown>;
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
  /** port -> TYPE string, from the process spec's `_inputs`/`_outputs` schema.
   *  Distinct from the *PortsSchema fields (which record WHERE a port wires, not
   *  its type). Consumed by the card port rows, tooltips, and the Inspector. */
  inputSchema?: Record<string, unknown>;
  outputSchema?: Record<string, unknown>;
  /** Structured contract, serialized as `_contract`. Absent means derive
   *  it from `description` (the process docstring). */
  contract?: Record<string, unknown>;
  /** Optional illustrative figure — a data-URI image or inline SVG string from
   *  the spec's `_figure` (or `config._figure`). Rendered on the card when
   *  Detail → Figures allows. */
  figure?: string;
  /** True when this process is itself a bigraph Composite (a "Composite
   *  Process", e.g. EcoliWCM): its inner model can be drilled into as another
   *  loom view. The backend sets `is_composite_process`; double-clicking such a
   *  card opens its inner composite (App's drill-down). */
  isCompositeProcess?: boolean;
  /** port name -> book-palette hue of the store it binds, so ProcessNode can
   *  paint each port's connection dot + swatch to match its wire (storeColor.ts). */
  portColors?: Record<string, string>;
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
