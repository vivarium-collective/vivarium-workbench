// src/hooks/useLayoutMode.ts — owns which layout mode is active and
// dispatches layout runs through the registry.

import { useCallback, useState } from 'react';
import type { Node, Edge } from '@xyflow/react';
import { getMode, DEFAULT_MODE_ID } from '../layouts/registry';
import { DEFAULT_GRANULARITY } from '../layouts/processColumn';
import type { GroupBand, LayoutResult, ZoomTierId } from '../layouts/types';

export interface UseLayoutMode {
  modeId: string;
  setModeId: (id: string) => void;
  mode: ReturnType<typeof getMode>;
  bands: GroupBand[];
  granularity: number;
  setGranularity: (g: number) => void;
  runLayout: (
    nodes: Node[],
    edges: Edge[],
    compositeId: string | null,
    tier: ZoomTierId,
  ) => Promise<LayoutResult>;
}

export function useLayoutMode(initialModeId = DEFAULT_MODE_ID): UseLayoutMode {
  const [modeId, setModeId] = useState(initialModeId);
  const [bands, setBands] = useState<GroupBand[]>([]);
  // Anchored default: hubFractionFor(DEFAULT_GRANULARITY) is exactly the
  // hubFraction affinityFixture.test.ts validates. Do not hardcode a number
  // here — the app's default clustering must be the tested one.
  const [granularity, setGranularity] = useState(DEFAULT_GRANULARITY);
  const mode = getMode(modeId);

  const runLayout = useCallback(
    async (nodes: Node[], edges: Edge[], compositeId: string | null, tier: ZoomTierId) => {
      const result = await getMode(modeId).run(nodes, edges, { compositeId, tier, granularity });
      setBands(result.bands ?? []);
      return result;
    },
    [modeId, granularity],
  );

  return { modeId, setModeId, mode, bands, granularity, setGranularity, runLayout };
}
