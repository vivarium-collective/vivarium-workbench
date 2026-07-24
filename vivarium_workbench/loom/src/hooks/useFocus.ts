// src/hooks/useFocus.ts — which processes are "active" right now.
//
// focused = hover ∪ selection (transient). pinned = explicit, accumulates,
// so two processes' wiring can be compared side by side.
//
// `ctx` is memoized on the three primitives, NOT rebuilt per render: the edge
// filter memoizes on `ctx` identity, and a fresh Set every render would make
// that memo a no-op and re-filter several hundred edges on every mouse move.
// The setters likewise no-op when the value is unchanged, so sliding across one
// process card's interior does not churn state.

import { useCallback, useMemo, useState } from 'react';
import type { FocusContext } from '../layouts/types';

export interface UseFocus {
  hovered: string | null;
  selected: string | null;
  pinned: Set<string>;
  hover: (id: string | null) => void;
  select: (id: string | null) => void;
  togglePin: (id: string) => void;
  clear: () => void;
  ctx: FocusContext;
}

export function useFocus(): UseFocus {
  const [hovered, setHovered] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [pinned, setPinned] = useState<Set<string>>(() => new Set());

  const togglePin = useCallback((id: string) => {
    setPinned((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }, []);

  const clear = useCallback(() => {
    setHovered(null);
    setSelected(null);
    setPinned((prev) => (prev.size === 0 ? prev : new Set()));
  }, []);

  const ctx = useMemo<FocusContext>(() => {
    const focused = new Set<string>();
    if (hovered) focused.add(hovered);
    if (selected) focused.add(selected);
    return { focused, pinned };
  }, [hovered, selected, pinned]);

  return {
    hovered, selected, pinned,
    hover: setHovered, select: setSelected,
    togglePin, clear, ctx,
  };
}
