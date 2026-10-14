// src/layouts/tiers.ts — the semantic-zoom tier ladder, shared by every mode
// that grows its cards with zoom.
//
// Extracted from processColumn.ts so hierarchy.ts can size its ELK nodes per
// tier without importing the whole process-column module (which pulls in the
// affinity clustering). processColumn.ts re-exports these so existing imports
// keep resolving.

import type { ZoomTier, ZoomTierId } from './types';

export const TIERS: ZoomTier[] = [
  { id: 'glyph',    minZoom: 0,    cardWidth: 180, cardHeight: 56 },
  { id: 'ports',    minZoom: 0.25, cardWidth: 240, cardHeight: 96 },
  { id: 'types',    minZoom: 0.5,  cardWidth: 340, cardHeight: 150 },
  { id: 'config',   minZoom: 0.7,  cardWidth: 390, cardHeight: 190 },
  { id: 'contract', minZoom: 0.9,  cardWidth: 430, cardHeight: 240 },
  { id: 'full',     minZoom: 1.6,  cardWidth: 680, cardHeight: 320 },
];

/** Zoom overlap a tier keeps once entered, so scrolling across a threshold
 *  does not flicker cards between two tiers. */
export const TIER_HYSTERESIS = 0.05;

export function tierForZoom(zoom: number, current?: ZoomTierId): ZoomTierId {
  // Raw tier for this zoom: the highest tier (TIERS is ascending by minZoom)
  // whose lower edge the zoom has reached.
  let rawIdx = 0;
  for (let i = 0; i < TIERS.length; i++) if (zoom >= TIERS[i].minZoom) rawIdx = i;
  const raw = TIERS[rawIdx].id;
  if (!current) return raw;

  const curIdx = TIERS.findIndex((t) => t.id === current);
  if (curIdx < 0 || raw === current) return raw;

  // Zooming IN (raw is a higher tier): advance immediately — by definition of
  // the raw tier, `zoom` has already passed the target tier's minZoom. Applying
  // hysteresis here is what stalled every upward transition.
  if (rawIdx > curIdx) return raw;

  // Zooming OUT (raw is a lower tier): hold the current tier until `zoom` dips a
  // full TIER_HYSTERESIS below the current tier's lower edge, so a small wobble
  // across the threshold does not flicker a tier. The margin (0.05) is smaller
  // than every gap between adjacent minZooms (>=0.25), so no tier is skipped.
  if (zoom >= TIERS[curIdx].minZoom - TIER_HYSTERESIS) return current;
  return raw;
}
