// @vitest-environment jsdom
// Tests for SetupRunPanel — migrated from ConfigurePanel.test.tsx when
// ConfigurePanel was merged into SetupRunPanel (Tasks 5+6).
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { SetupRunPanel } from '../panels/SetupRunPanel';

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

/** Minimal required props beyond the per-test ones. */
const BASE_PROPS = {
  emitSet: new Set<string>(),
  onCompleted: () => {},
  onApplied: () => {},
};

describe('SetupRunPanel', () => {
  it('renders a textarea for list[string], pre-filled from default', () => {
    render(
      <SetupRunPanel
        {...BASE_PROPS}
        compositeId="some.composite.id"
        parameters={PARAMS}
        overrides={{}}
      />
    );
    // Labels include the parameter name.
    expect(screen.getByText((t) => t.includes('biomodel_ids'))).toBeTruthy();
    // Textarea pre-filled from the default list, one per line.
    const ta = screen.getByLabelText(/biomodel_ids/i) as HTMLTextAreaElement;
    expect(ta.tagName).toBe('TEXTAREA');
    expect(ta.value).toBe('BIOMD0000000001');
  });

  it('"Preview wiring" parses the textarea, POSTs to composite-resolve, calls onApplied', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetchOk({ state: { fresh: true }, parameters: PARAMS }) as any,
    );
    const onApplied = vi.fn();
    render(
      <SetupRunPanel
        {...BASE_PROPS}
        compositeId="x.compare-biomodel"
        parameters={PARAMS}
        overrides={{}}
        onApplied={onApplied}
      />
    );
    const ta = screen.getByLabelText(/biomodel_ids/i) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'BIOMD0000000001\nBIOMD0000000005\n' } });
    fireEvent.click(screen.getByText('Preview wiring'));

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

  it('renders a <select> dropdown for a param with choices, defaulting to its default', () => {
    const params = {
      emitter: {
        type: 'string' as const,
        default: 'parquet',
        choices: ['parquet', 'sqlite', 'xarray', 'null'],
        description: 'Observation sink.',
      },
    };
    render(
      <SetupRunPanel
        {...BASE_PROPS}
        compositeId="x.baseline"
        parameters={params}
        overrides={{}}
      />
    );
    const sel = screen.getByLabelText(/emitter/i) as HTMLSelectElement;
    expect(sel.tagName).toBe('SELECT');
    expect(sel.value).toBe('parquet');
    // One <option> per choice.
    const opts = Array.from(sel.querySelectorAll('option')).map((o) => o.value);
    expect(opts).toEqual(['parquet', 'sqlite', 'xarray', 'null']);
  });

  it('choices dropdown: changing selection feeds the chosen value to onApplied', async () => {
    vi.stubGlobal('fetch', mockFetchOk({ state: {}, parameters: {} }) as any);
    const onApplied = vi.fn();
    const params = {
      emitter: {
        type: 'string' as const,
        default: 'parquet',
        choices: ['parquet', 'sqlite', 'xarray', 'null'],
      },
    };
    render(
      <SetupRunPanel
        {...BASE_PROPS}
        compositeId="x.baseline"
        parameters={params}
        overrides={{}}
        onApplied={onApplied}
      />
    );
    const sel = screen.getByLabelText(/emitter/i) as HTMLSelectElement;
    fireEvent.change(sel, { target: { value: 'sqlite' } });
    fireEvent.click(screen.getByText('Preview wiring'));
    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    expect(onApplied.mock.calls[0][0]).toEqual({ emitter: 'sqlite' });
  });

  it('no parameters → no parameter inputs, Run button present', () => {
    render(
      <SetupRunPanel
        {...BASE_PROPS}
        compositeId="x"
        parameters={{}}
        overrides={{}}
      />
    );
    // No "Preview wiring" button when there are no parameters.
    expect(screen.queryByText(/preview wiring/i)).toBeNull();
    // The Run button is always present (in the h3 heading and the button).
    expect(screen.getAllByText('Run').length).toBeGreaterThan(0);
  });

  it('readOnly disables Run and Preview and makes no fetch calls', () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy as any);
    render(
      <SetupRunPanel
        {...BASE_PROPS}
        compositeId="some.composite.id"
        parameters={PARAMS}
        overrides={{}}
        readOnly
      />
    );
    // Form still renders (parameter label present)…
    expect(screen.getByText((t) => t.includes('biomodel_ids'))).toBeTruthy();
    // …but Run and Preview are disabled.
    expect((screen.getByRole('button', { name: /^Run$/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /Preview wiring/i }) as HTMLButtonElement).disabled).toBe(true);
    // A read-only note is shown.
    expect(screen.getByText(/read-only|live dashboard/i)).toBeTruthy();
    // No network calls happened on render.
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
