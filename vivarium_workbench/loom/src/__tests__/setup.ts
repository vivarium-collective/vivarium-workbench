// Shared jsdom shims for the loom unit tests. jsdom provides neither
// ResizeObserver nor a canvas backend; React Flow (and the semantic-zoom /
// inspector panels) touch both, and without these the component renders throw
// mid-tree and the UI assertions become silently unreachable. These are logic
// tests, not pixel tests, so a no-op canvas context is enough.

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
}

// A permissive 2D/placeholder context: any method is a no-op, the few that must
// return a value do so. A Proxy keeps it robust to whichever calls a component
// happens to make.
function fakeContext(el: HTMLCanvasElement): unknown {
  return new Proxy(
    {},
    {
      get(_t, prop) {
        if (prop === 'canvas') return el;
        if (prop === 'measureText') return () => ({ width: 0 });
        if (prop === 'getImageData') return () => ({ data: new Uint8ClampedArray(4), width: 1, height: 1 });
        if (prop === 'createLinearGradient' || prop === 'createRadialGradient') {
          return () => ({ addColorStop: () => {} });
        }
        if (prop === 'createPattern') return () => ({});
        if (prop === 'getContextAttributes') return () => ({});
        return () => {};
      },
    },
  );
}

const proto = HTMLCanvasElement.prototype as unknown as {
  getContext: (this: HTMLCanvasElement) => unknown;
  toDataURL: () => string;
};
proto.getContext = function () {
  return fakeContext(this);
};
proto.toDataURL = () => 'data:image/png;base64,';
