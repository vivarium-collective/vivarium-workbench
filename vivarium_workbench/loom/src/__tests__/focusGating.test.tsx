// @vitest-environment jsdom
//
// App-level regression tests for the two review fixes on top of Task 6:
//   1. onNodeMouseEnter/onNodeMouseLeave are only wired up in modes that cull
//      edges by focus, so hierarchy mode pays nothing for hover tracking it
//      never uses (Finding 1).
//   2. `focus.clear()` runs on every composite:load, and a pinned node that
//      gets explicitly hidden is pruned from the pin set (Finding 2).
import { Profiler } from 'react';
import { describe, it, expect, beforeAll, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import App from '../App';

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
});

function postCompositeLoad(state: unknown, metadata: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', {
      data: { type: 'composite:load', state, metadata },
    }));
  });
}

/** One process ('p1') wired to one store ('s1') — enough for both a real
 *  process-node DOM element and a real wire edge to exist. */
const ONE_PROCESS_STATE = {
  state: {
    p1: { _type: 'process', address: 'local:test', inputs: { a: ['s1'] }, outputs: {} },
    s1: 5,
  },
};

async function loadOntoWiringTab(metadata: Record<string, unknown>) {
  // static=1 disables onlyRenderVisibleElements, so nodes render regardless
  // of jsdom's zero-size viewport.
  window.history.pushState({}, '', '?static=1');
  render(<App />);
  postCompositeLoad(ONE_PROCESS_STATE, metadata);
  fireEvent.click(screen.getByRole('button', { name: /^Wiring$/i }));
  const label = await screen.findByText('p1');
  return label;
}

describe('focus-driven edge culling — App wiring', () => {
  it('hierarchy mode (default) does not re-render on node hover', async () => {
    let renders = 0;
    render(
      <Profiler id="root" onRender={() => { renders += 1; }}>
        <App />
      </Profiler>,
    );
    window.history.pushState({}, '', '?static=1');
    postCompositeLoad(ONE_PROCESS_STATE, { id: 'test.composites.hover-a', name: 'hover-a' });
    fireEvent.click(screen.getByRole('button', { name: /^Wiring$/i }));
    await screen.findByText('p1');

    // Default mode is hierarchy — no focus hint should even be present.
    expect(document.querySelector('.loom-focus-hint')).toBeNull();

    const before = renders;
    fireEvent.mouseEnter(screen.getByText('p1'));
    fireEvent.mouseLeave(screen.getByText('p1'));
    // Nothing wired up onNodeMouseEnter/Leave in hierarchy mode — no re-render.
    expect(renders).toBe(before);
  });

  it('process-column mode reveals wiring on hover (handlers ARE wired)', async () => {
    const label = await loadOntoWiringTab({ id: 'test.composites.hover-b', name: 'hover-b' });
    const select = screen.getByTitle('Layout mode') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'process-column' } });
    await screen.findByText('p1');

    expect(document.querySelector('.loom-focus-hint')?.textContent)
      .toMatch(/hover to reveal wiring/i);

    fireEvent.mouseEnter(screen.getByText('p1'));
    expect(document.querySelector('.loom-focus-hint')?.textContent)
      .toMatch(/showing wiring for 1 node/i);

    fireEvent.mouseLeave(screen.getByText('p1'));
    expect(document.querySelector('.loom-focus-hint')?.textContent)
      .toMatch(/hover to reveal wiring/i);
    void label;
  });

  it('pinning then loading a NEW composite clears the pin (Finding 2a)', async () => {
    await loadOntoWiringTab({ id: 'test.composites.pin-a', name: 'pin-a' });
    const select = screen.getByTitle('Layout mode') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'process-column' } });
    await screen.findByText('p1');

    fireEvent.click(screen.getByText('p1'), { shiftKey: true });
    expect(document.querySelector('.loom-focus-hint')?.textContent)
      .toMatch(/1 pinned/);

    // A new composite loads (id and node ids may collide across composites —
    // this must not leave the pin dangling from the previous one). Flush the
    // async layout re-run rather than re-querying for 'p1' by text: React
    // Flow transiently re-keys nodes across the reload, so a text query can
    // catch it mid-transition — the hint text is what this behavior is about.
    postCompositeLoad(ONE_PROCESS_STATE, { id: 'test.composites.pin-a-v2', name: 'pin-a-v2' });
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });

    expect(document.querySelector('.loom-focus-hint')?.textContent)
      .not.toMatch(/pinned/);
    expect(document.querySelector('.loom-focus-hint')?.textContent)
      .toMatch(/hover to reveal wiring/i);
  });

  it('hiding a pinned node prunes the pin instead of stranding it (Finding 2b)', async () => {
    await loadOntoWiringTab({ id: 'test.composites.pin-b', name: 'pin-b' });
    const select = screen.getByTitle('Layout mode') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'process-column' } });
    await screen.findByText('p1');

    fireEvent.click(screen.getByText('p1'), { shiftKey: true });
    expect(document.querySelector('.loom-focus-hint')?.textContent)
      .toMatch(/1 pinned/);

    // Un-check p1 in the sidebar's Processes tab — the ONLY other way to hide
    // a node besides the (now unreachable) canvas.
    fireEvent.click(screen.getByRole('button', { name: /^processes$/i }));
    const checkbox = await screen.findByRole('checkbox', { name: /p1/i });
    fireEvent.click(checkbox);

    // The pin must not survive hiding its own node — else the hint keeps
    // claiming "(1 pinned)" over a canvas with nothing left to un-pin it from.
    expect(document.querySelector('.loom-focus-hint')?.textContent)
      .not.toMatch(/pinned/);
  });
});
