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
