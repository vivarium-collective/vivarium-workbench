// @vitest-environment jsdom
// Tests for SetupRunPanel — migrated from ConfigurePanel.test.tsx when
// ConfigurePanel was merged into SetupRunPanel (Tasks 5+6).
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import {
  SetupRunPanel, _initialValue, _castFormValue,
} from '../panels/SetupRunPanel';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

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
    expect(screen.getByText((t) => t.includes('biomodel_ids'))).toBeTruthy();
    const ta = screen.getByLabelText(/biomodel_ids/i) as HTMLTextAreaElement;
    expect(ta.tagName).toBe('TEXTAREA');
    expect(ta.value).toBe('BIOMD0000000001');
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
    const opts = Array.from(sel.querySelectorAll('option')).map((o) => o.value);
    expect(opts).toEqual(['parquet', 'sqlite', 'xarray', 'null']);
  });

  it('no parameters → Run button present', () => {
    render(
      <SetupRunPanel
        {...BASE_PROPS}
        compositeId="x"
        parameters={{}}
        overrides={{}}
      />
    );
    expect(screen.getAllByText('Run').length).toBeGreaterThan(0);
  });

  it('readOnly disables Run and makes no fetch calls', () => {
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
    // …but Run is disabled.
    expect((screen.getByRole('button', { name: /^Run$/i }) as HTMLButtonElement).disabled).toBe(true);
    // A read-only note is shown.
    expect(screen.getByText(/read-only|live dashboard/i)).toBeTruthy();
    // No network calls happened on render.
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

// Map/dict/object params are edited as JSON text and MUST cast back to an
// object — a bare string reaches generators like baseline's
// `config_overrides.items()` and crashes with "'str' object has no attribute
// 'items'". These lock the fix.
describe('map/object param casting', () => {
  const mapDef = { type: 'map' as const, default: {}, description: '' };

  it('_initialValue serializes an object to JSON, never "[object Object]"', () => {
    expect(_initialValue({ type: 'map' as const, default: { a: 1 }, description: '' }, undefined))
      .toBe('{"a":1}');
    expect(_initialValue(mapDef, undefined)).toBe('{}');   // empty map default
    expect(String(_initialValue(mapDef, { b: 2 }))).not.toContain('[object Object]');
  });

  it('_castFormValue turns map text back into an object (never a bare string)', () => {
    expect(_castFormValue(mapDef, '')).toEqual({});                 // empty → {}
    expect(_castFormValue(mapDef, '[object Object]')).toEqual({});  // legacy coercion → {}
    expect(_castFormValue(mapDef, '{"x":[1,2]}')).toEqual({ x: [1, 2] });
    expect(_castFormValue(mapDef, 'not json')).toEqual({});         // lenient fallback
  });
});
