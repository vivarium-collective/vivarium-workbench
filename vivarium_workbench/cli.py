"""vivarium-workbench CLI - serve a workspace via the workbench."""
from __future__ import annotations
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import warnings
from pathlib import Path

import yaml


def _pick_free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _workspace_name(workspace: Path) -> str:
    """Read `name` from <workspace>/workspace.yaml, falling back to dir name."""
    try:
        data = yaml.safe_load((workspace / "workspace.yaml").read_text(encoding="utf-8")) or {}
        return data.get("name") or workspace.name
    except (OSError, yaml.YAMLError):
        return workspace.name


def cmd_serve(args: argparse.Namespace) -> int:
    """Render the workspace dashboard once and start the HTTP server."""
    workspace = Path(args.workspace).resolve()
    if not (workspace / "workspace.yaml").is_file():
        print(f"ERROR: not a workspace (no workspace.yaml): {workspace}", file=sys.stderr)
        return 2

    if getattr(args, "detach", False):
        return _serve_detached(workspace, args)

    # Make the workspace's own package importable for the render step
    # (e.g. pbg_chromosome_rep1.core.build_core), and register the workspace
    # root for lib helpers.
    ws_str = str(workspace)
    if ws_str not in sys.path:
        sys.path.insert(0, ws_str)
    from vivarium_workbench.lib._root import set_workspace_root
    set_workspace_root(workspace)

    from vivarium_workbench.publish import _normalize_base_path
    base_path = _normalize_base_path(getattr(args, "base_path", "") or "")

    if getattr(args, "trust_proxy", False):
        os.environ["VIVARIUM_WORKBENCH_TRUST_PROXY"] = "1"

    allowed = getattr(args, "allowed_origin", None) or []
    if allowed:
        os.environ["VIVARIUM_WORKBENCH_ALLOWED_ORIGINS"] = ",".join(allowed)

    # Render the dashboard HTML once before serving.
    try:
        from vivarium_workbench.lib.report import render_dashboard
        render_dashboard(workspace, write_all=True, base_path=base_path)
    except Exception as e:
        print(f"warning: dashboard render failed: {e}", file=sys.stderr)

    # Pick port + write server-info ahead of boot (server.serve() also writes
    # one, but writing it here ensures the URL is printed below correctly).
    port = args.port or _pick_free_port()
    server_dir = workspace / ".pbg" / "server"
    server_dir.mkdir(parents=True, exist_ok=True)
    info = {
        "port": port,
        "host": "127.0.0.1",
        "url": f"http://127.0.0.1:{port}",
        "pid": os.getpid(),
        "screen_dir": str(server_dir / "content"),
        "state_dir": str(server_dir / "state"),
    }
    (server_dir / "server-info").write_text(json.dumps(info))

    # Write PID file (consumed by /pbg-server stop and the switcher's
    # cleanup-stale endpoint).
    pid_file = server_dir / "server.pid"
    pid_file.write_text(str(os.getpid()))

    def _unregister():
        try:
            from viva_superpowers import workspace_catalog
            workspace_catalog.unregister_server(workspace)
        except Exception:
            pass
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass

    # Register the cleanup hook FIRST so pid_file is always removed, even
    # if registration in the global running registry fails below.
    import atexit
    atexit.register(_unregister)

    # Register the running dashboard in ~/.pbg/servers/<name>.json so the
    # workspace switcher in other dashboards can see it. Failure here is
    # non-fatal — the dashboard still works, it just won't appear in other
    # dashboards' switchers.
    try:
        from viva_superpowers import workspace_catalog
        ws_name = _workspace_name(workspace)
        # Ensure this workspace appears in OTHER dashboards' switchers.
        # add() is idempotent; safe to call on every boot.
        workspace_catalog.add(workspace)
        workspace_catalog.register_server(
            name=ws_name, path=workspace,
            pid=os.getpid(), port=port,
            url=f"http://127.0.0.1:{port}",
        )
        import signal as _signal

        def _sig_handler(signum, frame):
            _unregister()
            sys.exit(0)

        _signal.signal(_signal.SIGTERM, _sig_handler)
    except Exception as e:
        print(f"warning: workspace switcher registration failed: {e}", file=sys.stderr)

    host = getattr(args, "host", None) or "127.0.0.1"
    advertise_host = "127.0.0.1" if host == "0.0.0.0" else host
    print(f"\nWorkspace dashboard: http://{advertise_host}:{port}")
    if host == "0.0.0.0":
        print("   (bound on all interfaces — reachable from outside this host)")
    print("   (Ctrl-C to stop)\n")

    # Boot the FastAPI app under uvicorn (the migration's typed seam is now the
    # served entrypoint; the legacy stdlib server.serve path is retired).
    from vivarium_workbench.lib.startup import serve_fastapi
    return serve_fastapi(workspace=workspace, port=port, host=host, base_path=base_path)


def migrate_investigations_to_studies(ws_root: Path, dry_run: bool = False) -> dict:
    """One-shot: walk investigations/, rename → studies/, migrate spec v2→v3.

    Returns {migrated|would_migrate: N, errors: [{name, error}], warnings: [...]}.
    Idempotent: if investigations/ does not exist, returns migrated=0 immediately.
    """
    from vivarium_workbench.lib.spec_migration import migrate_v2_to_v3

    inv_root = ws_root / "investigations"
    studies_root = ws_root / "studies"

    if not inv_root.is_dir():
        return {"migrated": 0, "errors": [], "warnings": ["no investigations/ to migrate"]}

    count_key = "would_migrate" if dry_run else "migrated"
    result: dict = {count_key: 0, "errors": [], "warnings": []}

    for inv in sorted(inv_root.iterdir()):
        if not inv.is_dir():
            continue
        spec_path = inv / "spec.yaml"
        if not spec_path.is_file():
            continue
        try:
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                v3 = migrate_v2_to_v3(spec)
            for w in caught:
                result["warnings"].append(f"{inv.name}: {w.message}")

            if dry_run:
                result["would_migrate"] += 1
                continue

            studies_root.mkdir(parents=True, exist_ok=True)
            dst = studies_root / inv.name
            if dst.exists():
                result["errors"].append({"name": inv.name,
                                         "error": "destination already exists"})
                continue

            inv.rename(dst)
            # Rename spec.yaml → study.yaml and write v3 content
            (dst / "spec.yaml").rename(dst / "study.yaml")
            (dst / "study.yaml").write_text(yaml.safe_dump(v3, sort_keys=False))
            result["migrated"] += 1
        except Exception as e:
            result["errors"].append({"name": inv.name, "error": str(e)})

    # If investigations/ is now empty, remove it.
    if not dry_run and inv_root.is_dir() and not any(inv_root.iterdir()):
        inv_root.rmdir()

    return result


