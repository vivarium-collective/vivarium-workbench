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

// Composites emit type names ('integer','boolean','list','map') that differ
// from the code's older spellings ('int','bool','list[string]'). A mismatch
// sends the raw String() of the field to the backend — seed="0" crashed
// RandomState, config_overrides="[object Object]" crashed .items(). These lock
// the type-name normalization + the map JSON round-trip.
describe('param casting (type-name normalization + crash fix)', () => {
  const mk = (type: string, def: unknown) => ({ type, default: def, description: '' } as any);

  it('integer / boolean / list cast correctly under the composite type names', () => {
    expect(_castFormValue(mk('integer', 0), '0')).toBe(0);         // was crashing as "0"
    expect(_castFormValue(mk('integer', 0), '42')).toBe(42);
    expect(_castFormValue(mk('boolean', false), 'false')).toBe(false);
    expect(_castFormValue(mk('boolean', false), 'true')).toBe(true);
    expect(_castFormValue(mk('boolean', false), false)).toBe(false);
    expect(_castFormValue(mk('list', []), 'a\nb')).toEqual(['a', 'b']);
  });

  it('_initialValue seeds fields for the composite type names', () => {
    expect(_initialValue(mk('integer', 0), undefined)).toBe('0');
    expect(_initialValue(mk('boolean', false), undefined)).toBe(false);
    expect(_initialValue(mk('list', ['x', 'y']), undefined)).toBe('x\ny');
  });

  it('map params serialize/parse as JSON, never "[object Object]"', () => {
    const mapDef = mk('map', {});
    expect(_initialValue(mk('map', { a: 1 }), undefined)).toBe('{"a":1}');
    expect(_initialValue(mapDef, undefined)).toBe('{}');
    expect(String(_initialValue(mapDef, { b: 2 }))).not.toContain('[object Object]');
    expect(_castFormValue(mapDef, '')).toEqual({});
    expect(_castFormValue(mapDef, '[object Object]')).toEqual({});
    expect(_castFormValue(mapDef, '{"x":[1,2]}')).toEqual({ x: [1, 2] });
    expect(_castFormValue(mapDef, 'not json')).toEqual({});
  });
});
