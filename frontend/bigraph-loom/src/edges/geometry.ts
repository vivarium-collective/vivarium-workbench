// src/edges/geometry.ts — pure geometry helpers for floating edges.

export interface Point {
  x: number;
  y: number;
}

/**
 * The point on a circle (`center`, `radius`) nearest to `toward` — i.e. where
 * a straight line from the circle's center to `toward` crosses the
 * circumference.
 *
 * Falls back to the +x direction when `toward` coincides with `center`, so the
 * result is always a real on-circle point (never NaN).
 */
export function circleAnchor(center: Point, radius: number, toward: Point): Point {
  const dx = toward.x - center.x;
  const dy = toward.y - center.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return { x: center.x + radius, y: center.y };
  return {
    x: center.x + (dx / len) * radius,
    y: center.y + (dy / len) * radius,
  };
}

export type Side = 'left' | 'right' | 'top' | 'bottom';

/**
 * The side of `from` that faces `to`, picked from the dominant axis of the
 * offset. Used to give a floating edge a sensible curve direction.
 */
export function dominantSide(from: Point, to: Point): Side {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? 'right' : 'left';
  return dy >= 0 ? 'bottom' : 'top';
}
