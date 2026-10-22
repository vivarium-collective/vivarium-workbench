"""The study-run remote routes on compose-api (contract: docs/backend-compose-api.md).

Used only when the operator named a backend (``serve --backend-base-url``) that is compose-api
(:func:`server_capabilities.compose_dispatch_active`) and neither a session build nor the pinned config chose
the simulator-keyed path (:func:`remote_pinned.uses_compose_dispatch`). Every function answers the shape the
legacy ``remote_run_views`` route answers, so the panel and poll code are unchanged.

The workspace's code is not shipped: it runs in an environment compose-api already has, an owner-published
image registered for the workspace's repository (its ``GET /registry/entries`` entry). The composite is
exported with ``local:<module>.<Class>`` addresses (the only form compose-api accepts), with one emitter
recording what a local run of the study records, and checked against that environment's interface manifest
before anything is sent: for a prebuilt image, compose-api itself does not look classes up.

Run ids are ``compose-<simulation id>``: compose-api's ids are integers, which the other routes read as legacy
simulation ids. The Composites-tab remote run (``remote_run.run_remote``) takes the same dispatch through
:func:`run_composite`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vivarium_workbench.lib import remote_pinned, study_spec
from vivarium_workbench.lib.investigations import load_spec

if TYPE_CHECKING:
    from compose_api_client.ext import ComposeSession

RUN_ID_PREFIX = "compose-"
BACKEND = "compose-api"
#: The engine's own classes. Every process-bigraph core registers them under their dotted names, and no
#: environment's interface manifest lists them (a manifest describes the environment's own module roots).
ENGINE_MODULE_ROOT = "process_bigraph"
#: The emitter the compose mode adds to record the study (composite_runs.inject_emitter_for_declared_paths names
#: it so); landing takes it by name when the composite has emitters of its own.
STUDY_EMITTER = "user_emitter"
#: compose-api's bearer token for calls that need a signed-in caller (cancel), as its own CLI reads it.
TOKEN_ENV_VAR = "COMPOSE_API_TOKEN"
#: compose-api's bound on ``interval_time`` (``POST /simulation/run`` answers 400 above it).
MAX_STEPS = 1000
#: The prefix of the tag that names one dispatch, so a submission whose answer was lost can be found again.
DISPATCH_TAG_PREFIX = "workbench:dispatch:"
#: How often, and how far apart in seconds, a submission whose answer was lost is looked up by its dispatch tag.
LOOKUP_ATTEMPTS = 5
LOOKUP_INTERVAL = 3.0

_TERMINAL_OK = {"completed"}
_TERMINAL_BAD = {"failed", "cancelled", "out_of_memory", "timeout"}
_QUEUED = {"submitting", "waiting", "queued", "pending"}


class ComposeDispatchError(Exception):
    """The workspace cannot be run on this compose-api; ``body`` and ``status`` are the route's answer."""

    def __init__(self, body: dict, status: int) -> None:
        super().__init__(body.get("error"))
        self.body = body
        self.status = status


def is_run_id(value: object) -> bool:
    """A compose-api run id (``compose-4706``)."""
    text = str(value or "").strip()
    return text.startswith(RUN_ID_PREFIX) and text[len(RUN_ID_PREFIX):].isdigit()


def run_id_of(simulation_id: int) -> str:
    return f"{RUN_ID_PREFIX}{int(simulation_id)}"


def simulation_id_of(run_id: str) -> int:
    if not is_run_id(run_id):
        raise ValueError(f"{run_id!r} is not a compose-api run id")
    return int(str(run_id).strip()[len(RUN_ID_PREFIX):])


def _text(value: object) -> "str | None":
    """A client field that is a string, else None (an optional field the service left out is ``UNSET``)."""
    return value if isinstance(value, str) else None


def _session() -> "ComposeSession":
    """A session on the named backend, signed in when ``COMPOSE_API_TOKEN`` is set (anonymous otherwise)."""
    from compose_api_client.ext import ComposeSession, EnvironmentToken

    from vivarium_workbench.lib.sms_api_client import explicit_backend_base_url

    base = explicit_backend_base_url()
    if base is None:
        raise ComposeDispatchError({"error": "this is a compose-api run, but no backend was named "
                                             "(serve --backend-base-url) to ask about it", "reason": "no-backend"}, 409)
    tokens = EnvironmentToken() if os.environ.get(TOKEN_ENV_VAR, "").strip() else None
    return ComposeSession(base, tokens=tokens, timeout=120.0)


