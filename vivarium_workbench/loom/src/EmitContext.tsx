// Context that broadcasts the current explicit-emit path set to node renderers.
// Each entry is a path joined by '/'. A node is "emitting" if its own path is in
// the set, or if any prefix of its path is in the set (inherited from an
// ancestor).
import { createContext } from 'react';

export const EmitContext = createContext<Set<string>>(new Set());
