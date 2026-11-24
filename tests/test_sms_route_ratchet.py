"""Ban: the CORE calls no SMS-specific viva-api method and spells no SMS route (docs/backend-adapters.md §2.4).

SMS code lives in the plugin package ``vivarium_workbench_sms``, which may call these surfaces freely. Steps P1b and
P2 ratcheted the core's remaining callers down to none; at the end of P2 (Routes Rt3) the ratchet became this ban:

* a core module referring to one of ``SMS_METHODS`` fails -- put the call in the plugin, behind a backend facet;
* a core module spelling a viva-api route (``/api/v1``, ``/core/v1``, ``/compose/v1``, ``/env-worker/v1`` or any
  ``/viva/v1``) fails -- the core reaches viva-api only through a provider's backend.

What counts as a use: any reference to the method by name -- a call, a bound method passed on
(``status_of or client.compose_status``), or ``getattr(client, "compose_status")``. ``SMS_METHODS`` itself
is checked against the client: it must be exactly the public methods whose bodies reach an SMS route,
directly or through other client methods and module helpers, so a new SMS method cannot slip past.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from _plugin_paths import PLUGIN_DIR

PKG = Path(__file__).resolve().parent.parent / "vivarium_workbench"
PLUGIN = PLUGIN_DIR  # tests/_plugin_paths.py (step P4b: the plugin is its own workspace member)
CLIENT = PLUGIN / "sms_api_client.py"

#: ``SmsApiClient`` methods that can reach an SMS-specific route. Those marked (fallback) go to
#: ``/viva/v1/environments`` first and to ``/core/v1/simulator*`` only when the backend lacks the
#: environments capabilities (W2); they leave this set when those fallbacks are dropped (step 4).
SMS_METHODS = frozenset({
    # /core/v1/simulator* (fallback)
    "latest_simulator", "register_simulator", "register_branch_head", "simulator_status",
    "list_branch_builds", "list_simulators", "simulator_commit", "upload_simulator",
    # /viva/v1/capabilities first, /core/v1/capabilities on a 404 (fallback)
    "capabilities",
    # /api/v1/simulations*
    "list_build_simulations", "download_workspace", "simulation_status", "get_simulation",
    "cancel_simulation", "simulation_chain_progress", "simulation_trace", "run_simulation", "download_data",
    # /api/v1/.../analys*
    "run_analysis", "analysis_status", "list_analyses",
    # /compose/v1, rewritten to the interim /viva/v1/compose
    "compose_submit", "compose_status", "compose_status_batch", "download_compose_results",
    # /env-worker/v1 (rewritten to /viva/v1/workers): viva-api's hosted env workers (step P2 Workers moved the
    # launchers into the plugin, so no core module calls these)
    "start_env_worker", "env_worker_status", "stop_env_worker",
    "start_relayed_env_worker", "call_relayed_env_worker", "stop_relayed_env_worker",
    "submit_env_worker_task", "get_env_worker_task",
})

#: (module, receiver) pairs that are NOT an ``SmsApiClient`` although they share a method name. Pinned to the
#: module, so naming some other variable ``session`` does not hide an SMS call.
NOT_SMS_RECEIVERS = frozenset({
    ("lib.remote_run_compose", "session"),   # compose-api's own client (compose_api_client.ComposeSession)
    ("lib.ai_views", "ai_tools"),            # the AI chat's own capabilities() (a module function)
})

#: The SMS-specific prefixes. ``/viva/v1/compose`` is the interim home of ``/compose/v1``; ``/env-worker/v1`` is
#: viva-api's hosted env-worker surface, spelled ``/viva/v1/workers`` when the server advertises ``viva-v1-workers``
#: (step P2 Workers). The rest of ``/viva/v1`` is viva-core, which the design keeps. This classifies the CLIENT's
#: methods (``SMS_METHODS``); the core's route ban below is wider.
_ROUTE = re.compile(r"^/(api/v1|core/v1|compose/v1|viva/v1/compose|env-worker/v1|viva/v1/workers)(/|$)")

#: Every viva-api route the core must not spell: the SMS-specific prefixes above and, since the end of step P2, the
#: rest of ``/viva/v1`` too (viva-core's surface is a provider's backend as well; the core names none).
_ANY_VIVA_API_ROUTE = re.compile(r"^/(api/v1|core/v1|compose/v1|env-worker/v1|viva/v1)(/|$)")


def _modules():
    for path in sorted(PKG.rglob("*.py")):
        if path == CLIENT:
            continue
        name = ".".join(path.relative_to(PKG).with_suffix("").parts)
        yield name, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _receiver(node: ast.Attribute) -> str | None:
    return node.value.id if isinstance(node.value, ast.Name) else None


def _sms_calls() -> dict[str, set[str]]:
    """Every reference to an SMS method by name: ``x.method`` (called or not) and ``getattr(x, "method")``."""
    found: dict[str, set[str]] = {}
    for name, tree in _modules():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr in SMS_METHODS
                    and (name, _receiver(node)) not in NOT_SMS_RECEIVERS):
                found.setdefault(name, set()).add(node.attr)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("getattr", "hasattr")
                    and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in SMS_METHODS):
                found.setdefault(name, set()).add(node.args[1].value)
    return found


def _client_sms_methods(client: Path = CLIENT) -> set[str]:
    """The public ``SmsApiClient`` methods that reach an SMS route: a route literal in their body, or a
    reference (by name) to a client method, module-level helper, or module/class constant of
    ``sms_api_client`` that holds or reaches one (``_COMPOSE_PREFIX + "/..."`` counts)."""
    tree = ast.parse(client.read_text(encoding="utf-8"))
    # Transport every request goes through, so not a route for other methods to inherit: the path
    # rewriter (it spells /viva/v1/compose to map /compose/v1 onto it) and the capability lookup it
    # consults (whose /core/v1 fallback makes ``capabilities`` itself an SMS method, listed above).
    transport = {"resolve_path", "_has_successor", "_server_capabilities"}
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SmsApiClient")
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name not in transport}
    helpers = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name not in transport}

    def constants(body: list) -> dict[str, ast.AST]:
        out: dict[str, ast.AST] = {}
        for n in body:
            targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
            for t in targets:
                if isinstance(t, ast.Name) and getattr(n, "value", None) is not None:
                    out[t.id] = n
        return out
    module_consts, class_consts = constants(tree.body), constants(cls.body)
    nodes = {**{f"m:{k}": v for k, v in methods.items()}, **{f"f:{k}": v for k, v in helpers.items()},
             **{f"c:{k}": v for k, v in module_consts.items()}, **{f"a:{k}": v for k, v in class_consts.items()}}

    def edges(fn: ast.AST) -> set[str]:
        out = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Attribute) and n.attr in methods:
                out.add(f"m:{n.attr}")
            elif isinstance(n, ast.Attribute) and n.attr in class_consts:
                out.add(f"a:{n.attr}")
            elif isinstance(n, ast.Name) and n.id in helpers:
                out.add(f"f:{n.id}")
            elif isinstance(n, ast.Name) and n.id in module_consts:
                out.add(f"c:{n.id}")
        return out

    reach = {k for k, fn in nodes.items()
             if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and _ROUTE.match(n.value)
                    for n in ast.walk(fn))}
    graph = {k: edges(fn) for k, fn in nodes.items()}
    grew = True
    while grew:
        grew = False
        for k in nodes:
            if k not in reach and graph[k] & reach:
                reach.add(k)
                grew = True
    return {k[2:] for k in reach if k.startswith("m:") and not k[2:].startswith("_")}


def _route_literals() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for name, tree in _modules():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and _ANY_VIVA_API_ROUTE.match(node.value)):
                found.setdefault(name, set()).add(node.value)
    return found


def test_the_core_calls_no_sms_method():
    found = _sms_calls()
    assert found == {}, (
        "SMS-specific SmsApiClient calls in the core (docs/backend-adapters.md §2.4): put them in the SMS plugin "
        "(vivarium_workbench_sms) behind a backend facet instead:\n  "
        + "\n  ".join(f"{m}: {sorted(v)}" for m, v in sorted(found.items())))


def test_the_core_spells_no_sms_route():
    found = _route_literals()
    assert found == {}, (
        "viva-api routes spelled in the core; put the call in vivarium_workbench_sms (the plugin), behind a backend "
        "facet:\n  " + "\n  ".join(f"{m}: {sorted(v)}" for m, v in sorted(found.items())))


@pytest.mark.plugin
def test_sms_methods_match_the_client():
    """SMS_METHODS is exactly the client's public methods that reach an SMS route: a new one (or a renamed
    one) must be listed, and a method that stops reaching one must leave the list. It parses the installed plugin's
    client, so it needs the plugin (docs/backend-adapters.md step P3, K2a)."""
    from vivarium_workbench_sms import sms_api_client

    client = Path(sms_api_client.__file__)
    assert client.resolve() == CLIENT.resolve()   # the client the ban's docstring and SMS_METHODS describe
    derived = _client_sms_methods(client)
    assert derived == SMS_METHODS, {"missing from SMS_METHODS": sorted(derived - SMS_METHODS),
                                    "listed but not SMS": sorted(SMS_METHODS - derived)}
