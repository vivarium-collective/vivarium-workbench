import { describe, it, expect } from 'vitest';
import { circleAnchor, dominantSide } from '../edges/geometry';

describe('circleAnchor', () => {
  it('returns the circumference point toward a point to the right', () => {
    const p = circleAnchor({ x: 0, y: 0 }, 10, { x: 100, y: 0 });
    expect(p.x).toBeCloseTo(10);
    expect(p.y).toBeCloseTo(0);
  });

  it('returns the circumference point toward a point below-left', () => {
    // Direction (-1,-1) normalised, scaled by radius √2 → (-1,-1).
    const p = circleAnchor({ x: 0, y: 0 }, Math.SQRT2, { x: -5, y: -5 });
    expect(p.x).toBeCloseTo(-1);
    expect(p.y).toBeCloseTo(-1);
  });

  it('always lands exactly on the circumference', () => {
    const c = { x: 30, y: 40 };
    const p = circleAnchor(c, 7, { x: 1000, y: -200 });
    expect(Math.hypot(p.x - c.x, p.y - c.y)).toBeCloseTo(7);
  });

  it('falls back to a deterministic on-circle point when toward === center', () => {
    const p = circleAnchor({ x: 5, y: 5 }, 3, { x: 5, y: 5 });
    expect(Number.isNaN(p.x)).toBe(false);
    expect(Number.isNaN(p.y)).toBe(false);
    expect(Math.hypot(p.x - 5, p.y - 5)).toBeCloseTo(3);
  });
});

describe('dominantSide', () => {
  it('right when the target is mostly to the right', () => {
    expect(dominantSide({ x: 0, y: 0 }, { x: 100, y: 10 })).toBe('right');
  });

  it('left when the target is mostly to the left', () => {
    expect(dominantSide({ x: 0, y: 0 }, { x: -100, y: 10 })).toBe('left');
  });

  it('bottom when the target is mostly below', () => {
    expect(dominantSide({ x: 0, y: 0 }, { x: 10, y: 100 })).toBe('bottom');
  });

  it('top when the target is mostly above', () => {
    expect(dominantSide({ x: 0, y: 0 }, { x: 10, y: -100 })).toBe('top');
  });
});
