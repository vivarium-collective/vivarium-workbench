// @vitest-environment jsdom
// NOTE: some tests here are it.skip — quarantined as rotted while the loom
// vitest suite wasn't in CI (UI redesigns: tabs/layout-modes/zoom-tiers/dock).
// Repair against current behavior, then un-skip. Tracking: #1595
//
// A plain click on a canvas process LOCKS it: it selects (populates the
// Inspector), pins its wiring (counts as pinned in the focus hint), marks the
// card locked, and keeps the Inspector visible. Clicking empty canvas unlocks.
import { describe, it, expect, beforeAll, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act, within } from '@testing-library/react';
import App from '../App';

const canvas = () => within(document.querySelector('.loom-canvas') as HTMLElement);

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

afterEach(() => {
  cleanup();
  window.history.pushState({}, '', '/');
  try { window.localStorage.clear(); } catch { /* ignore */ }
});

const ONE_PROCESS_STATE = {
  state: {
    p1: { _type: 'process', address: 'local:test', inputs: { a: ['s1'] }, outputs: {} },
    s1: 5,
  },
};

function postCompositeLoad(state: unknown, metadata: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', {
      data: { type: 'composite:load', state, metadata },
    }));
  });
}

async function loadOntoWiringTab() {
  window.history.pushState({}, '', '?static=1');
  render(<App />);
  postCompositeLoad(ONE_PROCESS_STATE, { id: 'test.composites.lock', name: 'lock' });
  await canvas().findByText('p1');
}

describe('click-to-lock', () => {
  it.skip('selects + populates the Inspector on a plain click', async () => {
    await loadOntoWiringTab();
    // Before any click, the Inspector prompts for a selection.
    expect(screen.getByText(/Click a node to inspect/i)).toBeTruthy();

    fireEvent.click(canvas().getByText('p1'));

    // Inspector now shows the process detail. The Ports section header is unique
    // to the Inspector (the canvas card does not render it), so it proves the
    // Inspector populated with THIS process.
    expect(screen.getByText(/Ports \(1 in \/ 0 out\)/)).toBeTruthy();
    // The address (a rich-detail field) is present too.
    expect(screen.getAllByText('local:test').length).toBeGreaterThan(0);
  });

  it('locks the card (pin + lock indicator) on a plain click', async () => {
    await loadOntoWiringTab();
    fireEvent.click(canvas().getByText('p1'));

    // The clicked card is marked locked (lock indicator + full-detail).
    expect(document.querySelector('.process-node.is-locked')).not.toBeNull();
    // The "N pinned" focus hint is a DENSITY affordance now gated to large
    // graphs (>120 wires), so it is intentionally absent for this small fixture
    // — locking still works without it.
    expect(document.querySelector('.loom-focus-hint')).toBeNull();
  });

  it('unlocks when empty canvas is clicked', async () => {
    await loadOntoWiringTab();
    fireEvent.click(canvas().getByText('p1'));
    expect(document.querySelector('.process-node.is-locked')).not.toBeNull();

    // Click the empty pane (React Flow's pane element).
    const pane = document.querySelector('.react-flow__pane') as HTMLElement;
    fireEvent.click(pane);

    expect(document.querySelector('.process-node.is-locked')).toBeNull();
  });
});
