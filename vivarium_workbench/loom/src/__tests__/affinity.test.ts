import { describe, it, expect } from 'vitest';
import type { Node } from '@xyflow/react';
import { isNoiseKey, storeKeysForProcess, isBookkeepingProcess } from '../layouts/affinity';

function proc(label: string, inputs: Record<string, string>, outputs: Record<string, string> = {}): Node {
  return {
    id: label, type: 'process', position: { x: 0, y: 0 },
    data: {
      label, nodeType: 'process', processType: 'step', address: 'a', config: {},
      path: ['agents', '0', label], inputPorts: Object.keys(inputs), outputPorts: Object.keys(outputs),
      inputPortsSchema: inputs, outputPortsSchema: outputs,
    },
  } as unknown as Node;
}

describe('isNoiseKey', () => {
  it('rejects process-private bookkeeping stores', () => {
    for (const k of ['_layer_token_7', 'next_update_time', 'process.foo',
                     'process_state.dnaa_hydrolysis', 'request', 'timestep',
                     'global_time', 'pinned_flux_targets', 'allocate.ecoli-x']) {
      expect(isNoiseKey(k)).toBe(true);
    }
  });

  it('keeps real biological stores', () => {
    for (const k of ['bulk', 'listeners', 'unique.RNA', 'unique.active_ribosome',
                     'boundary', 'environment', 'unique.promoter']) {
      expect(isNoiseKey(k)).toBe(false);
    }
  });
});

describe('storeKeysForProcess', () => {
  it('truncates keys to depth 2 and counts port multiplicity', () => {
    const keys = storeKeysForProcess(
      proc('p', { a: 'unique.RNA.foo', b: 'unique.RNA.bar', c: 'bulk' }),
    );
    expect(keys.get('unique.RNA')).toBe(2);
    expect(keys.get('bulk')).toBe(1);
  });

  it('merges input and output ports', () => {
    const keys = storeKeysForProcess(proc('p', { a: 'bulk' }, { b: 'bulk', c: 'listeners' }));
    expect(keys.get('bulk')).toBe(2);
    expect(keys.get('listeners')).toBe(1);
  });

  it('drops noise keys entirely', () => {
    const keys = storeKeysForProcess(proc('p', { a: 'bulk', b: '_layer_token_3', c: 'timestep' }));
    expect([...keys.keys()]).toEqual(['bulk']);
  });
});

describe('storeKeysForProcess — relative-navigation wire targets', () => {
  it('never yields the meaningless "." key for a bare ".." (parent-scope) target', () => {
    // Real fixture case: v2ecoli-baseline's `division` process wires its
    // `agents` output port to `['..']`, which convert.ts joins to the
    // string '..'. That must not surface as a cluster key at all.
    const keys = storeKeysForProcess(proc('division', {}, { agents: '..' }));
    expect(keys.has('.')).toBe(false);
    expect(keys.size).toBe(0);
  });

  it('drops pure dot-navigation targets while keeping real keys from the same process', () => {
    const keys = storeKeysForProcess(
      proc('division', { bulk: 'bulk' }, { agents: '..', environment: '.' }),
    );
    expect(keys.has('.')).toBe(false);
    expect(keys.has('')).toBe(false);
    expect(keys.get('bulk')).toBe(1);
    expect([...keys.keys()]).toEqual(['bulk']);
  });
});

describe('isBookkeepingProcess', () => {
  it('matches what defaultHiddenIds already hides', () => {
    expect(isBookkeepingProcess('unique_update_4')).toBe(true);
    expect(isBookkeepingProcess('allocator_2')).toBe(true);
    expect(isBookkeepingProcess('rnap_data_listener')).toBe(true);
    expect(isBookkeepingProcess('ecoli-transcript-initiation')).toBe(false);
  });
});
