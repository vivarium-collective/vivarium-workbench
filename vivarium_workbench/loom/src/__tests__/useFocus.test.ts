import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useFocus } from '../hooks/useFocus';

describe('useFocus', () => {
  it('starts empty', () => {
    const { result } = renderHook(() => useFocus());
    expect(result.current.ctx.focused.size).toBe(0);
    expect(result.current.ctx.pinned.size).toBe(0);
  });

  it('tracks hover and clears it', () => {
    const { result } = renderHook(() => useFocus());
    act(() => result.current.hover('p1'));
    expect(result.current.ctx.focused.has('p1')).toBe(true);
    act(() => result.current.hover(null));
    expect(result.current.ctx.focused.size).toBe(0);
  });

  it('keeps a selection while hover moves away', () => {
    const { result } = renderHook(() => useFocus());
    act(() => result.current.select('p1'));
    act(() => result.current.hover('p2'));
    expect(result.current.ctx.focused.has('p1')).toBe(true);
    expect(result.current.ctx.focused.has('p2')).toBe(true);
  });

  it('accumulates and removes pins', () => {
    const { result } = renderHook(() => useFocus());
    act(() => result.current.togglePin('p1'));
    act(() => result.current.togglePin('p2'));
    expect(result.current.ctx.pinned.size).toBe(2);
    act(() => result.current.togglePin('p1'));
    expect([...result.current.ctx.pinned]).toEqual(['p2']);
  });

  it('clear() drops hover, selection and pins', () => {
    const { result } = renderHook(() => useFocus());
    act(() => { result.current.hover('p1'); result.current.select('p2'); });
    act(() => result.current.togglePin('p3'));
    act(() => result.current.clear());
    expect(result.current.ctx.focused.size).toBe(0);
    expect(result.current.ctx.pinned.size).toBe(0);
  });

  it('keeps ctx identity stable across re-renders that change nothing', () => {
    // The edge filter memoizes on ctx; a fresh Set every render would make the
    // memo a no-op and re-filter several hundred edges per mouse move.
    const { result, rerender } = renderHook(() => useFocus());
    const first = result.current.ctx;
    rerender();
    expect(result.current.ctx).toBe(first);
    act(() => result.current.hover('p1'));
    expect(result.current.ctx).not.toBe(first);
    const hovered = result.current.ctx;
    rerender();
    expect(result.current.ctx).toBe(hovered);
  });

  it('re-hovering the same node does not mint a new ctx', () => {
    const { result } = renderHook(() => useFocus());
    act(() => result.current.hover('p1'));
    const first = result.current.ctx;
    act(() => result.current.hover('p1'));
    expect(result.current.ctx).toBe(first);
  });
});
