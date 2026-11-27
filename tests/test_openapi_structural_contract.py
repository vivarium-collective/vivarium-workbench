"""The OpenAPI structural contract — the gate for the app.py route-registrar refactor.

The dashboard's generated TypeScript client (``static/types/*.generated.d.ts``) and the
AI-chat tool index are both derived from ``create_app().openapi()``. Collapsing the ~160
thin wrapper routes into a declarative route table must leave that schema structurally
identical: same paths, methods, **operationIds**, parameters, requestBody, responses, and
``components.schemas``. Only the per-operation ``description`` (the route docstring, which the
refactor trims to a one-line summary) is allowed to differ — that is where the LOC savings is,
and it only makes the OpenAPI ``description`` fields and the derived AI tool descriptions
terser, never renames or re-types anything the client depends on.

This test freezes that structural skeleton as a committed golden and asserts it never drifts.

Regenerate the golden intentionally (after a *real* route change) with:

    UPDATE_OPENAPI_GOLDEN=1 pytest tests/test_openapi_structural_contract.py -k golden

It needs the SMS backend provider active (providers on), so it is a ``plugin``-marked test
like ``test_generate_ts`` — the committed golden carries the provider's routes and schemas.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

GOLDEN_PATH = Path(__file__).parent / "_fixtures" / "openapi_structural_golden.json"


def _build_openapi() -> dict[str, Any]:
    """Build the live OpenAPI exactly as the gate checks it: providers ON, no live backend.

    Mirrors how the generated client is produced — the SMS provider's routes are added to the
    app at create time (``_include_provider_routers``), so they are in ``openapi()`` here.
    """
    # Providers ON (default); no reachable backend needed just to enumerate routes.
    os.environ.pop("VIVARIUM_WORKBENCH_BACKEND_PROVIDERS", None)
    os.environ.pop("VIVA_API_BASE", None)
    os.environ.pop("SMS_API_BASE", None)
    from vivarium_workbench.lib.backends import provider

    provider.reset()
    from vivarium_workbench.api.app import create_app

    return create_app().openapi()


def structural_skeleton(spec: dict[str, Any]) -> dict[str, Any]:
    """The schema stripped to what the TS client and tool index depend on.

    Every per-operation ``description`` is dropped (the docstring the refactor trims); everything
    else is kept verbatim, including each operation's ``summary``, ``tags``, ``operationId``,
    ``parameters`` (with their own descriptions), ``requestBody``, ``responses``, and the full
    ``components.schemas``.
    """
    paths: dict[str, Any] = {}
    for path, item in spec.get("paths", {}).items():
        methods: dict[str, Any] = {}
        for method, op in item.items():
            if method not in ("get", "post", "put", "delete", "patch", "head", "options", "trace"):
                # path-level keys (parameters, $ref, servers) — keep verbatim.
                methods[method] = op
                continue
            if isinstance(op, dict):
                methods[method] = {k: v for k, v in op.items() if k != "description"}
            else:
                methods[method] = op
        paths[path] = methods
    return {
        "paths": paths,
        "schemas": spec.get("components", {}).get("schemas", {}),
    }


def _diff_report(golden: dict[str, Any], actual: dict[str, Any]) -> str:
    """A focused, human-readable diff of where the skeletons disagree."""
    lines: list[str] = []

    g_ops = {(p, m): op for p, item in golden["paths"].items() for m, op in item.items()}
    a_ops = {(p, m): op for p, item in actual["paths"].items() for m, op in item.items()}
    removed = sorted(set(g_ops) - set(a_ops))
    added = sorted(set(a_ops) - set(g_ops))
    if removed:
        lines.append(f"operations MISSING after refactor ({len(removed)}):")
        lines += [f"  - {m.upper()} {p}" for p, m in removed]
    if added:
        lines.append(f"operations ADDED after refactor ({len(added)}):")
        lines += [f"  + {m.upper()} {p}" for p, m in added]
    for key in sorted(set(g_ops) & set(a_ops)):
        if g_ops[key] != a_ops[key]:
            p, m = key
            gop, aop = g_ops[key], a_ops[key]
            subkeys = sorted(set(gop) | set(aop))
            changed = [k for k in subkeys if gop.get(k) != aop.get(k)]
            lines.append(f"operation CHANGED: {m.upper()} {p} -> fields {changed}")
            for k in changed:
                if k == "operationId":
                    lines.append(f"    operationId: {gop.get(k)!r} -> {aop.get(k)!r}")
                else:
                    gv = json.dumps(gop.get(k), sort_keys=True)
                    av = json.dumps(aop.get(k), sort_keys=True)
                    lines.append(f"    {k}: {gv[:200]}  ->  {av[:200]}")

    g_sch, a_sch = golden["schemas"], actual["schemas"]
    sremoved = sorted(set(g_sch) - set(a_sch))
    sadded = sorted(set(a_sch) - set(g_sch))
    if sremoved:
        lines.append(f"schemas MISSING ({len(sremoved)}): {sremoved}")
    if sadded:
        lines.append(f"schemas ADDED ({len(sadded)}): {sadded}")
    for name in sorted(set(g_sch) & set(a_sch)):
        if g_sch[name] != a_sch[name]:
            lines.append(f"schema CHANGED: {name}")
    return "\n".join(lines) if lines else "(no structural diff — only descriptions changed)"


@pytest.mark.plugin
def test_openapi_structural_contract_golden():
    """The live OpenAPI's structural skeleton must match the committed golden.

    Only operation ``description`` fields may differ from the pre-refactor schema.
    """
    actual = structural_skeleton(_build_openapi())

    if os.environ.get("UPDATE_OPENAPI_GOLDEN") == "1":
        GOLDEN_PATH.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("regenerated openapi structural golden")

    assert GOLDEN_PATH.exists(), (
        f"missing golden {GOLDEN_PATH}; create it with "
        "UPDATE_OPENAPI_GOLDEN=1 pytest tests/test_openapi_structural_contract.py -k golden"
    )
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    # Round-trip actual through json so tuple/dict key types match the loaded golden.
    actual = json.loads(json.dumps(actual, sort_keys=True))
    assert actual == golden, (
        "OpenAPI structural contract drift — the route-registrar refactor must preserve "
        "paths/methods/operationIds/parameters/requestBody/responses/components.schemas "
        "(only operation descriptions may change). Diff:\n" + _diff_report(golden, actual)
    )
