// @vitest-environment jsdom
// DockContainer manages left/right dock zones around a canvas. Each panel can be
// docked left↔right via a ⇄ header button and collapsed to a thin edge tab;
// placement + zone widths persist to one localStorage blob.

import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import { DockContainer, type DockPanelSpec } from '../panels/DockContainer';

/** jsdom's localStorage here is partial; install a Map-backed one and hand the
 *  map back so a test can inspect (and a re-mount can read) what was written. */
function installStorage(): Map<string, string> {
  const m = new Map<string, string>();
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k: string) => (m.has(k) ? (m.get(k) as string) : null),
      setItem: (k: string, v: string) => { m.set(k, String(v)); },
      removeItem: (k: string) => { m.delete(k); },
      clear: () => { m.clear(); },
      key: (i: number) => [...m.keys()][i] ?? null,
      get length() { return m.size; },
    } as Storage,
  });
  return m;
}

let store: Map<string, string>;
beforeEach(() => { store = installStorage(); });
afterEach(cleanup);

const PANELS: DockPanelSpec[] = [
  { id: 'process', title: 'Processes', defaultSide: 'left', render: () => <div>process-body</div> },
  { id: 'inspector', title: 'Inspector', defaultSide: 'right', render: () => <div>inspector-body</div> },
  { id: 'nodes', title: 'Nodes', defaultSide: 'right', render: () => <div>nodes-body</div> },
];

function dockState() {
  const raw = store.get('loom.dock.v3');
  return raw ? JSON.parse(raw) : null;
}
const leftZone = () => document.querySelector('.loom-dock-zone-left') as HTMLElement | null;
const rightZone = () => document.querySelector('.loom-dock-zone-right') as HTMLElement | null;

describe('DockContainer', () => {
  it('places Process left, Inspector + Nodes right, canvas in the center', () => {
    render(<DockContainer panels={PANELS}><div>canvas</div></DockContainer>);
    expect(within(leftZone()!).getByText('Processes')).toBeTruthy();
    expect(within(rightZone()!).getByText('Inspector')).toBeTruthy();
    expect(within(rightZone()!).getByText('Nodes')).toBeTruthy();
    // The canvas child lives in the center, not in either dock zone.
    const center = document.querySelector('.loom-dock-center') as HTMLElement;
    expect(within(center).getByText('canvas')).toBeTruthy();
    // Panel bodies render.
    expect(screen.getByText('process-body')).toBeTruthy();
  });

  it('docks a panel to the other side via the ⇄ button (and persists it)', () => {
    render(<DockContainer panels={PANELS}><div>canvas</div></DockContainer>);
    // Process starts on the left; move it right.
    fireEvent.click(screen.getByRole('button', { name: /dock processes right/i }));
    // It now lives in the right zone; the left zone is empty (renders nothing).
    expect(within(rightZone()!).getByText('Processes')).toBeTruthy();
    expect(leftZone()).toBeNull();
    // Persisted.
    expect(dockState().panels.process.side).toBe('right');
  });

  it('collapses a panel to a thin edge tab and expands it again', () => {
    render(<DockContainer panels={PANELS}><div>canvas</div></DockContainer>);
    expect(screen.getByText('inspector-body')).toBeTruthy();
    // Collapse Inspector.
    fireEvent.click(screen.getByRole('button', { name: /collapse inspector/i }));
    expect(screen.queryByText('inspector-body')).toBeNull();
    expect(dockState().panels.inspector.collapsed).toBe(true);
    // The collapsed panel is a labeled tab that expands on click.
    const tab = screen.getByRole('button', { name: /expand inspector/i });
    expect(tab.className).toContain('loom-dock-tab');
    fireEvent.click(tab);
    expect(screen.getByText('inspector-body')).toBeTruthy();
    expect(dockState().panels.inspector.collapsed).toBe(false);
  });

  it('restores persisted placement on a fresh mount', () => {
    // Pre-seed: process docked right + collapsed.
    store.set('loom.dock.v3', JSON.stringify({
      panels: {
        process: { side: 'right', collapsed: true },
        inspector: { side: 'right', collapsed: false },
        nodes: { side: 'left', collapsed: false },
      },
      widths: { left: 300, right: 300 },
    }));
    render(<DockContainer panels={PANELS}><div>canvas</div></DockContainer>);
    // Process starts collapsed (a tab) on the RIGHT; Nodes moved to the LEFT.
    expect(within(rightZone()!).getByRole('button', { name: /expand processes/i })).toBeTruthy();
    expect(within(leftZone()!).getByText('Nodes')).toBeTruthy();
    expect(screen.queryByText('process-body')).toBeNull();
  });

  it('ignores a stale persisted id the specs no longer declare', () => {
    store.set('loom.dock.v3', JSON.stringify({
      panels: { ghost: { side: 'left', collapsed: false }, process: { side: 'right', collapsed: false } },
      widths: { left: 300, right: 300 },
    }));
    render(<DockContainer panels={PANELS}><div>canvas</div></DockContainer>);
    // No phantom "ghost" panel; process honored the saved right side.
    expect(screen.queryByText('ghost')).toBeNull();
    expect(within(rightZone()!).getByText('Processes')).toBeTruthy();
  });
});
