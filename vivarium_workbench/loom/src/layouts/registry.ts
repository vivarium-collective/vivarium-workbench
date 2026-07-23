// src/layouts/registry.ts — the ordered set of available layout modes.
// Adding a view mode means adding one entry here.

import type { LayoutMode } from './types';
import { hierarchyMode } from './hierarchy';

export const DEFAULT_MODE_ID = 'hierarchy';

export const LAYOUT_MODES: LayoutMode[] = [hierarchyMode];

export function getMode(id: string | null | undefined): LayoutMode {
  return LAYOUT_MODES.find((m) => m.id === id)
    ?? LAYOUT_MODES.find((m) => m.id === DEFAULT_MODE_ID)
    ?? LAYOUT_MODES[0];
}
