"""Recipe operations on a process-bigraph composite document.

Pure logic; no I/O. Used by the Investigation Composites tab endpoints and
the runtime orchestrator.
"""
from __future__ import annotations
from typing import Any


def _follow_dotted_path(doc: dict, dotted: str) -> tuple[dict, str]:
    """Return (parent_container, final_key) for the addressed value.

    Path resolution:
      - 'rate'                                  -> doc['parameters']['rate'] container, key='default'
      - 'state.chromosome.DnaA_count._default'  -> doc['state']['chromosome']['DnaA_count'] container, key='_default'
      - 'state.replication.config.rate'         -> doc['state']['replication']['config'] container, key='rate'
    """
    if '.' in dotted:
        parts = dotted.split('.')
        node: Any = doc
        for p in parts[:-1]:
            if not isinstance(node, dict) or p not in node:
                raise KeyError(
                    f"path component {p!r} not found while resolving {dotted!r}; "
                    f"available keys: {list(node.keys()) if isinstance(node, dict) else 'n/a'}"
                )
            node = node[p]
        if not isinstance(node, dict):
            raise KeyError(f"path {dotted!r} ends in a non-mapping container")
        return node, parts[-1]
    # Bare name: assume a declared parameter; key is 'default'.
    params = doc.get('parameters') or {}
    if dotted not in params:
        raise KeyError(
            f"parameter {dotted!r} undeclared; available: {list(params.keys())}"
        )
    return params[dotted], 'default'


def apply_parameter_overrides(doc: dict, overrides: dict) -> None:
    """Apply scalar overrides to ``doc`` in place.

    Two override shapes:
      - bare name (``rate: 2.0``) -> sets ``parameters[name]['default']``.
      - dotted path (``state.chromosome.DnaA_count._default: 200``) -> sets
        the addressed scalar.
    Raises KeyError if a non-existent path is referenced.
    """
    for key, value in (overrides or {}).items():
        container, final = _follow_dotted_path(doc, key)
        container[final] = value


def apply_process_overrides(doc: dict, overrides: dict) -> None:
    """Apply process swap/removal overrides to ``doc`` in place.

    Each entry is ``process_name -> spec``:
      - None       -> remove the process
      - str        -> set address (keep config)
      - dict       -> may contain 'address' and/or 'config' to swap/replace
    Raises KeyError if the process doesn't exist.
    """
    state = doc.get('state') or {}
    for proc_name, spec in (overrides or {}).items():
        if proc_name not in state:
            raise KeyError(
                f"unknown process {proc_name!r}; available: {list(state.keys())}"
            )
        if spec is None:
            del state[proc_name]
            continue
        node = state[proc_name]
        if not isinstance(node, dict) or node.get('_type') != 'process':
            raise KeyError(f"{proc_name!r} is not a process node; cannot override")
        if isinstance(spec, str):
            node['address'] = spec
            continue
        if isinstance(spec, dict):
            if 'address' in spec:
                node['address'] = spec['address']
            if 'config' in spec:
                node['config'] = spec['config']
            continue
        raise TypeError(f"process_overrides[{proc_name!r}] must be None, str, or dict")


def walk_state_snapshot(state: dict, *, max_depth: int = 8) -> list[dict]:
    """Flatten a process-bigraph STATE SNAPSHOT into a list of leaf records.

    Unlike ``walk_state_tree`` (which reads composite documents with
    ``_type`` / ``address`` metadata), this walks a serialized state — the
    ``.pbg`` files dropped by ``v2ecoli.pbg.save_pbg``, runs.db history
    rows, etc. Output records are intentionally narrow so a thousand
    leaves stay browser-friendly:

        {path: [...], kind: 'structured_array' | 'array' | 'object' |
                            'string' | 'int' | 'float' | 'bool' | 'null',
         fields?: [...]        # structured arrays only
         length?: int          # arrays only
         preview?: str         # scalars, truncated to 50 chars
        }

    Stops at depth ``max_depth`` to keep the response bounded; deeper
    subtrees collapse into an `object` leaf with the path you'd recurse
    into.
    """
    out: list[dict] = []

    def _walk(node, path: tuple, depth: int):
        if isinstance(node, dict):
            # Process-bigraph structured arrays carry a marker key.
            if node.get("__structured_array__"):
                fields = [f[0] for f in (node.get("dtype") or []) if isinstance(f, (list, tuple)) and f]
                out.append({"path": list(path), "kind": "structured_array", "fields": fields})
                return
            if not node:
                out.append({"path": list(path), "kind": "object", "length": 0})
                return
            if depth >= max_depth:
                out.append({"path": list(path), "kind": "object", "length": len(node), "truncated": True})
                return
            for k, v in node.items():
                _walk(v, path + (str(k),), depth + 1)
        elif isinstance(node, list):
            out.append({"path": list(path), "kind": "array", "length": len(node)})
        elif isinstance(node, bool):
            out.append({"path": list(path), "kind": "bool", "preview": str(node)})
        elif isinstance(node, int):
            out.append({"path": list(path), "kind": "int", "preview": str(node)})
        elif isinstance(node, float):
            out.append({"path": list(path), "kind": "float", "preview": f"{node:.6g}"})
        elif node is None:
            out.append({"path": list(path), "kind": "null"})
        else:
            s = str(node)
            out.append({"path": list(path), "kind": type(node).__name__, "preview": (s[:50] + "…") if len(s) > 50 else s})

    if isinstance(state, dict):
        for k, v in state.items():
            _walk(v, (str(k),), 1)
    return out


def walk_state_tree(doc: dict) -> list[dict]:
    """Flatten ``doc['state']`` into a list of node records.

    Each record:
        {path: [...], kind: 'store' | 'process',
         type?: str, default?: Any,
         address?: str, config?: dict}
    """
    state = doc.get('state') or {}
    out: list[dict] = []

    def _walk(node: Any, path: tuple):
        if not isinstance(node, dict):
            # Plain scalar leaf (e.g. a value or placeholder string).
            out.append({
                'path': list(path),
                'kind': 'store',
                'type': type(node).__name__,
                'default': node,
            })
            return
        if node.get('_type') == 'process':
            out.append({
                'path': list(path),
                'kind': 'process',
                'address': node.get('address', ''),
                'config': node.get('config', {}),
            })
            return
        if '_type' in node:
            out.append({
                'path': list(path),
                'kind': 'store',
                'type': node.get('_type', ''),
                'default': node.get('_default'),
            })
            return
        for key, child in node.items():
            _walk(child, path + (key,))

    for key, child in state.items():
        _walk(child, (key,))
    return out