# -- which environment runs the workspace --------------------------------------------------------------------------


def _canonical_repo(url: str) -> str:
    """``github.com/owner/repo`` for any spelling of a git remote (https, ssh, ``.git``, trailing slash)."""
    text = url.strip()
    text = re.sub(r"^git@([^:]+):", r"\1/", text)
    text = re.sub(r"^[a-z+]+://", "", text)
    text = re.sub(r"^[^@/]+@", "", text)  # credentials in an https remote
    return text.removesuffix("/").removesuffix(".git").lower()


def _origin(ws_root: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(ws_root), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _head(ws_root: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(ws_root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def resolve_environment(session: "ComposeSession", ws_root: Path) -> dict:
    """The prebuilt environment that runs this workspace: ``{name, simulator, image, entry}``.

    ``VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT=name:<environment>`` names it outright. Otherwise the workspace's
    git origin is matched to a registry entry's ``source``, and the entry's prebuilt environment is the one.
    Raises :class:`ComposeDispatchError` (409) naming what to register when nothing matches.
    """
    from vivarium_workbench.lib.env_compat import get_env

    environments = {e.name: e for e in session.environments()}
    entries = session.catalog()
    named = (get_env("BACKEND_ENVIRONMENT", "") or "").strip()
    if named:
        name = named.removeprefix("name:")
        env = environments.get(name)
        if env is None or env.kind != "prebuilt" or not env.simulator:
            raise ComposeDispatchError({
                "error": f"compose-api has no prebuilt environment {name!r} (it has: "
                         f"{', '.join(sorted(n for n, e in environments.items() if e.kind == 'prebuilt')) or 'none'})",
                "reason": "unknown-environment"}, 409)
        entry = next((x for x in entries if name in (x.bundles or [])), None)
        return {"name": name, "simulator": env.simulator, "image": env.image, "entry": entry,
                "module_roots": _module_roots(entries, name), "stale": bool(env.stale)}

    origin = _origin(ws_root)
    if not origin:
        raise ComposeDispatchError({
            "error": "this workspace has no git origin, so compose-api's registry cannot be matched to it; set "
                     "VIVARIUM_WORKBENCH_BACKEND_ENVIRONMENT=name:<environment>",
            "reason": "no-remote"}, 409)
    repo = _canonical_repo(origin)
    entry = next((x for x in entries if x.source and _canonical_repo(x.source) == repo), None)
    prebuilt = [environments[b] for b in (entry.bundles or [])
                if b in environments and environments[b].kind == "prebuilt" and environments[b].simulator] if entry else []
    if entry is None or not prebuilt:
        what = (f"its registry entry {entry.id!r} has no prebuilt environment" if entry is not None
                else f"no registry entry has source {origin}")
        raise ComposeDispatchError({
            "error": f"compose-api cannot run this workspace: {what}. Registering it takes an image built from the "
                     "repository (a compose.Dockerfile and its publishing workflow) and a compose-api config change: "
                     "a manifest.yaml entry at level tested, an environments.yaml entry, and the image digest in "
                     "PREBUILT_SIMULATORS (docs/backend-compose-api.md, onboarding).",
            "reason": "not-registered", "origin": origin}, 409)
    env = prebuilt[0]
    return {"name": env.name, "simulator": env.simulator, "image": env.image, "entry": entry,
            "module_roots": _module_roots(entries, env.name), "stale": bool(env.stale)}


def _module_roots(entries: "list[Any]", environment: str) -> list[str]:
    """Every module root an environment provides: the union over each registry entry it bundles (an environment
    such as ``registry_env`` bundles several)."""
    roots: list[str] = []
    for entry in entries:
        if environment in (entry.bundles or []):
            roots.extend(r for r in (entry.module_roots or []) if r not in roots)
    return roots


# -- the document --------------------------------------------------------------------------------------------------


def _addresses(node: Any) -> list[Any]:
    """Every process address in a document, in string or ``{protocol, data}`` form, as compose-api collects them
    (``registry.validation``): a dict under ``address`` without a ``protocol`` is a type declaration, not one."""
    found: list[Any] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "address" and (isinstance(value, str) or (isinstance(value, dict) and "protocol" in value)):
                found.append(value)
            else:
                found.extend(_addresses(value))
    elif isinstance(node, list):
        for v in node:
            found.extend(_addresses(v))
    return found


def _split_address(address: Any) -> "tuple[str, str | None]":
    """``(protocol, target)`` of an address in either form; a bare string is ``local``, as compose-api reads it."""
    if isinstance(address, dict):
        target = address.get("data")
        return str(address.get("protocol")), target if isinstance(target, str) else None
    if ":" in address:
        protocol, target = address.split(":", 1)
        return protocol, target
    return "local", address


def _address_text(address: Any) -> str:
    protocol, target = _split_address(address)
    return f"{protocol}:{target}" if target is not None else json.dumps(address, sort_keys=True)


def _class_path(address: str) -> str:
    """``<module>.<Class>`` of a ``local:`` address in either form (``local:!x.Y`` or ``local:x.Y``)."""
    return address.removeprefix("local:").removeprefix("!")


def manifest_violations(document: dict, components: "list[str]", module_roots: "list[str]") -> list[str]:
    """Why each address in ``document`` would not run in the environment, if any would not.

    A class under one of the environment's module roots must be one its manifest lists (``components``, the
    manifest's addresses); an engine class (``process_bigraph.*``) is resolved by every core; anything else is
    not in the environment at all.
    """
    listed = {_class_path(c) for c in components}
    problems = []
    for address in sorted({_address_text(a) for a in _addresses(document)}):
        protocol, path = _split_address(address)
        if protocol != "local" or path is None or path.startswith("!"):
            problems.append(f"{address}: compose-api accepts only local:<module>.<Class> addresses")
            continue
        root = path.split(".", 1)[0]
        if root == ENGINE_MODULE_ROOT:
            continue
        if root not in module_roots:
            problems.append(f"{address}: module {root!r} is not in this environment (it provides "
                            f"{', '.join(module_roots) or 'nothing registered'})")
        elif path not in listed:
            problems.append(f"{address}: not in the environment's interface manifest")
    return problems


def _state(document: dict) -> dict:
    """The state of a document: a static spec exports ``{schema, state}``, a generator the state itself."""
    return document["state"] if isinstance(document.get("state"), dict) else document


def _with_state(document: dict, state: dict) -> dict:
    return {**document, "state": state} if isinstance(document.get("state"), dict) else state


def top_level_emitters(document: dict) -> list[str]:
    """The keys of the state's top-level nodes whose class is a process-bigraph ``Emitter`` (each one is a key
    of the run's ``results_*.pber``)."""
    import importlib

    from process_bigraph.emitter import Emitter

    found = []
    for key, node in _state(document).items():
        address = node.get("address") if isinstance(node, dict) else None
        if not isinstance(address, (str, dict)):
            continue
        protocol, path = _split_address(address)
        if protocol != "local" or path is None:
            continue
        module, _, name = path.removeprefix("!").rpartition(".")
        try:
            cls = getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError, ValueError):
            continue
        if isinstance(cls, type) and issubclass(cls, Emitter):
            found.append(key)
    return found


def export_document(ws_root: Path, composite_id: str, params: dict, emit_paths: "list[str]") -> dict:
    """The composite as compose-api runs it: ``local:<module>.<Class>`` addresses, and a RAM emitter recording
    ``emit_paths`` (:data:`STUDY_EMITTER`), whose history landing reads. Without paths, the composite's own
    emitter is the record, if it has exactly one."""
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    hook = ((lambda doc: _with_state(doc, cr.inject_emitter_for_declared_paths(_state(doc), emit_paths)))
            if emit_paths else None)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "composite.pbg"
        export_composite_pbg(ws_root, composite_id, out, overrides=params, address_form="registry",
                             document_hook=hook)
        document = json.loads(out.read_text(encoding="utf-8"))
    if not emit_paths and len(top_level_emitters(document)) != 1:
        raise ComposeDispatchError({
            "error": "the study declares nothing to record (no observables) and its composite has "
                     f"{len(top_level_emitters(document))} emitters, not one, so a compose-api run would have no "
                     "single history to land",
            "reason": "nothing-to-emit"}, 422)
    return document


# -- the routes ----------------------------------------------------------------------------------------------------


def _backend_error(e: Exception) -> tuple[dict, int]:
    """A route's answer for a compose-api call that failed. Only a network failure is "unreachable"; a signed-out
    or refused caller is told so, and any other answer is compose-api's own error (still being polled)."""
    from compose_api_client.ext import ApiError, AuthError, ForbiddenError, NetworkError, RateLimitedError

    # An answer is checked before NetworkError: ServiceUnavailableError (502/503/504) and RateLimitedError (429) are
    # both NetworkErrors in the client's hierarchy, yet compose-api (or its proxy) did answer.
    if isinstance(e, RateLimitedError):
        return {"phase": "backend_error", "reachable": True, "reason": "rate limited", "error": str(e)}, 502
    if isinstance(e, ApiError):
        return {"phase": "backend_error", "reachable": True, "reason": "backend error", "error": str(e)}, 502
    if isinstance(e, NetworkError):
        return {"phase": "unreachable", "reachable": False, "reason": "backend unreachable", "error": str(e)}, 502
    if isinstance(e, AuthError):
        return {"phase": "error", "reachable": True, "reason": "unauthenticated",
                "error": f"compose-api needs a signed-in caller: set {TOKEN_ENV_VAR} ({e})"}, 401
    if isinstance(e, ForbiddenError):
        return {"phase": "error", "reachable": True, "reason": "forbidden", "error": str(e)}, 403
    return {"phase": "backend_error", "reachable": True, "reason": "backend error", "error": str(e)}, 502


def checked_steps(value: object) -> float:
    """A run's step count as compose-api takes it (``interval_time``): above 0 and at most :data:`MAX_STEPS`.
    Raises :class:`ComposeDispatchError` (400) rather than run fewer steps than were asked for."""
    try:
        steps = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ComposeDispatchError({"error": f"n_steps must be a number, got {value!r}", "reason": "bad-steps"},
                                   400) from None
    if not 0 < steps <= MAX_STEPS:
        raise ComposeDispatchError({"error": f"compose-api runs 1 to {MAX_STEPS} steps per run; this run asks for "
                                             f"{value}", "reason": "bad-steps", "max_steps": MAX_STEPS}, 400)
    return steps


def _tag(text: str) -> str:
    """``text`` as a compose-api tag (``labels.clean_tags``: 64 characters of letters, digits, `` _.:/+-``)."""
    cleaned = re.sub(r"[^A-Za-z0-9 _.:/+-]", "_", text).strip()[:64]
    return cleaned if cleaned and cleaned[0].isalnum() else f"x{cleaned}"[:64]


def _find_dispatched(session: "ComposeSession", dispatch_tag: str) -> "int | None":
    """The simulation a submission tagged ``dispatch_tag`` created, if compose-api has one (None when it has none
    or cannot be asked)."""
    from compose_api_client.ext import CliError

    for attempt in range(LOOKUP_ATTEMPTS):
        if attempt:
            time.sleep(LOOKUP_INTERVAL)  # the request may still be committing the run it created
        try:
            page = session.simulations(tags=[dispatch_tag], limit=1)
        except CliError:
            continue
        if page.simulations:
            return page.simulations[0].simulation_id
    return None


def _dispatch(session: "ComposeSession", ws_root: Path, composite_id: str, params: dict, steps: float,
              emit_paths: "list[str]", *, tags: "list[str]", description: str) -> "tuple[int, dict]":
    """Export, check and submit one composite: ``(simulation id, environment)``.

    Raises :class:`ComposeDispatchError` for a run that cannot be sent (409 no environment or a class not in it,
    422 a composite that cannot be exported) and for a submission whose answer was lost and whose run cannot be
    found by its dispatch tag (502, ``may_exist``). Other client errors propagate.
    """
    from compose_api_client.ext import NotFoundError, UncertainOutcomeError

    env = resolve_environment(session, ws_root)
    try:
        document = export_document(ws_root, composite_id, params, emit_paths)
    except ComposeDispatchError:
        raise
    except Exception as e:  # noqa: BLE001 - the export's own reason (an unregistered process) IS the answer
        raise ComposeDispatchError({"error": f"could not export composite {composite_id!r} as a document: {e}",
                                    "reason": "export-failed"}, 422) from e
    try:
        manifest = session.environment_manifest(env["name"])
    except NotFoundError as e:
        raise ComposeDispatchError({
            "error": f"compose-api has no interface manifest for environment {env['name']!r}, so composite "
                     f"{composite_id!r} cannot be checked against it ({e})",
            "reason": "no-manifest", "environment": env["name"]}, 409) from e
    problems = manifest_violations(document, [c.address for c in manifest.components], env["module_roots"])
    if problems:
        raise ComposeDispatchError({
            "error": f"environment {env['name']!r} cannot run composite {composite_id!r}: " + "; ".join(problems),
            "reason": "not-in-environment", "violations": problems, "environment": env["name"]}, 409)
    dispatch_tag = f"{DISPATCH_TAG_PREFIX}{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory() as td:
        pbg = Path(td) / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', composite_id)}.pbg"
        pbg.write_text(json.dumps(document), encoding="utf-8")
        try:
            experiment = session.submit(pbg, simulator=env["simulator"], interval=steps,
                                        tags=[*tags, dispatch_tag], description=description)
        except UncertainOutcomeError as e:
            # The answer was lost after the request may have arrived: never resubmit, find the run by its tag.
            found = _find_dispatched(session, dispatch_tag)
            if found is None:
                raise ComposeDispatchError({
                    "error": f"compose-api failed while accepting the run, so it may exist: find it on compose-api "
                             f"by tag {dispatch_tag!r} before running again ({e})",
                    "reason": "uncertain", "may_exist": True, "dispatch_tag": dispatch_tag,
                    "reachable": True}, 502) from e
            return found, env
    return int(experiment.simulation_database_id), env


def dispatch_preflight(ws_root: Path) -> dict:
    """``/api/remote-dispatch-preflight`` on compose-api: ``{target, dispatch, ok, reason, message, environment?}``,
    ready when an environment runs this workspace (:func:`resolve_environment`); its reason when none does."""
    from compose_api_client.ext import CliError

    out: dict = {"target": "deployment", "dispatch": BACKEND}
    try:
        with _session() as session:
            env = resolve_environment(session, Path(ws_root))
    except ComposeDispatchError as e:
        return {**out, **e.body, "ok": False, "message": e.body.get("error")}
    except CliError as e:
        body, _ = _backend_error(e)
        return {**out, **body, "ok": False, "message": body["error"]}
    return {**out, "ok": True, "reason": "environment", "environment": env["name"],
            "message": f"compose-api runs this workspace in environment {env['name']!r} ({env['image']}): "
                       "its image runs, not this checkout, so nothing needs pushing."}


def submit(ws_root: Path, body: dict) -> tuple[dict, int]:
    """``POST /simulation/run`` for a study's composite. ``({run_id, phase: "running", backend}, 202)``; 400 for a
    step count compose-api does not run, 409 when no environment runs the workspace or a class is not in it, 422
    for a composite that cannot be exported, 502 when compose-api cannot be reached or failed (never retried: a
    retry could submit twice; a submission whose answer was lost is looked up by its dispatch tag)."""
    from compose_api_client.ext import CliError, InvalidRequestError

    from vivarium_workbench.lib.study_runs import study_emit_paths

    study = (body.get("study") or "").strip()
    if not study:
        return {"error": "study is required"}, 400
    spec_path = study_spec.study_spec_path(ws_root, study)
    if spec_path is None or not spec_path.is_file():
        return {"error": f"study {study!r} not found"}, 404
    spec = load_spec(spec_path)
    baseline = (spec.get("baseline") or [{}])[0]
    composite_id = (body.get("composite") or baseline.get("composite") or "").strip()
    if not composite_id:
        return {"error": f"study {study!r} declares no baseline composite"}, 400
    params = dict(body["params"] if body.get("params") is not None else (baseline.get("params") or {}))
    n_steps = body.get("n_steps") or params.get("n_steps") or 1
    params.pop("n_steps", None)

    try:
        steps = checked_steps(n_steps)
        with _session() as session:
            simulation_id, env = _dispatch(
                session, ws_root, composite_id, params, steps, study_emit_paths(study_spec.study_dir(ws_root, study)),
                tags=["workbench", _tag(f"study:{study}")],
                description=f"vivarium-workbench: study {study}, {composite_id}")
    except ComposeDispatchError as e:
        return e.body, e.status
    except InvalidRequestError as e:
        return {"error": f"compose-api refused the run: {e.detail}", "reason": "refused", "reachable": True}, 409
    except CliError as e:
        return _backend_error(e)
    run_id = run_id_of(simulation_id)
    _record_pending(ws_root, study, composite_id, run_id)
    out = {"run_id": run_id, "phase": "running", "backend": BACKEND, "environment": env["name"]}
    if env["stale"]:
        out["manifest_warning"] = (f"compose-api's interface manifest for {env['name']!r} was read from another image "
                                   "than the one it runs, so the class check may be out of date")
    if head_matches_entry(ws_root, env["entry"]) is False:
        out["warning"] = (f"this checkout's HEAD is not commit {env['entry'].commit} that compose-api's registry pins "
                          f"for {env['entry'].id}: the run uses the environment's image ({env['image']}), "
                          "not this checkout")
    return out, 202


def run_composite(ws_root: Path, composite_id: str, *, dest: Path, n_steps: float, overrides: "dict | None",
                  emit_paths: "list[str] | None", poll_interval: float, poll_timeout: float) -> Path:
    """The Composites-tab remote run on compose-api (``remote_run.run_remote``'s branch): dispatch the composite,
    wait for it, and save its results archive in ``dest``. Raises ``RuntimeError`` naming why a run could not be
    sent or did not complete, and ``TimeoutError`` past ``poll_timeout`` (the run may still be going)."""
    from compose_api_client.ext import ApiTimeout, CliError, InvalidRequestError

    try:
        steps = checked_steps(n_steps)
        with _session() as session:
            simulation_id, env = _dispatch(
                session, Path(ws_root), composite_id, dict(overrides or {}), steps, list(emit_paths or []),
                tags=["workbench", _tag(f"composite:{composite_id}")],
                description=f"vivarium-workbench: composite {composite_id}")
            run_id = run_id_of(simulation_id)
            print(f"Submitted to compose-api ({env['name']}). Run id: {run_id}", flush=True)
            try:
                state = session.wait(simulation_id, poll=poll_interval,
                                     timeout=poll_timeout if poll_timeout and poll_timeout > 0 else None,
                                     on_update=lambda s: print(f"  status: {s.status}", flush=True))
            except ApiTimeout as e:
                raise TimeoutError(f"compose-api run {run_id} did not finish within {poll_timeout:.0f}s; it may "
                                   "still be running there") from e
            if not state.ok:
                error = state.record.error_message if state.record is not None else None
                raise RuntimeError(f"compose-api run {run_id} ended {state.status}"
                                   + (f": {error}" if isinstance(error, str) and error else ""))
            Path(dest).mkdir(parents=True, exist_ok=True)
            return session.download(simulation_id, Path(dest) / f"{run_id}-results.zip")
    except ComposeDispatchError as e:
        raise RuntimeError(str(e.body.get("error"))) from e
    except InvalidRequestError as e:
        raise RuntimeError(f"compose-api refused the run: {e.detail}") from e
    except CliError as e:
        raise RuntimeError(str(_backend_error(e)[0]["error"])) from e


def land(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Land a finished run's results as a study run: ``({run_id}, 200)``, the shape the legacy route answers.

    409 for a run that has not completed (nothing is downloaded), 404 when compose-api does not know it, 422 for
    an archive that is not one emitter's history, 502 when compose-api cannot be reached. Provenance is the
    backend's record of the run (its simulation, image and the registry entry's commit), never the checkout.
    """
    from compose_api_client.ext import CliError, NotFoundError

    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib.remote_run_landing import land_composite_results

    study = (body.get("study") or "").strip()
    run_id = str(body.get("simulation_id") or body.get("run_id") or "").strip()
    if not study or not is_run_id(run_id):
        return {"error": "study and a compose-api run id are required"}, 400
    spec_path = study_spec.study_spec_path(ws_root, study)
    if spec_path is None or not spec_path.is_file():
        return {"error": f"study {study!r} not found"}, 404
    baseline = (load_spec(spec_path).get("baseline") or [{}])[0]
    spec_id = baseline.get("composite") or study
    simulation_id = simulation_id_of(run_id)

    study_dir = study_spec.study_dir(ws_root, study)
    db_file = study_dir / "runs.db"
    if db_file.is_file():
        # Landing is idempotent: this run is landed once per study, and asking again answers that landing.
        conn = cr.connect(db_file)
        try:
            found = conn.execute(
                "SELECT run_id FROM runs_meta WHERE json_extract(params_json, '$.compose.run_id') = ?",
                (run_id,)).fetchone()
        finally:
            conn.close()
        if found is not None:
            return {"run_id": found[0], "already_landed": True}, 200

    try:
        with _session() as session:
            state = session.status(simulation_id)
            if not state.ok:
                return {"error": f"run {run_id!r} is {state.status}; only a completed run has results to land",
                        "phase": _phase(state.status), "run_id": run_id}, 409
            detail = session.simulation(simulation_id)
            image = _image_of(session, _text(detail.simulator))
            with tempfile.TemporaryDirectory() as td:
                archive = Path(td) / "results.zip"
                archive.write_bytes(session.results(simulation_id))
                if not zipfile.is_zipfile(archive):
                    return {"error": f"simulation {simulation_id} served something other than a results archive",
                            "run_id": run_id}, 422
                deployment = remote_pinned.remote_deployment_name()
                provenance = {
                    "source": deployment, "backend": BACKEND,
                    "compose": {"run_id": run_id, "simulation_id": simulation_id,
                                "experiment_id": detail.experiment_id, "image": image,
                                "slurm_job_id": state.slurm_job},
                }
                landed = cr.generate_run_id(spec_id, params=provenance)
                try:
                    n_steps = land_composite_results(archive, db_file, landed, emitter=STUDY_EMITTER)
                except ValueError as e:
                    return {"error": f"simulation {simulation_id}'s results cannot be landed: {e}",
                            "run_id": run_id}, 422
    except ComposeDispatchError as e:
        return {**e.body, "run_id": run_id}, e.status
    except NotFoundError as e:
        return {"error": str(e), "run_id": run_id}, 404
    except CliError as e:
        return {**_backend_error(e)[0], "run_id": run_id}, _backend_error(e)[1]

    manifest = cr.build_run_manifest(
        spec_id=spec_id, params=provenance, n_steps=n_steps, emitter="sqlite", emit_paths=[], runtime={},
        origin="remote", study=None, generation_id=None, ws_root=None)
    # The code that ran is the image's, named by its digest; the checkout is not what ran.
    manifest["code_version"] = {"git_sha": None, "package": None, "repo": None, "remote_url": None,
                                "image": image}
    conn = cr.connect(db_file)
    try:
        try:
            cr.save_metadata(conn, spec_id=spec_id, run_id=landed, params=provenance,
                             label=f"Remote run ({deployment})", started_at=time.time(), n_steps=n_steps,
                             workspace=ws_root, manifest=manifest)
            cr.complete_metadata(conn, run_id=landed, n_steps=n_steps, status="completed")
        except Exception:
            # No history rows without the run they belong to.
            with conn:
                conn.execute("DELETE FROM history WHERE simulation_id = ?", (landed,))
            raise
        cr.delete_run(conn, run_id=f"remote-pending-{run_id}", workspace=ws_root)
    finally:
        conn.close()
    return {"run_id": landed}, 200


def _image_of(session: "ComposeSession", simulator: "str | None") -> "str | None":
    """The image a run ran in. compose-api's ``simulator`` is a prebuilt simulator's NAME when the deployment lists
    its image (``simulations._simulator_label``), else the image itself; the name resolves to the image (a digest)
    through ``GET /registry/environments``."""
    if simulator is None:
        return None
    env = next((e for e in session.environments() if e.simulator == simulator and e.image), None)
    return env.image if env is not None else simulator


def _record_pending(ws_root: Path, study: str, spec_id: str, run_id: str) -> None:
    """A Runs-tab placeholder from the moment compose-api accepted the run (best-effort: a visibility row must
    never fail an already-dispatched run). Same id scheme as the other routes, so landing can replace it."""
    try:
        from vivarium_workbench.lib import composite_runs as cr

        deployment = remote_pinned.remote_deployment_name()
        conn = cr.connect(study_spec.study_dir(ws_root, study) / "runs.db")
        try:
            cr.save_metadata(
                conn, spec_id=spec_id, run_id=f"remote-pending-{run_id}",
                params={"source": deployment, "simulation_id": run_id, "backend": BACKEND},
                label=f"Remote dispatch ({deployment}) — in progress",
                started_at=time.time(), n_steps=0, workspace=ws_root,
            )
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass


def _phase(raw: str) -> str:
    raw = str(raw or "").lower()
    return ("done" if raw in _TERMINAL_OK else "failed" if raw in _TERMINAL_BAD
            else "queued" if raw in _QUEUED else "running")


def status(run_id: str) -> tuple[dict, int]:
    """``GET /results/simulation/status`` in the ``remote_run_status`` shape (``submitting`` while compose-api
    has not created the SLURM job yet)."""
    from compose_api_client.ext import CliError, NotFoundError

    try:
        with _session() as session:
            state = session.status(simulation_id_of(run_id))
            if state.status == "submitting":
                # The status route answers 404 both before SLURM has the job and for no such simulation (the client
                # reads either as "submitting"); the simulation itself tells them apart.
                session.simulation(simulation_id_of(run_id))
    except ComposeDispatchError as e:
        return {**e.body, "phase": "error", "run_id": run_id}, e.status
    except NotFoundError as e:
        return {"phase": "not_found", "error": str(e), "run_id": run_id}, 404
    except CliError as e:
        body, code = _backend_error(e)
        return {**body, "run_id": run_id}, code
    error = state.record.error_message if state.record is not None and state.record.error_message else None
    return {"kind": "run", "phase": _phase(state.status), "raw_status": state.status,
            "error": error if isinstance(error, str) else None, "run_id": run_id}, 200


def progress(run_id: str) -> tuple[dict, int]:
    """The ``remote_run_chain_progress`` shape for one compose-api run: a single job, no seeds."""
    body, code = status(run_id)
    if code != 200:
        # A refusal (no backend, signed out, no such run) will not change by polling again; a failed answer may.
        return {"kind": "chain_progress", **body, "terminal": code < 500, "simulation_id": run_id}, code
    phase = body["phase"]
    return {"kind": "chain_progress", "phase": phase, "terminal": phase in ("done", "failed"),
            "seeds_total": None, "seeds_succeeded": None, "seeds_failed": None, "seeds_in_progress": None,
            "simulation_id": run_id, "run_id": run_id}, 200


def cancel(run_id: str) -> tuple[dict, int]:
    """``DELETE /simulations/{id}``: needs a signed-in caller (``COMPOSE_API_TOKEN``). 401/403/404/409 pass
    through; 502 only when compose-api is unreachable or failed."""
    from compose_api_client.ext import ApiError, CliError, NotFoundError

    ids = {"kind": "cancel", "run_id": run_id, "simulation_id": run_id}
    try:
        with _session() as session:
            out = session.cancel_simulation(simulation_id_of(run_id))
    except ComposeDispatchError as e:
        return {**e.body, **ids}, e.status
    except NotFoundError as e:
        return {"error": str(e), **ids}, 404
    except ApiError as e:
        if e.status_code == 409:
            return {"error": str(e.detail), "reason": "not-cancellable", **ids}, 409
        body, code = _backend_error(e)
        return {**body, **ids}, code
    except CliError as e:
        body, code = _backend_error(e)
        return {**body, **ids}, code
    return {**ids, "withdrawn": out.withdrawn,
            "slurm_job_id": out.slurm_job_id if isinstance(out.slurm_job_id, int) else None}, 200


def trace(run_id: str) -> "tuple[bytes | dict, int, str]":
    """The run's Chrome Trace Event JSON (``GET /results/simulation/trace/chrome``)."""
    from compose_api_client.ext import CliError, NotFoundError

    try:
        with _session() as session:
            doc = session.trace_chrome(simulation_id_of(run_id))
    except ComposeDispatchError as e:
        return e.body, e.status, ""
    except NotFoundError as e:
        return {"error": str(e)}, 404, ""
    except CliError as e:
        body, code = _backend_error(e)
        return body, code, ""
    return json.dumps(doc).encode("utf-8"), 200, f"{run_id}-trace.json"


def head_matches_entry(ws_root: Path, entry: Any) -> "bool | None":
    """Whether the checkout's HEAD is the commit the registry entry pins (None when it pins none)."""
    commit = getattr(entry, "commit", None)
    head = _head(ws_root)
    if not isinstance(commit, str) or not commit or not head:
        return None
    return head.startswith(commit) or commit.startswith(head)
