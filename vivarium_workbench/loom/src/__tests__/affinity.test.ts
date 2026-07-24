import { describe, it, expect } from 'vitest';
import type { Node } from '@xyflow/react';
import { isNoiseKey, storeKeysForProcess, isBookkeepingProcess } from '../layouts/affinity';
import { stateToReactFlow } from '../convert';

/**
 * Build a process node the way the app really does — by running the real
 * `stateToReactFlow` over a minimal composite state — so the node carries
 * EXACTLY the port fields convert.ts attaches (raw joined `*PortsSchema` plus
 * the resolved absolute `*PortsTarget`). Wire targets are given as ARRAYS,
 * their true form; the joined string is a lossy display encoding and must
 * never be the input to clustering.
 *
 * The process lands at path `['agents','0',label]`, so its parent store — the
 * scope wire targets are relative to — is `agents.0`.
 */
function proc(
  label: string,
  inputs: Record<string, string[]>,
  outputs: Record<string, string[]> = {},
): Node {
  const state = {
    agents: {
      '0': {
        [label]: { _type: 'process', address: 'local:X', config: {}, inputs, outputs },
      },
    },
  };
  const { nodes } = stateToReactFlow(state);
  const found = nodes.find((n) => n.id === `agents.0.${label}`);
  if (!found) throw new Error(`test fixture did not produce a process node for ${label}`);
  return found as unknown as Node;
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

describe('convert.ts port target resolution', () => {
  it('attaches BOTH the raw joined schema and the resolved absolute target', () => {
    const node = proc('p', { a: ['unique', 'RNA'] }, { b: ['..', 'bulk'] });
    const data = node.data as any;
    // Raw joined form is unchanged — it feeds the port tooltips + Inspector.
    expect(data.inputPortsSchema).toEqual({ a: 'unique.RNA' });
    expect(data.outputPortsSchema).toEqual({ b: '...bulk' });
    // Resolved absolute form is new, and is what clustering consumes.
    expect(data.inputPortsTarget).toEqual({ a: 'agents.0.unique.RNA' });
    expect(data.outputPortsTarget).toEqual({ b: 'agents.bulk' });
  });

  it('resolves interior ".." with pop semantics, not by deleting the token', () => {
    const node = proc('p', { a: ['unique', '..', 'bulk'], b: ['bulk', '..'] });
    const data = node.data as any;
    expect(data.inputPortsTarget).toEqual({ a: 'agents.0.bulk', b: 'agents.0' });
  });
});

describe('storeKeysForProcess', () => {
  it('truncates keys to depth 2 and counts port multiplicity', () => {
    const keys = storeKeysForProcess(
      proc('p', { a: ['unique', 'RNA', 'foo'], b: ['unique', 'RNA', 'bar'], c: ['bulk'] }),
    );
    expect(keys.get('unique.RNA')).toBe(2);
    expect(keys.get('bulk')).toBe(1);
  });

  it('merges input and output ports', () => {
    const keys = storeKeysForProcess(
      proc('p', { a: ['bulk'] }, { b: ['bulk'], c: ['listeners'] }),
    );
    expect(keys.get('bulk')).toBe(2);
    expect(keys.get('listeners')).toBe(1);
  });

  it('drops noise keys entirely', () => {
    const keys = storeKeysForProcess(
      proc('p', { a: ['bulk'], b: ['_layer_token_3'], c: ['timestep'] }),
    );
    expect([...keys.keys()]).toEqual(['bulk']);
  });
});

describe('storeKeysForProcess — relative-navigation wire targets', () => {
  // Process parent scope is `agents.0` throughout (see `proc`).

  it('keys a plain in-scope target by its own name', () => {
    expect([...storeKeysForProcess(proc('p', { a: ['bulk'] }))]).toEqual([['bulk', 1]]);
  });

  it('keys a nested in-scope target at full depth', () => {
    expect([...storeKeysForProcess(proc('p', { a: ['unique', 'RNA'] }))])
      .toEqual([['unique.RNA', 1]]);
  });

  it('truncates a deeper in-scope target to keyDepth segments', () => {
    expect([...storeKeysForProcess(proc('p', { a: ['unique', 'RNA', 'foo'] }))])
      .toEqual([['unique.RNA', 1]]);
  });

  it('never yields the meaningless "." key for a bare ".." (parent-scope) target', () => {
    // Real fixture case: v2ecoli-baseline's `division` process wires its
    // `agents` output port to `['..']`. That resolves to `agents` — OUTSIDE
    // the process's own parent store — so it has no local store identity and
    // must not surface as a cluster key at all.
    const keys = storeKeysForProcess(proc('division', {}, { agents: ['..'] }));
    expect(keys.has('.')).toBe(false);
    expect(keys.size).toBe(0);
  });

  it('skips an up-then-into-a-sibling target (outside the process scope)', () => {
    // ['..','bulk'] from parent `agents.0` resolves to `agents.bulk`, which is
    // NOT under `agents.0`. It is a different store from `agents.0.bulk` and
    // must not be conflated with it.
    const keys = storeKeysForProcess(proc('p', {}, { a: ['..', 'bulk'] }));
    expect(keys.has('bulk')).toBe(false);
    expect(keys.size).toBe(0);
  });

  it('skips a multi-segment out-of-scope boundary target', () => {
    const keys = storeKeysForProcess(proc('p', {}, { a: ['..', 'boundary', 'external'] }));
    expect(keys.has('boundary.external')).toBe(false);
    expect(keys.size).toBe(0);
  });

  it('skips a target that walks above the composite root', () => {
    const keys = storeKeysForProcess(proc('p', {}, { a: ['..', '..', 'x'] }));
    expect(keys.size).toBe(0);
  });

  it('resolves a leading "." to the same scope', () => {
    const keys = storeKeysForProcess(proc('p', {}, { a: ['.', 'bulk'] }));
    expect(keys.has('.')).toBe(false);
    expect([...keys.keys()]).toEqual(['bulk']);
  });

  it('pops on an interior ".." instead of dropping the token', () => {
    // ['unique','..','bulk'] resolves to `agents.0.bulk` — the SAME store as
    // ['bulk']. The previous string-munging fix produced a phantom
    // 'unique.bulk' key here.
    const keys = storeKeysForProcess(proc('p', { a: ['unique', '..', 'bulk'], b: ['bulk'] }));
    expect(keys.has('unique.bulk')).toBe(false);
    expect([...keys]).toEqual([['bulk', 2]]);
  });

  it('skips a target that navigates in and straight back out to the parent', () => {
    // ['bulk','..'] resolves to `agents.0` — the parent store itself, not a
    // store the process is wired into. The previous fix yielded a phantom
    // 'bulk' key here.
    const keys = storeKeysForProcess(proc('p', { a: ['bulk', '..'] }));
    expect(keys.has('bulk')).toBe(false);
    expect(keys.size).toBe(0);
  });

  it('drops pure navigation targets while keeping real keys from the same process', () => {
    const keys = storeKeysForProcess(
      proc('division', { bulk: ['bulk'] }, { agents: ['..'], environment: ['.'] }),
    );
    expect(keys.has('.')).toBe(false);
    expect(keys.has('')).toBe(false);
    expect(keys.get('bulk')).toBe(1);
    expect([...keys.keys()]).toEqual(['bulk']);
  });

  it('never emits a key containing an empty or dot-only segment', () => {
    const keys = storeKeysForProcess(
      proc('p', { a: ['..'], b: ['.'], c: ['..', '..'], d: ['bulk'] }),
    );
    for (const k of keys.keys()) {
      expect(k.split('.').every((seg) => seg !== '' && seg !== '.' && seg !== '..')).toBe(true);
    }
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
