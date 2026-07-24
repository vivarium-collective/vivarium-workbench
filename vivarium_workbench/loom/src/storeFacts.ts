// src/storeFacts.ts — facts about a store derived from the wire graph.
//
// The store-side counterpart of the process contract: a process declares
// what it does with a port, and a store can report who touches it. Both
// come from data already in the document.

import type { Edge } from '@xyflow/react';

export interface StoreFacts {
  /** Processes that read this store (store -> process, edgeType 'input'). */
  readers: string[];
  /** Processes that write this store (process -> store, edgeType 'output'). */
  writers: string[];
}

export function readersAndWriters(storeId: string, edges: Edge[]): StoreFacts {
  const readers = new Set<string>();
  const writers = new Set<string>();
  for (const e of edges) {
    const kind = (e.data as { edgeType?: string } | undefined)?.edgeType;
    if (kind === 'input' && e.source === storeId) readers.add(e.target);
    else if (kind === 'output' && e.target === storeId) writers.add(e.source);
  }
  return { readers: [...readers].sort(), writers: [...writers].sort() };
}
