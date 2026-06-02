// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { ConfigurePanel } from '../panels/ConfigurePanel';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function mockFetchOk(body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => body,
  });
}

const PARAMS = {
  biomodel_ids: {
    type: 'list[string]' as const,
    default: ['BIOMD0000000001'],
    description: 'BioModels ids, one per line.',
  },
};

describe('ConfigurePanel', () => {
  it('renders a textarea for list[string], pre-filled from default', () => {
    render(
      <ConfigurePanel
        compositeId="some.composite.id"
        parameters={PARAMS}
        overrides={{}}
        onApplied={() => {}}
      />
    );
    // Labels include the parameter name.
    expect(screen.getByText((t) => t.includes('biomodel_ids'))).toBeTruthy();
    // Textarea pre-filled from the default list, one per line.
    const ta = screen.getByLabelText(/biomodel_ids/i) as HTMLTextAreaElement;
    expect(ta.tagName).toBe('TEXTAREA');
    expect(ta.value).toBe('BIOMD0000000001');
  });

  it('Apply parses the textarea, POSTs to composite-resolve, calls onApplied', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetchOk({ state: { fresh: true }, parameters: PARAMS }) as any,
    );
    const onApplied = vi.fn();
    render(
      <ConfigurePanel
        compositeId="x.compare-biomodel"
        parameters={PARAMS}
        overrides={{}}
        onApplied={onApplied}
      />
    );
    const ta = screen.getByLabelText(/biomodel_ids/i) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'BIOMD0000000001\nBIOMD0000000005\n' } });
    fireEvent.click(screen.getByText('Apply'));

    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    const [overrides, state] = onApplied.mock.calls[0];
    expect(overrides).toEqual({
      biomodel_ids: ['BIOMD0000000001', 'BIOMD0000000005'],
    });
    expect(state).toEqual({ fresh: true });

    // The fetch URL carries the JSON-encoded overrides.
    const calledUrl = (globalThis.fetch as any).mock.calls[0][0];
    expect(calledUrl).toContain('/api/composite-resolve');
    expect(calledUrl).toContain(encodeURIComponent('x.compare-biomodel'));
    expect(calledUrl).toContain(encodeURIComponent('["BIOMD0000000001","BIOMD0000000005"]'));
  });

  it('no parameters → empty-state message, no inputs', () => {
    render(
      <ConfigurePanel
        compositeId="x"
        parameters={{}}
        overrides={{}}
        onApplied={() => {}}
      />
    );
    expect(screen.getByText(/no parameters to configure/i)).toBeTruthy();
  });
});
