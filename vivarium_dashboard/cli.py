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
    print(f"\nWorkspace dashboard: http://127.0.0.1:{port}")
    print("   (Ctrl-C to stop)\n")

    # Boot the HTTP server.
    from vivarium_dashboard.server import serve as serve_dashboard
    return serve_dashboard(workspace=workspace, port=port)


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
            spec = yaml.safe_load(spec_path.read_text()) or {}
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vivarium-dashboard")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="Serve the dashboard for a workspace")
    p_serve.add_argument("--workspace", default=".", help="Path to workspace root (default: cwd)")
    p_serve.add_argument("--port", type=int, default=0, help="Port (default: pick a free port)")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
