import { describe, it, expect } from 'vitest';
import { initialEmitSet, declaredEmitPaths } from '../convert';

describe('declaredEmitPaths / initialEmitSet', () => {
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
    // global_time is excluded — it's always emitted for the time axis, not a
    // real observable toggle.
    expect(declaredEmitPaths(state)).toEqual(['bulk', 'listeners/mass']);
    expect(initialEmitSet(state)).toEqual(new Set(['bulk', 'listeners/mass']));
  });

  it('falls back to topLevelStorePaths when no emitter step is declared', () => {
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
        emitter: {
          _type: 'step',
          address: 'local:ParquetEmitter',
          config: { emit: { bulk: 'node' } },
          inputs: { bulk: ['bulk'] },
        },
      },
    };
    expect(declaredEmitPaths(doc)).toEqual(['bulk']);
  });

  it('returns [] for empty or missing state', () => {
    expect(declaredEmitPaths({})).toEqual([]);
    expect(declaredEmitPaths(null)).toEqual([]);
    expect(initialEmitSet(null)).toEqual(new Set());
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