def cmd_migrate_investigations(args: argparse.Namespace) -> int:
    """CLI handler for the migrate-investigations subcommand."""
    ws = Path(args.workspace).resolve()
    result = migrate_investigations_to_studies(ws, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


def cmd_migrate_studies(args: argparse.Namespace) -> int:
    """CLI handler for the migrate-studies subcommand."""
    from vivarium_workbench.lib.study_migrate import migrate_studies
    ws = Path(args.workspace).resolve()
    result = migrate_studies(ws, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


def cmd_migrate_artifacts(args: argparse.Namespace) -> int:
    """CLI handler for the migrate-artifacts subcommand."""
    from vivarium_workbench.lib.artifacts.migrate import migrate_artifacts
    ws = Path(args.workspace).resolve()
    report = migrate_artifacts(ws, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        if args.dry_run:
            print("DRY RUN — nothing was moved\n")
        print(report.summary())
    return 0


def cmd_run_composite_worker(args: argparse.Namespace) -> int:
    """CLI handler for the run-composite subcommand — runs one detached composite."""
    # Diagnostics for issue #754: when the Runs-tab Stop button signals a frozen
    # run (SIGTERM, via run_registry.stop_run), dump every thread's stack into
    # this worker's run.log (stdout/stderr are redirected there) before exiting,
    # then chain to the default handler so the process still terminates. This
    # turns an opaque "stuck run, force-quit" into an actionable traceback of
    # exactly what the job was blocked on.
    import faulthandler
    import signal
    faulthandler.enable()
    try:
        faulthandler.register(signal.SIGTERM, all_threads=True, chain=True)
    except (AttributeError, ValueError):
        pass  # SIGTERM unavailable on this platform (e.g. Windows) — skip
    from vivarium_workbench.lib.run_runner import execute
    return execute(Path(args.request))


# ---------------------------------------------------------------------------
# User-facing run/rerun/runs/status/logs helpers
# ---------------------------------------------------------------------------

def _emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


def _parse_params(pairs) -> dict:
    out = {}
    for p in (pairs or []):
        if "=" not in p:
            raise SystemExit(f"--param must be key=value, got: {p!r}")
        k, v = p.split("=", 1)
        try:
            out[k] = json.loads(v)
        except Exception:
            out[k] = v
    return out


def cmd_run_study(args) -> int:
    from vivarium_workbench.lib import cli_runs
    params = _parse_params(args.param)
    if args.seed is not None:
        params["seed"] = args.seed
    resp, code = cli_runs.run_study(
        Path(args.workspace).resolve(), args.slug,
        variant=args.variant, steps=args.steps,
        params=params, dry_run=args.dry_run,
        detach=args.detach, server=args.server)
    _emit(resp, args.json)
    if code < 400 and not args.dry_run and resp.get("run_id"):
        print(f"\nFollow:  vdash status {resp['run_id']}")
        print(f"Rerun:   vwb rerun {resp['run_id']}")
    return 0 if code < 400 else 1


def cmd_run_investigation(args) -> int:
    from vivarium_workbench.lib import cli_runs
    studies = args.studies.split(",") if args.studies else None
    resp, code = cli_runs.run_investigation(
        Path(args.workspace).resolve(), args.slug,
        studies=studies, steps=args.steps, server=args.server)
    _emit(resp, args.json)
    return 0 if code < 400 else 1


def cmd_run_composite(args) -> int:
    from vivarium_workbench.lib import cli_runs
    emit = args.emit.split(",") if args.emit else None
    resp, code = cli_runs.run_composite(
        Path(args.workspace).resolve(), args.spec_id,
        steps=args.steps, emit_paths=emit,
        dry_run=args.dry_run, detach=args.detach)
    _emit(resp, args.json)
    if code < 400 and not args.dry_run and resp.get("run_id"):
        print(f"\nFollow:  vdash status {resp['run_id']}")
        print(f"Rerun:   vwb rerun {resp['run_id']}")
    return 0 if code < 400 else 1


def cmd_run_process(args) -> int:
    from vivarium_workbench.lib import cli_runs
    config = _parse_params(args.config) if args.config else None
    resp, code = cli_runs.run_process(
        Path(args.workspace).resolve(), args.address, config=config)
    _emit(resp, args.json)
    return 0 if code < 400 else 1


def cmd_rerun(args) -> int:
    # Single canonical rerun path (reproducible-rerun-spine Task 1):
    # lib.rerun.run_rerun replays the recorded manifest verbatim (exact
    # reproduction, routed to the study or composite launcher by origin).
    # The legacy `cli_runs.rerun` path (composite-only, delta params, no
    # manifest) is retired here; --steps/--detach no longer override an
    # exact replay, so warn rather than silently ignoring them.
    from vivarium_workbench.lib import rerun as rerun_lib
    if args.steps is not None or args.detach:
        print("warning: --steps/--detach are deprecated on `rerun` and have "
              "no effect — rerun always replays the recorded manifest "
              "exactly. Use `run study`/`run composite` for a fresh run "
              "with different parameters.", file=sys.stderr)
    resp, code = rerun_lib.run_rerun(Path(args.workspace).resolve(), args.run_id)
    _emit(resp, args.json)
    return 0 if code < 400 else 1


def cmd_runs(args) -> int:
    from vivarium_workbench.lib import cli_runs
    rows = cli_runs.list_study_runs(Path(args.workspace).resolve(), args.slug)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        for r in rows:
            print(f"{r['run_id']:50}  {r.get('status',''):10}  "
                  f"steps={r.get('n_steps','')}")
    return 0


def cmd_status(args) -> int:
    from vivarium_workbench.lib import cli_runs
    _db, row = cli_runs.find_run(Path(args.workspace).resolve(), args.run_id)
    if row is None:
        print(f"run not found: {args.run_id}")
        return 1
    _emit(row, args.json)
    return 0


_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "error", "complete", "orphaned"}


def cmd_logs(args) -> int:
    from vivarium_workbench.lib import cli_runs
    ws = Path(args.workspace).resolve()
    text = cli_runs.read_run_log(ws, args.run_id)
    if text is None:
        print(f"no log for run: {args.run_id}")
        return 1
    print(text)
    if not args.follow:
        return 0

    # Check if already terminal — if so, nothing to follow.
    _db, row = cli_runs.find_run(ws, args.run_id)
    if row is None or row.get("status") in _TERMINAL_STATUSES:
        return 0

    # Poll for appended content until terminal status or timeout.
    printed = len(text)
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        time.sleep(1)
        new_text = cli_runs.read_run_log(ws, args.run_id)
        if new_text and len(new_text) > printed:
            sys.stdout.write(new_text[printed:])
            sys.stdout.flush()
            printed = len(new_text)
        _db, row = cli_runs.find_run(ws, args.run_id)
        if row is None or row.get("status") in _TERMINAL_STATUSES:
            break
    return 0


def _normalize_repo_url(repo: str) -> str:
    """Turn a repo reference into a git-cloneable URL. Accepts full URLs
    (http/https/ssh) as-is, `host/org/repo` shorthands, and `org/repo`
    (assumed GitHub)."""
    import re
    repo = repo.strip()
    if repo.startswith(("http://", "https://", "git@", "ssh://")):
        return repo
    if re.match(r"^(github\.com|gitlab\.com|bitbucket\.org)/", repo):
        return "https://" + repo
    if re.match(r"^[\w.\-]+/[\w.\-]+$", repo):   # bare org/repo → GitHub
        return "https://github.com/" + repo
    return repo


def _parse_repo_at_commit(source: str):
    """Detect a ``<repo>@<ref>`` sync spec and return ``(repo_url, ref)``, or
    None if ``source`` isn't one (a manifest file path or a published-dashboard
    URL — neither of which carries a trailing ``@<ref>``).

    Splits on the LAST ``@`` and requires the ref to be a single path-less git
    ref, so an ssh URL (``git@host:org/repo@sha``) keeps its user@host while a
    plain manifest URL (no trailing ``@ref``) falls through.
    """
    import re
    repo_spec, sep, ref = source.rpartition("@")
    if not sep or not repo_spec or not ref:
        return None
    if "/" in ref or ":" in ref or not re.match(r"^[\w.\-]+$", ref):
        return None
    if "/" not in repo_spec and ":" not in repo_spec:   # need org/repo or ssh host:path
        return None
    return _normalize_repo_url(repo_spec), ref


def _synthesize_manifest(repo_url: str, ref: str) -> dict:
    """Build a minimal sync manifest for an arbitrary ``repo@commit``. The
    commit IS the source of truth here (there's no published bundle to verify
    against), so ``lockfile`` is left unset — the fidelity gate is skipped and
    ``uv sync`` materializes whatever that commit pins."""
    base = repo_url.rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    workspace = base.split("/")[-1].split(":")[-1]
    return {"repo": repo_url, "commit": ref, "lockfile": None, "workspace": workspace}


def _load_manifest(source: str) -> dict:
    """Resolve a sync source into a manifest dict. Accepts, in order:
      - ``<repo>@<ref>``      — synthesize a manifest to reproduce ANY commit
                                (e.g. ``github.com/org/repo@1a2b3c4`` or
                                ``org/repo@main``), published or not;
      - ``http(s)://…``       — a JSON manifest, or a dashboard base URL whose
                                ``/api/source/manifest`` is fetched;
      - a file path / file:// — a manifest JSON on disk.
    """
    import json
    import urllib.request

    repo_at = _parse_repo_at_commit(source)
    if repo_at:
        return _synthesize_manifest(*repo_at)

    if source.startswith(("http://", "https://")):
        url = source.rstrip("/")
        if not url.endswith("/api/source/manifest"):
            url = url + "/api/source/manifest"
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode())
    if source.startswith("file://"):
        source = source[len("file://"):]
    return json.loads(Path(source).read_text())


def cmd_run_remote(args: argparse.Namespace) -> int:
    """CLI handler for the run-remote subcommand.

    Validates the workspace git tree is clean and pushed, exports the named
    composite to a .pbg document, submits it to sms-api, polls until
    completion, and lands results.zip in the workspace.
    """
    from vivarium_workbench.lib.remote_run import run_remote
    from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError
    from vivarium_workbench.lib.workspace_deps_views import _sms_api_base

    workspace = Path(args.workspace).resolve()
    if not (workspace / "workspace.yaml").is_file():
        print(f"ERROR: not a workspace (no workspace.yaml): {workspace}", file=sys.stderr)
        return 2

    base_url = getattr(args, "sms_api_url", None) or _sms_api_base()
    client = SmsApiClient(base_url)

    dest = Path(args.dest) if getattr(args, "dest", None) else None

    try:
        results = run_remote(
            workspace,
            args.composite,
            client=client,
            poll_interval=getattr(args, "poll_interval", 10.0),
            dest=dest,
        )
        print(f"Done. Results: {results}")
        return 0
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except SmsApiError as e:
        print(f"ERROR (sms-api): {e}", file=sys.stderr)
        return 1


def cmd_sync(args) -> int:
    from vivarium_workbench.lib.sync_workspace import sync_from_manifest

    manifest = _load_manifest(args.manifest)
    dest = Path(args.dest) if args.dest else Path.cwd() / (manifest.get("workspace") or "workspace")
    body, status = sync_from_manifest(manifest, dest, run_post_sync=args.run_post_sync)
    if status == 200:
        print(f"synced {manifest.get('repo')}@{manifest.get('commit', '')[:7]} -> {body['path']}")
        print(f"registered as workspace '{manifest.get('workspace')}'. Open it from the switcher.")
        return 0
    print(f"sync failed ({status}): {body.get('error', body)}")
    return 1


def cmd_warm_catalog(args: argparse.Namespace) -> int:
    """Pre-build the persistent on-disk Registry + Composites catalog cache
    for a workspace (``lib.catalog_disk_cache``) so a cold worker/pod never
    pays the multi-minute "import every installed process package" walk on
    its first request — run this as a Dockerfile ``RUN`` step at image-build
    time.
    """
    ws = Path(args.workspace).resolve()
    if not (ws / "workspace.yaml").is_file():
        print(f"error: not a workspace (no workspace.yaml): {ws}", file=sys.stderr)
        return 2

    # Make the workspace's own package importable, same as `serve` does, so
    # build_registry/composites_via_subprocess (and the framework's own
    # source-signature walk) see the real workspace package.
    ws_str = str(ws)
    if ws_str not in sys.path:
        sys.path.insert(0, ws_str)
    from vivarium_workbench.lib._root import set_workspace_root
    set_workspace_root(ws)

    from vivarium_workbench.lib import catalog_disk_cache
    from vivarium_workbench.lib.composites_query import composites_via_subprocess
    from vivarium_workbench.lib.registry import build_registry

    reg = build_registry(ws, bypass_cache=True)
    comps = composites_via_subprocess(ws, bypass_cache=True)

    n_processes = len((reg or {}).get("processes") or []) if isinstance(reg, dict) else 0
    n_composites = len((comps or {}).get("composites") or []) if isinstance(comps, dict) else 0
    cache_files = sorted(p.name for p in catalog_disk_cache.cache_dir(ws).glob("*.json"))

    if isinstance(reg, dict) and reg.get("error"):
        print(f"warning: registry build reported an error: {reg['error']}", file=sys.stderr)
    if comps is None:
        print("warning: composites build returned no result", file=sys.stderr)
    elif isinstance(comps, dict) and comps.get("error"):
        print(f"warning: composites build reported an error: {comps['error']}", file=sys.stderr)

    print(f"warmed catalog: {n_processes} process(es), {n_composites} composite(s), "
          f"{len(cache_files)} cache file(s) written -> {catalog_disk_cache.cache_dir(ws)}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report on framework-dependency health; exit non-zero if any are stale."""
    from vivarium_workbench.lib import dep_doctor

    print(dep_doctor.format_report())
    return 1 if dep_doctor.problems() else 0


def cmd_smoke(args: argparse.Namespace) -> int:
    """Local spine smoke check (docs/dual-engine-comparison.md §5.4)."""
    from pathlib import Path as _Path

    from vivarium_workbench.lib import smoke

    ws = _Path(args.workspace) if getattr(args, "workspace", None) else None
    return smoke.run_smoke(ws)


def cmd_prepare_investigation(args: argparse.Namespace) -> int:
    """CLI handler: prepare an investigation's coordinated generation."""
    from vivarium_workbench.lib.prepare_investigation import prepare_investigation
    workspace = Path(args.workspace).resolve()
    if not (workspace / "workspace.yaml").is_file():
        print(f"ERROR: not a workspace (no workspace.yaml): {workspace}", file=sys.stderr)
        return 2
    prepare_investigation(
        workspace,
        investigation=args.investigation,
        study=args.study,
        steps=args.steps,
        render_only=args.render_only,
        dashboard_url=args.dashboard_url,
        param_set=args.param_set,
    )
    return 0


def cmd_gen_readme(args: argparse.Namespace) -> int:
    """Regenerate (or --check) a workspace README's generated composite +
    investigation tables from the workspace itself."""
    ws = Path(args.workspace).resolve()
    if not (ws / "workspace.yaml").is_file():
        print(f"error: not a workspace (no workspace.yaml): {ws}", file=sys.stderr)
        return 2
    from vivarium_workbench.gen_readme import generate

    return generate(ws, check=args.check, readme=args.readme)


def cmd_audit(args: argparse.Namespace) -> int:
    """Run the L0-L5 reproducibility audit and print / export the result."""
    ws = Path(args.workspace).resolve()
    if not (ws / "workspace.yaml").is_file():
        print(f"ERROR: not a workspace (no workspace.yaml): {ws}", file=sys.stderr)
        return 2
    from vivarium_workbench.lib.audit_views import build_audit
    report, _ = build_audit(ws)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0
    if args.html:
        from datetime import datetime, timezone
        from vivarium_workbench.lib.audit_report import render_audit_html
        out = Path(args.html)
        out.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        out.write_text(render_audit_html(ws, generated_at=now), encoding="utf-8")
        print(f"wrote audit report -> {out}")
        return 0

    summ = report.get("summary", {})
    print(f"Reproducibility audit — {summ.get('n_studies', 0)} studies, "
          f"{summ.get('n_investigations', 0)} investigations")
    dist = summ.get("grade_distribution", {})
    if dist:
        print("  grade: " + "   ".join(f"{k} {dist[k]}"
              for k in ["L5", "L4", "L3", "L2", "L1", "L0", "—"] if dist.get(k)))
    if report.get("error"):
        print(f"  (degraded: {report['error']})")
    print()

    def _rows(items):
        for it in items:
            g = it.get("grade") or {}
            b = g.get("blocked_by")
            tail = f"blocked: {b['level']} {b['name']}" if b else "full L5"
            print(f"  {g.get('label', '—'):>3}  {it.get('slug', ''):42} {tail}")

    _rows(report.get("studies", []))
    if report.get("investigations"):
        print()
        _rows(report.get("investigations", []))
    return 0


# ----------------------------------------------------------------------------
# add-dashboard: scaffold a robust read-only-dashboard publish for any workspace
# ----------------------------------------------------------------------------
# The workflow is deliberately ROBUST: the static dashboard build needs only
# vivarium-workbench + the workspace's OWN package importable for composite
# specs, NOT the sibling process-packages a workspace may declare by relative
# path (those don't resolve on a clean CI runner). So it installs the workbench
# from main and the workspace with --no-deps, and creates the gh-pages branch if
# it doesn't exist yet — so `add-dashboard` works on a fresh repo.
_DASHBOARD_WORKFLOW = r"""name: Publish read-only dashboard

# Build this workspace into a self-contained static SPA and publish it to the
# gh-pages branch at {PUBLISH_DIR}/ (served at
# https://{ORG}.github.io{BASE_PATH}/). A read-only mirror of every
# investigation + study on main, browsable with no server.
#
# Scaffolded by `vivarium-workbench add-dashboard`. DEPLOY job (post-merge), not
# a PR gate; it only writes {PUBLISH_DIR}/ (+ a /dashboard redirect) on gh-pages.

on:
  push:
    branches: [main]
    paths:
      - 'workspace/**'
      - 'investigations/**'
      - 'studies/**'
      - 'workspace.yaml'
      - 'reports/figures/**'
      - 'scripts/publish_dashboard.sh'
      - '.github/workflows/publish-dashboard.yml'
  workflow_dispatch:

concurrency:
  group: publish-dashboard
  cancel-in-progress: true

permissions:
  contents: write   # push to the gh-pages branch

jobs:
  publish:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    env:
      VIVARIUM_WORKBENCH_REQUIRE_LOOM: "1"
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Set up Python
        run: uv python install 3.12.9

      - name: Install vivarium-workbench (robust — skip workspace sim-deps)
        # Only vivarium-workbench + this workspace's own package are needed to
        # render composite specs statically. The workspace's sibling
        # process-packages (often relative-path deps) do NOT resolve on a clean
        # runner and are NOT needed for the static build, so install the
        # workspace with --no-deps: a missing sim-dep degrades to an unresolved
        # composite in the dashboard rather than aborting the whole publish.
        run: |
          uv venv
          uv pip install "vivarium-workbench @ git+https://github.com/vivarium-collective/vivarium-workbench.git@main"
          uv pip install -e . --no-deps || echo "workspace has no installable build; composites render from spec only"

      - name: Build workbench snapshot
        run: |
          source .venv/bin/activate
          bash scripts/publish_dashboard.sh reports/published/{PUBLISH_DIR}

      - name: Publish to gh-pages (create the branch if absent)
        run: |
          set -euo pipefail
          if [ ! -f reports/published/{PUBLISH_DIR}/index.html ]; then
            echo "::error::build produced no bundle (no index.html); nothing to publish"
            exit 1
          fi
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git fetch origin gh-pages 2>/dev/null || true
          git worktree add --detach /tmp/ghp HEAD
          cd /tmp/ghp
          if git rev-parse --verify origin/gh-pages >/dev/null 2>&1; then
            git checkout -B gh-pages origin/gh-pages
          else
            git checkout --orphan gh-pages
            git rm -rf . >/dev/null 2>&1 || true
            touch .nojekyll
          fi
          rm -rf {PUBLISH_DIR} && mkdir -p {PUBLISH_DIR}
          cp -R "$GITHUB_WORKSPACE/reports/published/{PUBLISH_DIR}/." {PUBLISH_DIR}/
          # Back-compat: keep the legacy /dashboard URL working by leaving a
          # redirect stub pointing at the new /{PUBLISH_DIR}/ (skipped when the
          # publish dir IS dashboard, i.e. a repo hasn't migrated yet).
          if [ "{PUBLISH_DIR}" != "dashboard" ]; then
            mkdir -p dashboard
            printf '%s' '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=../{PUBLISH_DIR}/"><link rel="canonical" href="../{PUBLISH_DIR}/"><title>Moved to /{PUBLISH_DIR}/</title><p>This read-only workbench moved to <a href="../{PUBLISH_DIR}/">/{PUBLISH_DIR}/</a>.</p>' > dashboard/index.html
          fi
          git add -A
          if git diff --cached --quiet; then
            echo "workbench snapshot unchanged; skipping commit"
          else
            git commit -m "gh-pages: republish read-only workbench (automated, ${GITHUB_SHA::7})"
            git push origin gh-pages
            echo "published read-only workbench to gh-pages:{PUBLISH_DIR}/"
          fi
"""

_DASHBOARD_SCRIPT = r"""#!/usr/bin/env bash
# Build the read-only dashboard SPA for this workspace (see the companion
# .github/workflows/publish-dashboard.yml). Scaffolded by
# `vivarium-workbench add-dashboard`; run it locally to preview the bundle.
set -euo pipefail
WS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$WS_ROOT/reports/published/{PUBLISH_DIR}}"
BASE_PATH="{BASE_PATH}"
INTERACTIVE_URL="{INTERACTIVE_URL}"
rm -rf "$OUT"
PYTHONPATH="$WS_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  vivarium-workbench-publish \
    --workspace "$WS_ROOT" \
    --out "$OUT" \
    --base-path "$BASE_PATH" \
    --interactive-url "$INTERACTIVE_URL"
find "$OUT" -name '*.map' -delete
touch "$OUT/.nojekyll"
echo "built read-only dashboard bundle at $OUT ($(du -sh "$OUT" | cut -f1))"
"""


def cmd_add_dashboard(args: argparse.Namespace) -> int:
    import re
    import stat
    import subprocess

    ws = Path(args.workspace).resolve()
    if not (ws / "workspace.yaml").is_file():
        print(f"error: {ws} is not a workbench workspace (no workspace.yaml)", file=sys.stderr)
        return 2

    org, repo = args.org, args.repo
    if not (org and repo):
        try:
            url = subprocess.check_output(
                ["git", "-C", str(ws), "remote", "get-url", "origin"], text=True
            ).strip()
            m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?$", url)
            if m:
                org = org or m.group(1)
                repo = repo or m.group(2)
        except Exception:
            pass
    if not repo:
        print("error: could not infer the repo name from git remote; pass --repo (and --org)",
              file=sys.stderr)
        return 2
    org = org or "vivarium-collective"
    # New read-only workbenches publish under /<repo>/workbench (renamed from the
    # legacy /dashboard). The gh-pages subdir is the base-path's leaf, and the
    # workflow leaves a /dashboard → /workbench redirect so old links survive.
    # Existing repos keep /dashboard until they re-run add-dashboard — the
    # rename rolls out incrementally, one republish at a time.
    base_path = args.base_path or f"/{repo}/workbench"
    publish_dir = base_path.rstrip("/").split("/")[-1] or "workbench"
    interactive_url = args.interactive_url or f"https://github.com/{org}/{repo}"

    wf_path = ws / ".github" / "workflows" / "publish-dashboard.yml"
    sh_path = ws / "scripts" / "publish_dashboard.sh"
    for p in (wf_path, sh_path):
        if p.exists() and not args.force:
            print(f"error: {p.relative_to(ws)} already exists (use --force to overwrite)",
                  file=sys.stderr)
            return 2
    wf_path.parent.mkdir(parents=True, exist_ok=True)
    sh_path.parent.mkdir(parents=True, exist_ok=True)

    wf_path.write_text(
        _DASHBOARD_WORKFLOW.replace("{ORG}", org).replace("{REPO}", repo)
        .replace("{BASE_PATH}", base_path).replace("{PUBLISH_DIR}", publish_dir)
    )
    sh_path.write_text(
        _DASHBOARD_SCRIPT.replace("{BASE_PATH}", base_path)
        .replace("{PUBLISH_DIR}", publish_dir).replace("{INTERACTIVE_URL}", interactive_url)
    )
    sh_path.chmod(sh_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    print(f"✓ wrote {wf_path.relative_to(ws)}")
    print(f"✓ wrote {sh_path.relative_to(ws)}")
    print(f"  base-path={base_path}  interactive-url={interactive_url}")
    print("\nNext:")
    print("  1. commit + push to main (or: gh workflow run publish-dashboard.yml)")
    print("  2. enable GitHub Pages for the repo with source = gh-pages branch")
    print("  3. dashboard lands at https://%s.github.io%s/" % (org, base_path))
    return 0


def cmd_scaffold_workspace(args: argparse.Namespace) -> int:
    """Scaffold a new process-bigraph workspace (bootstrap — runs before any
    server exists). Wraps ``viva_superpowers.scaffold`` (Phase 2.1i rewire-
    first: the plugin still owns the scaffold payload; only the caller — the
    ``/viva-workspace`` skill — moves from ``python -m viva_superpowers.scaffold``
    to this ``vwb`` verb. The module move is 2.1k)."""
    try:
        from viva_superpowers import scaffold
    except ImportError as e:  # noqa: BLE001
        print(f"error: workspace scaffolding requires viva_superpowers: {e}", file=sys.stderr)
        return 1

    target = Path(args.target).resolve()
    try:
        if args.in_place:
            ws = scaffold.scaffold_workspace_in_place(
                workspace_root=target,
                workspace_name=args.name,
                template_source=args.source,
                branch=args.branch,
                package_path=args.package,
            )
        else:
            ws = scaffold.scaffold_workspace(target, args.name, source=args.source)
    except Exception as e:  # noqa: BLE001
        print(f"error: scaffold failed: {e}", file=sys.stderr)
        return 1
    print(str(ws))
    return 0


def cmd_catalog_add(args: argparse.Namespace) -> int:
    """Register a workspace in ``~/.pbg/workspaces.json`` so it appears in the
    dashboard's workspace switcher (idempotent). Wraps
    ``viva_superpowers.workspace_catalog.add`` — the same call ``vwb serve``
    already makes on boot (Phase 2.1i rewire-first)."""
    try:
        from viva_superpowers import workspace_catalog
    except ImportError as e:  # noqa: BLE001
        print(f"error: catalog registration requires viva_superpowers: {e}", file=sys.stderr)
        return 1

    try:
        entry = workspace_catalog.add(args.path, name=args.name, package=args.package)
    except ValueError as e:  # not a workspace (no workspace.yaml)
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"error: catalog add failed: {e}", file=sys.stderr)
        return 1
    print(json.dumps(entry, indent=2, default=str))
    return 0


def cmd_scaffold_investigation(args: argparse.Namespace) -> int:
    """Scaffold an investigation + one skeleton study per composite generator
    (the ``investigation-from-wrapper`` bootstrap). Wraps
    ``viva_superpowers.scaffold.scaffold_investigation_from_wrapper`` (Phase
    2.1k step 0 — completes the ``/viva-expert`` rewire; the plugin still owns
    the scaffold payload, only the caller moves)."""
    try:
        from viva_superpowers import scaffold
    except ImportError as e:  # noqa: BLE001
        print(f"error: investigation scaffolding requires viva_superpowers: {e}", file=sys.stderr)
        return 1

    ws = Path(args.workspace).resolve()
    if not (ws / "workspace.yaml").is_file():
        print(f"error: not a workspace (no workspace.yaml): {ws}", file=sys.stderr)
        return 2
    generators = [s.strip() for s in (args.studies or "").split(",") if s.strip()]
    if not generators:
        print("error: --studies requires at least one composite generator id", file=sys.stderr)
        return 2
    try:
        # viva-superpowers' scaffold_investigation_from_wrapper prints `wrote: <path>`
        # progress lines to stdout. This verb's stdout is a machine-readable JSON
        # contract (the /viva-expert skill parses it), so route the scaffold's own
        # chatter to stderr — keep it visible to humans, off the JSON channel.
        import contextlib
        with contextlib.redirect_stdout(sys.stderr):
            result = scaffold.scaffold_investigation_from_wrapper(
                ws, args.name, generators,
                investigation_slug=args.investigation_slug, force=args.force,
            )
    except Exception as e:  # noqa: BLE001
        print(f"error: investigation scaffold failed: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


# ---------------------------------------------------------------------------
# Dashboard server lifecycle (Phase 2.1j) — the detached-serve + status/stop/
# open/restart verbs that replace viva_superpowers.workbench (the plugin's
# server manager). All state lives at <ws>/.pbg/server/{server-info,server.pid}
# — exactly what foreground ``vwb serve`` already writes, and what every skill
# reads for the dashboard URL.
# ---------------------------------------------------------------------------

def _server_dir(ws: Path) -> Path:
    return ws / ".pbg" / "server"


def _read_server_info(ws: Path) -> dict | None:
    try:
        return json.loads((_server_dir(ws) / "server-info").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _server_pid(ws: Path) -> int | None:
    info = _read_server_info(ws)
    if info and info.get("pid"):
        try:
            return int(info["pid"])
        except (TypeError, ValueError):
            pass
    try:
        return int((_server_dir(ws) / "server.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, owned by another user
    except (OSError, ValueError):
        return False
    return True


def _http_ok(url: str, timeout: float = 1.0) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (loopback)
            return 200 <= getattr(r, "status", 200) < 500
    except Exception:  # noqa: BLE001
        return False


def _open_browser(url: str) -> None:
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def _clear_server_state(ws: Path) -> None:
    for name in ("server-info", "server.pid"):
        try:
            (_server_dir(ws) / name).unlink()
        except FileNotFoundError:
            pass


def _serve_detached(workspace: Path, args: argparse.Namespace) -> int:
    """Launch ``vwb serve`` in the background, wait until it reports ready, and
    print the URL. Adopts an already-running server for the workspace."""
    server_dir = _server_dir(workspace)
    server_dir.mkdir(parents=True, exist_ok=True)

    existing = _read_server_info(workspace)
    if existing and existing.get("url") and _pid_alive(_server_pid(workspace)) \
            and _http_ok(existing["url"]):
        print(f"Dashboard already running: {existing['url']}")
        if getattr(args, "open", False):
            _open_browser(_investigation_url(existing["url"], getattr(args, "investigation", None)))
        return 0

    _clear_server_state(workspace)
    port = args.port or _pick_free_port()
    log_file = server_dir / "server.log"

    cmd = [sys.executable, "-m", "vivarium_workbench.cli", "serve",
           "--workspace", str(workspace), "--port", str(port)]
    if getattr(args, "host", None):
        cmd += ["--host", args.host]
    if getattr(args, "base_path", ""):
        cmd += ["--base-path", args.base_path]

    with open(log_file, "wb") as log:
        proc = subprocess.Popen(  # noqa: S603
            cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, cwd=str(workspace),
        )

    url = None
    for _ in range(400):  # ~40s (a cold workspace render + uvicorn boot is slow)
        if not _pid_alive(proc.pid):
            print(f"error: dashboard exited during startup — see {log_file}", file=sys.stderr)
            return 1
        info = _read_server_info(workspace)
        if info and info.get("url") and _http_ok(info["url"]):
            url = info["url"]
            break
        time.sleep(0.1)

    if not url:
        info = _read_server_info(workspace)
        url = (info or {}).get("url") or f"http://127.0.0.1:{port}"
        print(f"warning: dashboard did not report ready in time — check {log_file}",
              file=sys.stderr)

    print(f"Dashboard: {url}  (detached, pid {proc.pid})")
    if getattr(args, "open", False):
        _open_browser(_investigation_url(url, getattr(args, "investigation", None)))
    return 0


def _investigation_url(base_url: str, investigation: str | None) -> str:
    if not investigation:
        return base_url
    return base_url.rstrip("/") + f"/investigations/{investigation}"


def cmd_server_status(args: argparse.Namespace) -> int:
    """Report the dashboard server's state for a workspace."""
    ws = Path(args.workspace).resolve()
    info = _read_server_info(ws)
    if not info:
        print(json.dumps({"state": "stopped"}))
        return 0
    pid = _server_pid(ws)
    url = info.get("url", "")
    alive = _pid_alive(pid)
    reachable = _http_ok(url) if url else False
    state = "running" if (alive and reachable) else "stale"
    print(json.dumps({"state": state, "url": url, "pid": pid, "reachable": reachable}, indent=2))
    return 0


def cmd_server_stop(args: argparse.Namespace) -> int:
    """Stop the dashboard server for a workspace + clear its state."""
    ws = Path(args.workspace).resolve()
    pid = _server_pid(ws)
    if not pid or not _pid_alive(pid):
        _clear_server_state(ws)
        print("not running")
        return 0
    import signal as _signal
    try:
        os.kill(int(pid), _signal.SIGTERM)
    except OSError as e:
        print(f"error: could not stop pid {pid}: {e}", file=sys.stderr)
        return 1
    for _ in range(50):  # ~5s
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    _clear_server_state(ws)
    print(f"stopped (pid {pid})")
    return 0


def cmd_server_open(args: argparse.Namespace) -> int:
    """Open the running dashboard in a browser (optionally at an investigation)."""
    ws = Path(args.workspace).resolve()
    info = _read_server_info(ws)
    if not info or not info.get("url"):
        print("error: dashboard not running (no server-info). Run `vwb serve --detach` first.",
              file=sys.stderr)
        return 1
    _open_browser(_investigation_url(info["url"], getattr(args, "investigation", None)))
    print(info["url"])
    return 0


def cmd_server_restart(args: argparse.Namespace) -> int:
    """Stop the dashboard server (if running) then start it detached."""
    cmd_server_stop(args)
    args.detach = True
    return cmd_serve(args)


def _shrink_loom_png(raw: bytes, max_width: int, colors: int) -> bytes:
    """Downscale + palette-quantize a captured loom PNG so the saved image (and the
    self-contained report that inlines it as base64) stays small. Loom diagrams are
    line art with few colours, so a max-width cap + palette quantise cuts the PNG
    ~10x with no visible loss. Best-effort: returns the original bytes if Pillow is
    unavailable or anything fails. ``max_width`` <= 0 disables the downscale;
    ``colors`` <= 0 keeps full colour."""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        if max_width and im.width > max_width:
            h = round(im.height * max_width / im.width)
            im = im.resize((max_width, h), Image.LANCZOS)
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        if colors and colors > 0:
            im = im.quantize(colors=colors, method=Image.FASTOCTREE)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        return buf.getvalue() or raw
    except Exception:  # noqa: BLE001 — never let image-shrinking break the bake
        return raw


def cmd_render_loom(args: argparse.Namespace) -> int:
    """Render each study's baseline composite bigraph-loom to a PNG.

    Drives the running dashboard's loom (its ``__loomExportPng`` export hook) with
    a headless browser and saves ``studies/<slug>/viz/model-loom.png`` — the
    self-contained investigation report then shows it in the Model section offline
    (no live server), rasterised so a heavy composite's loom stays ~0.3 MB instead
    of a multi-MB vector SVG.
    """
    import base64
    from urllib.parse import quote

    from vivarium_workbench.lib.workspace_paths import WorkspacePaths

    ws = Path(args.workspace).resolve()
    info = _read_server_info(ws)
    url = (info or {}).get("url", "").rstrip("/")
    if not url or not _http_ok(url):
        print("render-loom needs a running dashboard. Start one first:\n"
              "  vivarium-workbench serve --workspace . --detach", file=sys.stderr)
        return 1

    wp = WorkspacePaths.load(ws)
    jobs: list[tuple[str, str]] = []
    for sd in sorted(wp.studies.iterdir()):
        sf = sd / "study.yaml"
        if not sf.is_file():
            continue
        if args.study and sd.name != args.study:
            continue
        spec = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
        # Read the composite from either schema shape (v4 conditions.baseline or
        # the legacy top-level baseline list) — v4 studies otherwise bake nothing.
        from vivarium_workbench.lib.investigation_report import _baseline_composite_id
        comp = _baseline_composite_id(spec)
        if comp:
            jobs.append((sd.name, comp))
    if not jobs:
        print("no studies with a baseline composite found")
        return 0

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("render-loom needs Playwright:\n"
              "  pip install 'vivarium-workbench[loom-render]'\n"
              "  python -m playwright install chromium", file=sys.stderr)
        return 1

    ok = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(
            viewport={"width": int(args.width), "height": int(args.height)},
            device_scale_factor=float(args.device_scale))
        for slug, comp in jobs:
            out = wp.studies / slug / "viz" / "model-loom.png"
            loom_url = (f"{url}/bigraph-loom/?id={quote(comp)}"
                        "&tabs=explore,document&nopersist=1")
            # --fresh-layout: ignore any committed default view / saved positions
            # and lay the graph out from scratch with the current layout engine
            # (use after a renderer/layout change so figures aren't pinned to
            # stale saved positions).
            if getattr(args, "fresh_layout", False):
                loom_url += "&fresh=1"
            # --layout: force a specific layout engine. 'hierarchy' (compact 2-D
            # packing) reads best for a multi-process composite in print — a
            # roughly-square grid instead of one long horizontal strip.
            if getattr(args, "layout", None):
                loom_url += f"&layout={quote(args.layout)}"
            # --hide-address: drop the "local:Foo" registry address from each
            # process card (useful interactively, clutter in a book figure).
            if getattr(args, "hide_address", False):
                loom_url += "&address=off"
            # --compact: print preset for BUSY multi-process composites — narrow
            # cards, drop config/symbols/address so name+equation lead, and pack
            # the grid tight so the figure fits a page legibly.
            if getattr(args, "compact", False):
                loom_url += "&compact=1"
            try:
                page.goto(loom_url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_selector(".react-flow__node", timeout=40_000)
                page.wait_for_timeout(int(args.settle_ms))
                data = page.evaluate(
                    "async () => { const f = window.__loomExportPng;"
                    " return f ? await f() : null; }")
                if not data or not data.startswith("data:image/png"):
                    raise RuntimeError("__loomExportPng returned null")
                out.parent.mkdir(parents=True, exist_ok=True)
                raw = base64.b64decode(data.split(",", 1)[1])
                out.write_bytes(_shrink_loom_png(raw, int(args.max_width), int(args.colors)))
                print(f"  OK   {slug}  ({out.stat().st_size // 1024} KB)")
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL {slug}: {exc}", file=sys.stderr)
        browser.close()
    print(f"{ok}/{len(jobs)} loom images rendered → studies/<slug>/viz/model-loom.png")
    return 0 if ok == len(jobs) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vivarium-workbench")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="Serve the dashboard for a workspace")
    p_serve.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_serve.add_argument("--port", type=int, default=0, help="Port (default: pick a free port)")
    p_serve.add_argument(
        "--host", default="127.0.0.1",
        help="Bind host (default 127.0.0.1; pass 0.0.0.0 to expose outside this machine, e.g. when running in a container)",
    )
    p_serve.add_argument(
        "--base-path", default="",
        help="Serve under a URL path prefix (e.g. /workbench) for hosting behind a "
             "shared reverse proxy / ALB. Default empty = serve at root.",
    )
    p_serve.add_argument(
        "--trust-proxy", action="store_true",
        help="Trust X-Forwarded-Host for the CSRF same-origin check "
             "(sets VIVARIUM_WORKBENCH_TRUST_PROXY=1). Only enable behind a "
             "reverse proxy you control (e.g. an ALB/SSM tunnel) — do NOT "
             "enable for direct/loopback serving.",
    )
    p_serve.add_argument(
        "--allowed-origin", action="append", metavar="ORIGIN",
        help="Declare a browser-facing Origin (scheme + host[:port], e.g. "
             "http://localhost:8080) that is always allowed for POST/DELETE, "
             "even when the proxy rewrites Host and omits X-Forwarded-Host "
             "(sets VIVARIUM_WORKBENCH_ALLOWED_ORIGINS). Repeatable. Use behind "
             "a proxy you control — an ALB terminating a /workbench subpath.",
    )
    p_serve.add_argument(
        "--detach", action="store_true",
        help="Launch the dashboard in the background (writes .pbg/server/server-info, "
             "prints the URL, and returns) instead of running in the foreground.",
    )
    p_serve.add_argument("--open", action="store_true",
                         help="Open the dashboard in a browser after it is ready (with --detach).")
    p_serve.add_argument("--investigation", default=None, metavar="SLUG",
                         help="With --open, open the dashboard at this investigation.")
    p_serve.set_defaults(func=cmd_serve)

    p_doctor = sub.add_parser(
        "doctor", help="Check framework-dependency health (stale process-bigraph / viva-superpowers)")
    p_doctor.set_defaults(func=cmd_doctor)

    p_warm = sub.add_parser(
        "warm-catalog",
        help="Pre-build the Registry + Composites on-disk catalog cache for a "
             "workspace (run at image-build time so a cold pod never pays the "
             "multi-minute process-package import walk)",
    )
    p_warm.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_warm.set_defaults(func=cmd_warm_catalog)

    p_smoke = sub.add_parser(
        "smoke",
        help="<1-min local sanity check: server + env worker + a tiny run "
             "(hermetic scaffold by default; --workspace = non-mutating subset)")
    p_smoke.add_argument("--workspace", default=None,
                         help="Check a REAL workspace (server + env-worker only; "
                              "no run is written). Default: hermetic temp workspace.")
    p_smoke.set_defaults(func=cmd_smoke)

    p_mig = sub.add_parser(
        "migrate-investigations",
        help="One-shot migration: investigations/ → studies/ (v2→v3 spec rewrite)",
    )
    p_mig.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_mig.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing anything",
    )
    p_mig.set_defaults(func=cmd_migrate_investigations)

    p_migs = sub.add_parser(
        "migrate-studies",
        help="Move nested investigations/<inv>/studies/* into the top-level studies/ registry and rewrite investigations to members: references",
    )
    p_migs.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_migs.add_argument("--dry-run", action="store_true", help="Report what would move without touching disk")
    p_migs.set_defaults(func=cmd_migrate_studies)

    p_miga = sub.add_parser(
        "migrate-artifacts",
        help="Re-key the artifact store onto the current content-address "
             "formula (process-bigraph 1.7.0 whole-float narrowing). "
             "Idempotent — safe to re-run.",
    )
    p_miga.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_miga.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would move, and what would be orphaned, without touching the store",
    )
    p_miga.add_argument("--json", action="store_true", help="Emit the report as JSON")
    p_miga.set_defaults(func=cmd_migrate_artifacts)

    p_run = sub.add_parser(
        "run-composite",
        help="Execute one composite run from a run-request file (internal; "
             "spawned detached by the dashboard)",
    )
    p_run.add_argument("--request", required=True,
                       help="Path to the run-request JSON file")
    p_run.set_defaults(func=cmd_run_composite_worker)

    p_prep = sub.add_parser(
        "prepare-investigation",
        help="Run an investigation's baselines + comparison variants and render "
             "its comparatives as one coordinated generation (requires a running "
             "dashboard for the workspace)",
    )
    p_prep.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_prep.add_argument("--investigation", default=None,
                        help="Investigation slug (default: the only one present)")
    p_prep.add_argument("--study", default=None,
                        help="Prepare only this study (reuses the current generation)")
    p_prep.add_argument("--steps", type=int, default=None,
                        help="Override sim length per run (default: study params)")
    p_prep.add_argument("--render-only", action="store_true",
                        help="Skip sims; re-render comparatives from existing runs.db")
    p_prep.add_argument("--param-set", default=None,
                        help="Optional params file hashed into the generation's param_set_hash")
    p_prep.add_argument("--dashboard-url", default=None,
                        help="Override dashboard URL (default: auto-detect)")
    p_prep.set_defaults(func=cmd_prepare_investigation)

    p_remote = sub.add_parser(
        "run-remote",
        help="Export a composite and run it on sms-api (requires pushed git tree)",
    )
    p_remote.add_argument(
        "--workspace", default=".", help="Path to workspace root (default: cwd)"
    )
    p_remote.add_argument(
        "composite",
        help="Composite id (e.g. pbg_my_ws.composites.my_composite)",
    )
    p_remote.add_argument(
        "--sms-api-url", default=None,
        help="Override the sms-api base URL (default: from workspace config or http://localhost:8080)",
    )
    p_remote.add_argument(
        "--poll-interval", type=float, default=10.0,
        help="Seconds between status polls (default: 10)",
    )
    p_remote.add_argument(
        "--dest", default=None,
        help="Directory for the landed results.zip (default: <workspace>/.pbg/remote-results/)",
    )
    p_remote.set_defaults(func=cmd_run_remote)

    p_sync = sub.add_parser(
        "sync",
        help="Materialize an exact repo@commit workspace locally (any commit, "
             "a published workbench URL, or a manifest file)",
    )
    p_sync.add_argument(
        "manifest",
        metavar="SOURCE",
        help="one of: <repo>@<ref> (e.g. github.com/org/repo@1a2b3c4 or "
             "org/repo@main) to reproduce any commit; a published workbench URL; "
             "or a manifest JSON path/URL",
    )
    p_sync.add_argument("--dest", default=None, help="destination dir (default: ./<workspace>)")
    p_sync.add_argument("--run-post-sync", action="store_true",
                        help="run manifest-declared cache-rebuild commands (executes remote-authored commands)")
    p_sync.set_defaults(func=cmd_sync)

    # ------------------------------------------------------------------
    # User-facing: run study|investigation|composite, rerun, runs, status, logs
    # ------------------------------------------------------------------
    def _add_common(p):
        p.add_argument("--workspace", default=".")
        p.add_argument("--json", action="store_true")

    p_run_user = sub.add_parser("run", help="Run a study, investigation, or composite")
    run_sub = p_run_user.add_subparsers(dest="run_what", required=True)

    rs = run_sub.add_parser("study", help="Run a study's baseline or a variant")
    rs.add_argument("slug")
    rs.add_argument("--variant", default=None)
    rs.add_argument("--steps", type=int, default=None)
    rs.add_argument("--seed", type=int, default=None)
    rs.add_argument("--param", action="append", help="key=value (repeatable)")
    rs.add_argument("--dry-run", action="store_true")
    rs.add_argument("--detach", action="store_true")
    rs.add_argument("--server", default=None)
    _add_common(rs)
    rs.set_defaults(func=cmd_run_study)

    ri = run_sub.add_parser("investigation", help="Run all studies in an investigation")
    ri.add_argument("slug")
    ri.add_argument("--studies", default=None, help="comma-separated subset")
    ri.add_argument("--steps", type=int, default=None,
                    help="force every study to this many ticks (overrides per-study lengths)")
    ri.add_argument("--server", default=None)
    _add_common(ri)
    ri.set_defaults(func=cmd_run_investigation)

    rc = run_sub.add_parser("composite", help="Run a catalog composite for N steps")
    rc.add_argument("spec_id")
    rc.add_argument("--steps", type=int, default=5)
    rc.add_argument("--emit", default=None, help="comma-separated store paths")
    rc.add_argument("--dry-run", action="store_true")
    rc.add_argument("--detach", action="store_true")
    _add_common(rc)
    rc.set_defaults(func=cmd_run_composite)

    rp = run_sub.add_parser("process", help="Run one registry process/step once (single update)")
    rp.add_argument("address", help="registry address, e.g. pkg.processes.Foo or local:Foo")
    rp.add_argument("--config", action="append", help="config key=value (repeatable)")
    _add_common(rp)
    rp.set_defaults(func=cmd_run_process)

    pr = sub.add_parser("rerun", help="Re-run a recorded run (replays its composite + recorded params/steps)")
    pr.add_argument("run_id")
    pr.add_argument("--steps", type=int, default=None)
    pr.add_argument("--detach", action="store_true")
    _add_common(pr)
    pr.set_defaults(func=cmd_rerun)

    pl = sub.add_parser("runs", help="List a study's recorded runs")
    pl.add_argument("slug")
    _add_common(pl)
    pl.set_defaults(func=cmd_runs)

    pst = sub.add_parser("status", help="Show one run's state + progress")
    pst.add_argument("run_id")
    _add_common(pst)
    pst.set_defaults(func=cmd_status)

    plog = sub.add_parser("logs", help="Print a run's log")
    plog.add_argument("run_id")
    plog.add_argument("--follow", action="store_true")
    _add_common(plog)
    plog.set_defaults(func=cmd_logs)

    p_genrdm = sub.add_parser(
        "gen-readme",
        help="Regenerate the workspace README's composite + investigation tables (between "
             "<!-- BEGIN/END --> markers) from the workspace itself",
    )
    p_genrdm.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_genrdm.add_argument("--check", action="store_true",
                          help="exit 1 if the README is stale instead of rewriting it (CI)")
    p_genrdm.add_argument("--readme", default=None, metavar="PATH",
                          help="README path (default: <workspace>/README.md)")
    p_genrdm.set_defaults(func=cmd_gen_readme)

    p_audit = sub.add_parser(
        "audit",
        help="Run the L0-L5 reproducibility audit over the workspace's studies + investigations",
    )
    p_audit.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_audit.add_argument("--json", action="store_true", help="emit the raw audit report as JSON")
    p_audit.add_argument("--html", default=None, metavar="PATH",
                         help="write a self-contained HTML audit report to PATH")
    p_audit.set_defaults(func=cmd_audit)

    p_dash = sub.add_parser(
        "add-dashboard",
        help="Scaffold a robust read-only-dashboard publish workflow into a workspace",
    )
    p_dash.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_dash.add_argument("--org", default=None, help="GitHub org (default: inferred from git remote)")
    p_dash.add_argument("--repo", default=None, help="GitHub repo name (default: inferred from git remote)")
    p_dash.add_argument("--base-path", default=None,
                        help="Pages base path (default: /<repo>/dashboard)")
    p_dash.add_argument("--interactive-url", default=None,
                        help="Link back to the source repo (default: https://github.com/<org>/<repo>)")
    p_dash.add_argument("--force", action="store_true", help="Overwrite existing files")
    p_dash.set_defaults(func=cmd_add_dashboard)

    p_scaffold = sub.add_parser(
        "scaffold-workspace",
        help="Scaffold a new process-bigraph workspace (bootstrap; runs before any server)",
    )
    p_scaffold.add_argument("--name", required=True, help="workspace name")
    p_scaffold.add_argument("--target", default=".", help="target directory (default: cwd)")
    p_scaffold.add_argument("--source", default=None,
                            help="template source (path or URL; default: viva-template)")
    p_scaffold.add_argument("--in-place", action="store_true",
                            help="promote an existing git checkout into a workspace branch")
    p_scaffold.add_argument("--branch", default=None, help="workspace branch name (--in-place)")
    p_scaffold.add_argument("--package", default=None,
                            help="workspace Python package path (--in-place)")
    p_scaffold.set_defaults(func=cmd_scaffold_workspace)

    p_catadd = sub.add_parser(
        "catalog-add",
        help="Register a workspace in ~/.pbg/workspaces.json (dashboard switcher; idempotent)",
    )
    p_catadd.add_argument("--path", default=".", help="workspace root (default: cwd)")
    p_catadd.add_argument("--name", default=None, help="display name (default: from workspace.yaml)")
    p_catadd.add_argument("--package", default=None, help="workspace package path")
    p_catadd.set_defaults(func=cmd_catalog_add)

    p_scaffinv = sub.add_parser(
        "scaffold-investigation",
        help="Scaffold an investigation + one skeleton study per composite generator",
    )
    p_scaffinv.add_argument("--name", required=True, help="Investigation name (derives slug + title)")
    p_scaffinv.add_argument("--studies", required=True,
                            help="Comma-separated composite generator ids "
                                 "(e.g. 'pkg.composites.a.a_baseline,pkg.composites.b.b_baseline')")
    p_scaffinv.add_argument("--workspace", default=".", help="Workspace root (default: cwd)")
    p_scaffinv.add_argument("--investigation-slug", dest="investigation_slug", default=None,
                            help="Directory slug (default: kebab-cased --name)")
    p_scaffinv.add_argument("--force", action="store_true", help="Overwrite existing files")
    p_scaffinv.set_defaults(func=cmd_scaffold_investigation)

    p_sstat = sub.add_parser("server-status", help="Report the dashboard server's state for a workspace")
    p_sstat.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_sstat.set_defaults(func=cmd_server_status)

    p_sstop = sub.add_parser("server-stop", help="Stop the dashboard server + clear its state")
    p_sstop.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_sstop.set_defaults(func=cmd_server_stop)

    p_sopen = sub.add_parser("server-open", help="Open the running dashboard in a browser")
    p_sopen.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_sopen.add_argument("--investigation", default=None, metavar="SLUG",
                         help="Open the dashboard at this investigation")
    p_sopen.set_defaults(func=cmd_server_open)

    p_srestart = sub.add_parser("server-restart", help="Stop then start the dashboard server (detached)")
    p_srestart.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_srestart.add_argument("--port", type=int, default=0, help="Port (default: pick a free port)")
    p_srestart.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    p_srestart.add_argument("--base-path", default="", help="URL path prefix (default: root)")
    p_srestart.set_defaults(func=cmd_server_restart)

    p_render_loom = sub.add_parser(
        "render-loom",
        help="Render each study's composite bigraph-loom to studies/<slug>/viz/"
             "model-loom.png for the report Model section (needs a running server)")
    p_render_loom.add_argument("--workspace", default=".", help="Workspace root (default: cwd)")
    p_render_loom.add_argument("--study", default=None, help="Only this study slug")
    p_render_loom.add_argument("--settle-ms", type=int, default=4000,
                               help="Wait after layout before capture (default 4000)")
    p_render_loom.add_argument("--device-scale", type=float, default=2.0,
                               help="Device scale factor for the capture (default 2.0; "
                                    "use 1.0 for a ~4x smaller PNG, 1.5 for ~2x smaller)")
    p_render_loom.add_argument("--width", type=int, default=1600,
                               help="Capture viewport width in CSS px (default 1600)")
    p_render_loom.add_argument("--height", type=int, default=1000,
                               help="Capture viewport height in CSS px (default 1000)")
    p_render_loom.add_argument("--max-width", type=int, default=1600,
                               help="Downscale the saved PNG to at most this width in px "
                                    "(default 1600; 0 = keep the loom's full resolution). "
                                    "Keeps the self-contained report small.")
    p_render_loom.add_argument("--colors", type=int, default=128,
                               help="Palette-quantize the saved PNG to this many colours "
                                    "(default 128; 0 = full colour). Loom diagrams are line "
                                    "art, so this cuts PNG size ~10x with no visible loss.")
    p_render_loom.add_argument("--fresh-layout", action="store_true",
                               help="Ignore any committed default view / saved positions and "
                                    "lay the graph out from scratch with the current layout "
                                    "engine. Use after a renderer/layout change so figures "
                                    "reflect the new layout instead of stale saved positions.")
    p_render_loom.add_argument("--layout", default=None,
                               choices=["hierarchy", "flow-down", "tree-grid", "flow-right"],
                               help="Force a layout engine (with --fresh-layout). 'hierarchy' "
                                    "= compact 2-D packing (a roughly-square grid, best for a "
                                    "multi-process composite in print); 'flow-down' = the tree; "
                                    "'tree-grid' = tree with processes in a grid; 'flow-right' "
                                    "= left→right DAG.")
    p_render_loom.add_argument("--hide-address", action="store_true",
                               help="Hide the 'local:Foo' registry address on each process card "
                                    "(clutter in a print figure).")
    p_render_loom.add_argument("--compact", action="store_true",
                               help="Print preset for busy multi-process composites: narrow "
                                    "cards, drop config/symbols/address so name + governing "
                                    "equation lead, and pack the grid tight so the whole figure "
                                    "fits a book page legibly. Best with --layout hierarchy.")
    p_render_loom.set_defaults(func=cmd_render_loom)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
