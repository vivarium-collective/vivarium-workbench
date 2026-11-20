"""Ratchet: the CORE's calls to the SMS-specific viva-api surfaces may shrink, never grow.

docs/backend-adapters.md §2.4 (step P1b, deliberate change): SMS code lives in the plugin package
``vivarium_workbench_sms``, which may call these surfaces freely. The workbench core (``vivarium_workbench``) may
call them only from the mixed modules listed in ``ALLOWED`` below -- the modules step P2 splits -- and that list
only shrinks; it is empty when P2 ends.

* a core module calling one of these methods that is not listed, or a listed one calling one it did not call
  before, fails -- put the call in the plugin (behind a backend facet) instead;
* a listed call that is GONE also fails, so the list is shrunk in the same PR that removes the call.

Only the plugin may spell an SMS route; a core module that does (a ``client._get("/api/v1/...")`` that bypasses
the methods) is pinned by ``ROUTE_LITERALS`` the same way.

What counts as a use: any reference to the method by name -- a call, a bound method passed on
(``status_of or client.compose_status``), or ``getattr(client, "compose_status")``. ``SMS_METHODS`` itself
is checked against the client: it must be exactly the public methods whose bodies reach an SMS route,
directly or through other client methods and module helpers, so a new SMS method cannot slip past.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "vivarium_workbench"
PLUGIN = Path(__file__).resolve().parent.parent / "vivarium_workbench_sms"
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

#: The core's remaining callers (step P1b: the plugin's own modules are no longer listed -- a move, not a removal
#: of calls). Shrink this as step P2 lands; never grow it.
ALLOWED: dict[str, frozenset[str]] = {
    "lib.composite_test_run_views": frozenset({"run_simulation"}),
    # compose_status is passed as a bound method: ``(status_of or client.compose_status)(sim_id)``.
    "lib.remote_run": frozenset({"compose_status", "compose_status_batch", "compose_submit",
                                 "download_compose_results"}),
    "lib.remote_run_landing": frozenset({"download_data"}),
    "lib.remote_run_views": frozenset({"run_analysis", "upload_simulator"}),
    "lib.remote_trace": frozenset({"compose_status", "simulation_trace"}),
    "lib.simulations_index": frozenset({"download_data"}),
}

#: Every SMS method any module calls, as of step 1a. With PINNED_PAIRS below, this keeps a move between modules
#: (a new ALLOWED key) from also slipping a new call in. Only ever remove names from it.
PINNED_UNION: frozenset[str] = frozenset({
    "analysis_status", "cancel_simulation", "compose_status", "compose_status_batch",
    "compose_submit", "download_compose_results", "download_data", "download_workspace", "get_simulation",
    "list_analyses", "list_branch_builds", "list_build_simulations", "list_simulators", "register_branch_head",
    "run_analysis", "run_simulation", "simulation_chain_progress", "simulation_status", "simulation_trace",
    "simulator_commit", "simulator_status", "upload_simulator",
})

#: SMS route literals outside the client: module -> the routes it spells.
ROUTE_LITERALS: dict[str, frozenset[str]] = {
    # The config discovery a card-run dispatch reads to pick a build's config file.
    "lib.composite_test_run_views": frozenset({"/api/v1/simulations/discovery"}),
}

#: The SMS-specific prefixes. ``/viva/v1/compose`` is the interim home of ``/compose/v1``; ``/env-worker/v1`` is
#: viva-api's hosted env-worker surface, spelled ``/viva/v1/workers`` when the server advertises ``viva-v1-workers``
#: (step P2 Workers). The rest of ``/viva/v1`` is viva-core, which the design keeps.
_ROUTE = re.compile(r"^/(api/v1|core/v1|compose/v1|viva/v1/compose|env-worker/v1|viva/v1/workers)(/|$)")


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


def _client_sms_methods() -> set[str]:
    """The public ``SmsApiClient`` methods that reach an SMS route: a route literal in their body, or a
    reference (by name) to a client method, module-level helper, or module/class constant of
    ``sms_api_client`` that holds or reaches one (``_COMPOSE_PREFIX + "/..."`` counts)."""
    tree = ast.parse(CLIENT.read_text(encoding="utf-8"))
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
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _ROUTE.match(node.value):
                found.setdefault(name, set()).add(node.value)
    return found


def _diff(found: dict[str, set[str]], allowed: dict[str, frozenset[str]]) -> tuple[list[str], list[str]]:
    new = [f"{m}: {sorted(found[m] - allowed.get(m, frozenset()))}"
           for m in sorted(found) if found[m] - allowed.get(m, frozenset())]
    gone = [f"{m}: {sorted(allowed[m] - found.get(m, set()))}"
            for m in sorted(allowed) if allowed[m] - found.get(m, set())]
    return new, gone


def test_no_new_sms_method_calls():
    new, _ = _diff(_sms_calls(), ALLOWED)
    assert not new, (
        "new SMS-specific SmsApiClient calls in the core (docs/backend-adapters.md §2.4): put them in the SMS "
        "plugin (vivarium_workbench_sms) behind a backend facet instead:\n  " + "\n  ".join(new))


#: (module, method) pairs in ALLOWED: 42 as of step 1a; 13 after step P1b moved the plugin's own modules out of
#: the core (a move, not 28 removed calls); 12 after step P2 Client moved the capability contract into the plugin;
#: 11 after step P2 Runs c2 moved run_jobs' batch status into the plugin. Only removals lower it.
PINNED_PAIRS = 11


def test_allowed_methods_never_grow():
    extra = set().union(*ALLOWED.values()) - PINNED_UNION
    assert not extra, f"ALLOWED gained SMS methods no module called before: {sorted(extra)}"
    pairs = sum(len(v) for v in ALLOWED.values())
    assert pairs <= PINNED_PAIRS, (
        f"ALLOWED has {pairs} (module, method) pairs, more than {PINNED_PAIRS}: a move must remove what it adds")


def test_removed_sms_method_calls_are_dropped_from_the_ratchet():
    _, gone = _diff(_sms_calls(), ALLOWED)
    assert not gone, "these calls are gone; remove them from ALLOWED so the ratchet stays tight:\n  " + "\n  ".join(gone)


def test_no_new_sms_route_literals():
    new, gone = _diff(_route_literals(), ROUTE_LITERALS)
    assert not new, ("SMS routes spelled in the core; put the call in vivarium_workbench_sms (the plugin):\n  "
                     + "\n  ".join(new))
    assert not gone, "these route literals are gone; remove them from ROUTE_LITERALS:\n  " + "\n  ".join(gone)


def test_sms_methods_match_the_client():
    """SMS_METHODS is exactly the client's public methods that reach an SMS route: a new one (or a renamed
    one) must be listed, and a method that stops reaching one must leave the list."""
    derived = _client_sms_methods()
    assert derived == SMS_METHODS, {"missing from SMS_METHODS": sorted(derived - SMS_METHODS),
                                    "listed but not SMS": sorted(SMS_METHODS - derived)}
