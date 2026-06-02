import { describe, it, expect } from 'vitest';
import { topLevelStorePaths } from '../convert';

describe('topLevelStorePaths', () => {
  it('returns top-level store keys, skipping process and step nodes', () => {
    const state = {
      biomodel_id: 'BIOMD0000000001',
      results: { copasi: {}, tellurium: {} },
      comparison: {},
      load: { _type: 'step', address: 'local:LoadBiomodelStep' },
      sim: { _type: 'process', address: 'local:Sim' },
    };
    expect(topLevelStorePaths(state)).toEqual([
      'biomodel_id', 'results', 'comparison',
    ]);
  });

  it('unwraps a {state: ...} envelope, like stateToReactFlow', () => {
    const doc = { state: { level: 1, proc: { _type: 'process' } } };
    expect(topLevelStorePaths(doc)).toEqual(['level']);
  });

  it('returns [] for empty or missing state', () => {
    expect(topLevelStorePaths({})).toEqual([]);
    expect(topLevelStorePaths(null)).toEqual([]);
  });
});
