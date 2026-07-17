#!/usr/bin/env python3
"""remote_commit_run.py — register→poll→run an ARBITRARY repo@commit on sms-api.

Generalizes ``ensure_latest_main_build.sh`` (which only gates the pinned
v2ecoli@main build) into the full local-authoring → remote-compute loop:
register a build for any pushed commit, poll it to completion, submit a
parameterized simulation against it, poll the run, and (optionally) land the
result into a workspace study's runs.db — the same three sms-api calls the
dashboard's "Run on remote" card makes (see
``vivarium_workbench/lib/remote_run_views.py``), reused directly here instead
of re-implemented, so this script never drifts from what the UI actually does.

Prerequisites:
  - An sms-api SSM tunnel is up (e.g. ``sms-proxy.sh -s smscdk`` -> localhost:8080).
  - The commit you want built has been PUSHED to ``--repo-url`` — sms-api
    clones it itself, it does not accept a local diff.
  - Run from the vivarium-workbench repo venv (``uv run``), since this
    imports vivarium_workbench.lib directly rather than re-implementing the
    sms-api JSON parsing in shell.

Usage:
  # Register+build the live tip of a branch, then run 2 gens x 3 seeds:
  uv run demos/v2ecoli/scripts/remote_commit_run.py \\
      --repo-url https://github.com/vivarium-collective/v2ecoli --branch main \\
      --generations 2 --seeds 3

  # Run against a specific already-registered commit:
  uv run demos/v2ecoli/scripts/remote_commit_run.py --commit 70b5ec3 ...

  # Skip the build phase entirely (build already known-good):
  uv run demos/v2ecoli/scripts/remote_commit_run.py --simulator-id 69 ...

  # Land the result straight into a local workspace study's runs.db:
  uv run demos/v2ecoli/scripts/remote_commit_run.py ... \\
      --workspace $V2ECOLI_DIR --study showcase-2-baseline-figures

Exit 0 only on a fully landed/downloaded success.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError  # noqa: E402

_BUILD_OK = {"completed", "complete", "succeeded", "built", "ready"}
_BUILD_BAD = {"failed", "error", "cancelled"}
_RUN_OK = {"completed", "complete", "done", "succeeded"}
_RUN_BAD = {"failed", "error", "cancelled"}


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def resolve_tip(repo_url: str, branch: str) -> str:
    out = subprocess.run(
        ["git", "ls-remote", repo_url, branch],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout.strip()
    if not out:
        raise SystemExit(f"ERROR: could not resolve {repo_url}@{branch} (network? bad branch?)")
    return out.split()[0]


def poll(label: str, ok: set, bad: set, interval: float, max_wait: float, check) -> dict:
    start = time.time()
    while True:
        elapsed = time.time() - start
        status = check()
        raw = str(status.get("status", "")).lower()
        log(f"  [{elapsed:0.0f}s] {label} status={raw or '<unknown>'}")
        if raw in ok:
            return status
        if raw in bad:
            raise SystemExit(f"ERROR: {label} ended with status={raw}. Body: {status}")
        if elapsed > max_wait:
            raise SystemExit(
                f"TIMEOUT after {max_wait:0.0f}s waiting on {label}; re-run with --simulator-id "
                f"or --simulation-id to resume checking without re-submitting."
            )
        time.sleep(interval)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo-url", default="https://github.com/vivarium-collective/v2ecoli")
    p.add_argument("--branch", default="main")
    p.add_argument("--commit", default=None, help="Defaults to the live tip of --repo-url@--branch")
    p.add_argument("--simulator-id", type=int, default=None, help="Skip register+build; run against an existing build")
    p.add_argument("--sms-api-url", default=None, help="Default: $SMS_API_BASE or http://localhost:8080")
    p.add_argument("--generations", type=int, default=1)
    p.add_argument("--seeds", type=int, default=1)
    p.add_argument("--observables", default="", help="Comma-separated store paths; ignored if --study is given")
    p.add_argument("--run-parca", dest="run_parca", action="store_true", default=True)
    p.add_argument("--no-run-parca", dest="run_parca", action="store_false")
    p.add_argument("--experiment-id", default=None)
    p.add_argument("--description", default=None)
    p.add_argument("--poll-interval", type=float, default=30.0)
    p.add_argument("--max-build-wait", type=float, default=1800.0)
    p.add_argument("--max-run-wait", type=float, default=3600.0)
    p.add_argument("--workspace", default=None, help="Land into this workspace's study runs.db (needs --study)")
    p.add_argument("--study", default=None, help="Study slug to land into (needs --workspace)")
    p.add_argument("--dest", default=None, help="Download dir when NOT landing (default demos/v2ecoli/.remote-runs/)")
    args = p.parse_args()

    if bool(args.workspace) != bool(args.study):
        raise SystemExit("ERROR: --workspace and --study must be given together")

    import os
    base_url = args.sms_api_url or os.environ.get("SMS_API_BASE", "http://localhost:8080")
    client = SmsApiClient(base_url)
    log(f"sms-api base: {base_url}")

    # ---- Phase 1: register + poll the build (or reuse an existing one) ----
    commit = args.commit
    sim_id = args.simulator_id
    if sim_id is None:
        commit = commit or resolve_tip(args.repo_url, args.branch)
        log(f"Registering {args.repo_url}@{args.branch}@{commit[:12]}…")
        try:
            uploaded = client.register_simulator(args.repo_url, args.branch, commit)
        except SmsApiError as e:
            raise SystemExit(f"ERROR (sms-api): {e}")
        sim_id = uploaded.get("database_id")
        if not sim_id:
            raise SystemExit(f"ERROR: register_simulator returned no database_id. Response: {uploaded}")
        log(f"  registered simulator_id={sim_id}; polling build (every {args.poll_interval:0.0f}s, "
            f"max {args.max_build_wait:0.0f}s)…")
        poll("build", _BUILD_OK, _BUILD_BAD, args.poll_interval, args.max_build_wait,
             lambda: client.simulator_status(int(sim_id)))
        log(f"BUILT ✓ simulator_id={sim_id}")
    else:
        log(f"Skipping build phase — using existing simulator_id={sim_id}")

    # ---- Phase 2: observables + study context (optional) ----
    observables = [o.strip() for o in args.observables.split(",") if o.strip()]
    spec_id = None
    study_dir_path = None
    if args.study:
        ws_root = Path(args.workspace).resolve()
        if not (ws_root / "workspace.yaml").is_file():
            raise SystemExit(f"ERROR: not a workspace (no workspace.yaml): {ws_root}")
        from vivarium_workbench.lib import study_spec
        from vivarium_workbench.lib.investigations import load_spec
        spec_path = study_spec.study_spec_path(ws_root, args.study)
        if spec_path is None or not spec_path.is_file():
            raise SystemExit(f"ERROR: study {args.study!r} not found under {ws_root}")
        spec = load_spec(spec_path)
        observables = observables or study_spec.collect_study_observables(spec)
        baseline = spec.get("baseline") or []
        spec_id = (baseline[0].get("composite") if baseline else None) or args.study
        study_dir_path = study_spec.study_dir(ws_root, args.study)
        log(f"Study {args.study!r} resolved -> spec_id={spec_id}, {len(observables)} observables")

    # ---- Phase 3: submit + poll the run ----
    log(f"Submitting run: simulator_id={sim_id}, generations={args.generations}, seeds={args.seeds}, "
        f"run_parca={args.run_parca}, {len(observables)} observables")
    try:
        sim = client.run_simulation(
            simulator_id=int(sim_id),
            num_generations=args.generations,
            num_seeds=args.seeds,
            run_parca=args.run_parca,
            observables=observables,
            experiment_id=args.experiment_id,
            description=args.description,
        )
    except SmsApiError as e:
        raise SystemExit(f"ERROR (sms-api): {e}")
    simulation_id = sim.get("database_id")
    if not simulation_id:
        raise SystemExit(f"ERROR: run_simulation returned no database_id. Response: {sim}")
    log(f"  submitted simulation_id={simulation_id}; polling run (every {args.poll_interval:0.0f}s, "
        f"max {args.max_run_wait:0.0f}s)…")
    poll("run", _RUN_OK, _RUN_BAD, args.poll_interval, args.max_run_wait,
         lambda: client.simulation_status(int(simulation_id)))
    log(f"DONE ✓ simulation_id={simulation_id}")

    # ---- Phase 4: land locally, or just download ----
    if args.study:
        from vivarium_workbench.lib.remote_run_landing import land_remote_run
        assert study_dir_path is not None and spec_id is not None  # guaranteed by the args.study branch above
        with tempfile.TemporaryDirectory() as td:
            tar_path = client.download_data(int(simulation_id), Path(td))
            run_id = land_remote_run(
                study_dir_path,
                spec_id=spec_id,
                simulation_id=int(simulation_id),
                experiment_id=args.experiment_id or f"sim-{simulation_id}-{args.study}",
                commit=commit or "",
                tar_path=tar_path,
            )
        log(f"LANDED ✓ run_id={run_id} into {study_dir_path}/runs.db")
    else:
        dest = Path(args.dest) if args.dest else Path(__file__).resolve().parents[1] / ".remote-runs"
        out_path = client.download_data(int(simulation_id), dest)
        log(f"DOWNLOADED ✓ {out_path} (land it into a study with --workspace/--study, "
            f"or via the dashboard's Simulations DB)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())