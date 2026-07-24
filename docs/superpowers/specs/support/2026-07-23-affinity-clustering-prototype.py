"""Compare two clustering strategies on the real v2ecoli baseline.

  A) key-assignment  : each process -> most-shared non-hub store it touches
  B) jaccard agglom  : cluster processes by similarity of their store sets
"""
import json, sys
from collections import defaultdict

PATH = ("/Users/eranagmon/code/v2ecoli/reports/composite-state/"
        "v2ecoli.composites.baseline.json")
NOISE = ("_layer_token", "process.", "process_state.", "request",
         "next_update_time", "pinned_flux_targets", "timestep",
         "global_time", "allocate.", "_")


def bookkeeping(n):
    n = n.lower()
    return n.startswith("unique_update") or n.startswith("allocator") or "listener" in n


def find_processes(node, path=()):
    if not isinstance(node, dict):
        return
    if node.get("_type") in ("process", "step"):
        yield path, node; return
    for k, v in node.items():
        if not k.startswith("_"):
            yield from find_processes(v, path + (k,))


def resolve(parent, wire):
    if isinstance(wire, str): wire = [wire]
    if not isinstance(wire, list): return None
    cur = list(parent)
    for seg in wire:
        if seg == "..": cur and cur.pop()
        elif seg != ".": cur.append(str(seg))
    return tuple(cur)


def keys_for(parent, ports, depth=2):
    out = set()
    for wire in (ports or {}).values():
        full = resolve(parent, wire)
        if not full: continue
        rel = full[len(parent):] if full[:len(parent)] == tuple(parent) else full
        if not rel: continue
        k = ".".join(rel[:depth])
        if not k.startswith(NOISE): out.add(k)
    return out


doc = json.load(open(PATH))
touches = {}
for path, node in find_processes(doc.get("state", doc)):
    name = path[-1]
    if bookkeeping(name): continue
    touches[name] = keys_for(path[:-1], node.get("inputs")) | keys_for(path[:-1], node.get("outputs"))

n = len(touches)
df = defaultdict(int)
for ks in touches.values():
    for k in ks: df[k] += 1


def strategy_a(hub_frac):
    cut = max(3, round(hub_frac * n))
    hubs = {k for k, c in df.items() if c >= cut}
    cl = defaultdict(list)
    for nm, ks in touches.items():
        c = [k for k in ks if k not in hubs]
        cl[max(c, key=lambda k: (df[k], k)) if c else "~global"].append(nm)
    return hubs, cl


print(f"{n} visible processes\n")
for hf in (0.25, 0.30, 0.35, 0.40):
    hubs, cl = strategy_a(hf)
    sizes = sorted((len(v) for v in cl.values()), reverse=True)
    print(f"--- A: hub>={round(hf*n)} ({hf:.0%})  hubs={sorted(hubs, key=lambda k:-df[k])}")
    print(f"    {len(cl)} clusters, sizes {sizes}, {sum(1 for s in sizes if s==1)} singletons")
    for key, names in sorted(cl.items(), key=lambda kv: (kv[0].startswith('~'), -len(kv[1]), kv[0])):
        print(f"      {key:28s} {', '.join(sorted(names))}")
    print()

# ---- B: Jaccard agglomerative (average linkage) on store-set similarity ----
names = sorted(touches)
def jac(a, b):
    A, B = touches[a], touches[b]
    return len(A & B) / len(A | B) if (A | B) else 0.0

for THRESH in (0.45, 0.55):
    groups = [[x] for x in names]
    while True:
        best, bi, bj = 0.0, -1, -1
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                s = sum(jac(a, b) for a in groups[i] for b in groups[j]) / (len(groups[i]) * len(groups[j]))
                if s > best: best, bi, bj = s, i, j
        if best < THRESH: break
        groups[bi] += groups[bj]; groups.pop(bj)
    sizes = sorted((len(g) for g in groups), reverse=True)
    print(f"--- B: jaccard avg-linkage, merge>={THRESH}")
    print(f"    {len(groups)} clusters, sizes {sizes}, {sum(1 for s in sizes if s==1)} singletons")
    for g in sorted(groups, key=lambda g: -len(g)):
        shared = set.intersection(*[touches[x] for x in g]) if g else set()
        lbl = ", ".join(sorted(shared - {"bulk", "listeners"})) or "(no distinctive shared store)"
        print(f"      [{lbl:34s}] {', '.join(sorted(g))}")
    print()
