// src/contract.ts — what a process advertises about itself.
//
// A process may declare a structured contract (serialized as `_contract`).
// When it does not, one is derived from its docstring: 45 of 46 v2ecoli
// baseline processes have a doc, and 14 already carry equations in the
// indented-block convention this parser reads. Derivation means the view
// works on day one and processes upgrade incrementally.

import type { ProcessNodeData } from './types';

export interface ProcessContract {
  summary: string;
  description: string;
  inputs: Record<string, string>;
  outputs: Record<string, string>;
  config: Record<string, string>;
  math: string[];
  symbols: Record<string, string>;
  assumptions: string[];
  references: string[];
}

/** Markers that make a docstring line an equation rather than prose. */
const MATH_RE = /[=~∑∏≈←≥≤]|\b(Multinomial|Binomial|Poisson|Normal|Gamma|Exponential)\s*\(/;

/** Structured types run past 300 chars; a card shows the shape, not the fields. */
export function abbreviateType(type: string): string {
  if (!type || typeof type !== 'string') return '';
  const m = type.match(/^([A-Za-z0-9_]+)\[(.*)\]$/s);
  if (!m) return type;
  const [, base, inner] = m;
  const fields = inner.split('|');
  // One "field" means it is a container like map[float] — keep it literal.
  if (fields.length < 2) return type;
  return `${base}[${fields.length} fields]`;
}

function emptyContract(): ProcessContract {
  return { summary: '', description: '', inputs: {}, outputs: {},
    config: {}, math: [], symbols: {}, assumptions: [], references: [] };
}

function fromDocstring(doc: string): ProcessContract {
  const c = emptyContract();
  const lines = doc.split('\n');
  const prose: string[] = [];

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (!c.summary) { c.summary = line; continue; }
    if (MATH_RE.test(line)) c.math.push(line);
    else prose.push(line);
  }
  c.description = prose.join(' ');
  return c;
}

/** The process's contract: declared if present, else derived from its doc. */
export function deriveContract(data: ProcessNodeData): ProcessContract | null {
  const declared = (data as unknown as { contract?: Partial<ProcessContract> }).contract;
  if (declared && typeof declared === 'object') {
    return { ...emptyContract(), ...declared };
  }
  const doc = data.description;
  if (!doc || !doc.trim()) return null;
  return fromDocstring(doc);
}

export interface Completeness {
  documented: number;
  total: number;
  /** Contract entries naming a port the process no longer has. */
  unknownPorts: string[];
}

export function contractCompleteness(
  c: ProcessContract | null,
  data: ProcessNodeData,
): Completeness {
  const inPorts = new Set(data.inputPorts ?? []);
  const outPorts = new Set(data.outputPorts ?? []);
  const total = inPorts.size + outPorts.size;
  if (!c) return { documented: 0, total, unknownPorts: [] };

  let documented = 0;
  const unknownPorts: string[] = [];
  for (const [port, text] of Object.entries(c.inputs)) {
    if (inPorts.has(port)) { if (text) documented++; } else unknownPorts.push(port);
  }
  for (const [port, text] of Object.entries(c.outputs)) {
    if (outPorts.has(port)) { if (text) documented++; } else unknownPorts.push(port);
  }
  return { documented, total, unknownPorts: unknownPorts.sort() };
}
