import { describe, it, expect } from 'vitest';
import { initialEmitSet, declaredEmitPaths, DECLARED_EMIT_PATHS_KEY } from '../convert';

describe('declaredEmitPaths / initialEmitSet', () => {
  it('reads the backend-embedded _declared_emit_paths metadata (the REAL served shape)', () => {
    // This is what the Explorer actually receives for a browsed-not-yet-run
    // composite: composite_state_views._embed_declared_emit_paths /
    // composite_resolve.declared_emit_paths embed the composite's
    // `emitters=[...]` declaration directly into the state tree — there is
    // NO `emitter`/`emitter_<i>` step node (install_default_emitters only
    // runs on the run-execution path, not this browse/view path).
    const state = {
      global_time: 0,
      bulk: {},
      listeners: { mass: {} },
      [DECLARED_EMIT_PATHS_KEY]: ['global_time', 'bulk', 'listeners'],
    };
    // global_time is excluded from the returned set — it's always emitted
    // for the time axis, not a real observable toggle.
    expect(declaredEmitPaths(state)).toEqual(['bulk', 'listeners']);
    expect(initialEmitSet(state)).toEqual(new Set(['bulk', 'listeners']));
  });

  it('falls back to topLevelStorePaths when no declared paths and no emitter step', () => {
    const state = {
      biomodel_id: 'BIOMD0000000001',
      results: { copasi: {}, tellurium: {} },
      sim: { _type: 'process', address: 'local:Sim' },
    };
    expect(declaredEmitPaths(state)).toEqual([]);
    expect(initialEmitSet(state)).toEqual(new Set(['biomodel_id', 'results']));
  });

  it('unwraps a {state: ...} envelope, like topLevelStorePaths', () => {
    const doc = {
      state: {
        bulk: {},
        listeners: {},
        [DECLARED_EMIT_PATHS_KEY]: ['bulk', 'listeners'],
      },
    };
    expect(declaredEmitPaths(doc)).toEqual(['bulk', 'listeners']);
  });

  it('returns [] for empty or missing state', () => {
    expect(declaredEmitPaths({})).toEqual([]);
    expect(declaredEmitPaths(null)).toEqual([]);
    expect(initialEmitSet(null)).toEqual(new Set());
  });

  it('_declared_emit_paths never leaks into topLevelStorePaths (not mistaken for a store)', async () => {
    const { topLevelStorePaths } = await import('../convert');
    const state = {
      bulk: {},
      [DECLARED_EMIT_PATHS_KEY]: ['bulk'],
    };
    expect(topLevelStorePaths(state)).toEqual(['bulk']);
  });

  describe('legacy fallback: installed emitter step node scan', () => {
    // Only reachable when the served state has NO `_declared_emit_paths`
    // metadata but DOES carry an installed `emitter`/`emitter_<i>` step (a
    // state that went through the run-execution `install_default_emitters`
    // path — e.g. an already-run composite re-browsed from its result).
    it('extracts declared emit paths from an installed emitter step node', () => {
      const state = {
        bulk: {},
        unique: {},
        listeners: { mass: {} },
        emitter: {
          _type: 'step',
          address: 'local:ParquetEmitter',
          config: { emit: { bulk: 'node', listeners_mass: 'node', global_time: 'node' } },
          inputs: {
            bulk: ['bulk'],
            listeners_mass: ['listeners', 'mass'],
            global_time: ['global_time'],
          },
        },
      };
      expect(declaredEmitPaths(state)).toEqual(['bulk', 'listeners/mass']);
      expect(initialEmitSet(state)).toEqual(new Set(['bulk', 'listeners/mass']));
    });

    it('numbered emitter_<i> nodes also count as declared-emit sources', () => {
      const state = {
        bulk: {},
        emitter_1: {
          _type: 'step',
          address: 'local:RAMEmitter',
          config: { emit: { bulk: 'node' } },
          inputs: { bulk: ['bulk'] },
        },
      };
      expect(declaredEmitPaths(state)).toEqual(['bulk']);
    });
  });
});
