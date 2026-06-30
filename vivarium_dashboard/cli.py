"""vivarium-dashboard CLI - serve a workspace via the dashboard."""
from __future__ import annotations
import argparse
import json
import os
import socket
import sys
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

    # Make the workspace's own package importable for the render step
    # (e.g. pbg_chromosome_rep1.core.build_core), and register the workspace
    # root for lib helpers.
    ws_str = str(workspace)
    if ws_str not in sys.path:
        sys.path.insert(0, ws_str)
    from vivarium_dashboard.lib._root import set_workspace_root
    set_workspace_root(workspace)

    # Render the dashboard HTML once before serving.
    try:
        from vivarium_dashboard.lib.report import render_dashboard
        render_dashboard(workspace, write_all=True)
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
            from pbg_superpowers import workspace_catalog
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
        from pbg_superpowers import workspace_catalog
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
    from vivarium_dashboard.lib.startup import serve_fastapi
    return serve_fastapi(workspace=workspace, port=port, host=host)


def migrate_investigations_to_studies(ws_root: Path, dry_run: bool = False) -> dict:
    """One-shot: walk investigations/, rename → studies/, migrate spec v2→v3.

    Returns {migrated|would_migrate: N, errors: [{name, error}], warnings: [...]}.
    Idempotent: if investigations/ does not exist, returns migrated=0 immediately.
    """
    from vivarium_dashboard.lib.spec_migration import migrate_v2_to_v3

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


def cmd_run_composite(args: argparse.Namespace) -> int:
    """CLI handler for the run-composite subcommand — runs one detached composite."""
    from vivarium_dashboard.lib.run_runner import execute
    return execute(Path(args.request))


def _load_manifest(source: str) -> dict:
    """Load a manifest from a file path, file://, or http(s):// (a JSON manifest
    or a dashboard base URL whose /api/source/manifest is fetched)."""
    import json
    import urllib.request

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
    from vivarium_dashboard.lib.remote_run import run_remote
    from vivarium_dashboard.lib.sms_api_client import SmsApiClient, SmsApiError
    from vivarium_dashboard.lib.workspace_deps_views import _sms_api_base

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
    from vivarium_dashboard.lib.sync_workspace import sync_from_manifest

    manifest = _load_manifest(args.manifest)
    dest = Path(args.dest) if args.dest else Path.cwd() / (manifest.get("workspace") or "workspace")
    body, status = sync_from_manifest(manifest, dest, run_post_sync=args.run_post_sync)
    if status == 200:
        print(f"synced {manifest.get('repo')}@{manifest.get('commit', '')[:7]} -> {body['path']}")
        print(f"registered as workspace '{manifest.get('workspace')}'. Open it from the switcher.")
        return 0
    print(f"sync failed ({status}): {body.get('error', body)}")
    return 1


def cmd_prepare_investigation(args: argparse.Namespace) -> int:
    """CLI handler: prepare an investigation's coordinated generation."""
    from vivarium_dashboard.lib.prepare_investigation import prepare_investigation
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vivarium-dashboard")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="Serve the dashboard for a workspace")
    p_serve.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_serve.add_argument("--port", type=int, default=0, help="Port (default: pick a free port)")
    p_serve.add_argument(
        "--host", default="127.0.0.1",
        help="Bind host (default 127.0.0.1; pass 0.0.0.0 to expose outside this machine, e.g. when running in a container)",
    )
    p_serve.set_defaults(func=cmd_serve)

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

    p_run = sub.add_parser(
        "run-composite",
        help="Execute one composite run from a run-request file (internal; "
             "spawned detached by the dashboard)",
    )
    p_run.add_argument("--request", required=True,
                       help="Path to the run-request JSON file")
    p_run.set_defaults(func=cmd_run_composite)

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
        help="Materialize a remote dashboard's exact repo@commit workspace locally",
    )
    p_sync.add_argument("manifest", help="manifest JSON path/URL, or a dashboard base URL")
    p_sync.add_argument("--dest", default=None, help="destination dir (default: ./<workspace>)")
    p_sync.add_argument("--run-post-sync", action="store_true",
                        help="run manifest-declared cache-rebuild commands (executes remote-authored commands)")
    p_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
