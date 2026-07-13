#!/usr/bin/env python3
"""Pre-build a v2ecoli simulator image on sms-api for the dashboard demo.

Pushes the current branch to origin, registers it as a simulator build on sms-api,
polls until the Docker image is ready, and saves the resulting ``simulator_id`` to
``demos/v2ecoli/.demo_state.json`` so the land step can reuse it without
re-pushing.

This script only writes to ``demos/v2ecoli/.demo_state.json`` — it does NOT
modify any existing v2ecoli files.

Usage:
    cd ~/vivarium-app/vivarium-dashboard
    python demos/v2ecoli/prep_remote_build.py

Pre-requisites:
    - sms-api SSM tunnel active (localhost:8080)
    - git origin remote configured
"""

from __future__ import annotations

import json
import os as _os
import sys
import time
from pathlib import Path

import v2ecoli as _v2ecoli

from vivarium_dashboard.lib.git_status import remote_push_and_sha, remote_repo_url
from vivarium_dashboard.lib.sms_api_client import SmsApiClient, SmsApiError
from vivarium_dashboard.lib.workspace_deps_views import _sms_api_base

WORKSPACE_ROOT = Path(_os.environ.get(
    "V2ECOLI_ROOT", str(Path(_v2ecoli.__file__).resolve().parent.parent)))
DEMO_DIR = WORKSPACE_ROOT / ".pbg" / "demo"
STATE_FILE = DEMO_DIR / ".demo_state.json"
POLL_INTERVAL = 10  # seconds between status polls
MAX_WAIT = 600  # max seconds to wait for build


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    print("Pre-building v2ecoli simulator on sms-api")
    print(f"Workspace: {WORKSPACE_ROOT}\n")

    state = load_state()

    # 1. Resolve git info
    print("[1/4] Resolving git branch and remote...")
    try:
        repo_url = remote_repo_url(WORKSPACE_ROOT)
        if not repo_url:
            print("ERROR: no origin remote configured")
            return 1
        sha = remote_push_and_sha(WORKSPACE_ROOT)
        print(f"  Remote: {repo_url}")
        print(f"  Commit: {sha}")
    except Exception as e:
        print(f"ERROR: git push failed: {e}")
        return 1

    # 2. Connect to sms-api
    print("\n[2/4] Connecting to sms-api...")
    client = SmsApiClient(_sms_api_base())
    try:
        versions = client.list_simulators()
        print(f"  sms-api reachable ({len(versions)} registered builds)")
    except SmsApiError as e:
        print(f"ERROR: sms-api unreachable — is the tunnel up?: {e}")
        return 1

    # 3. Resolve branch and register build
    print("\n[3/4] Registering build on sms-api...")
    try:
        branch = state.get("branch") or "main"
        latest = client.latest_simulator(repo_url, branch)
        commit = latest.get("git_commit_hash", "")
        if not commit:
            print("ERROR: could not resolve branch HEAD via sms-api")
            return 1
        print(f"  Branch: {branch} @ {commit[:12]}")

        # Check if we already have a build for this commit
        existing_id = state.get("simulator_id")
        if existing_id and state.get("commit") == commit:
            if state.get("status") in ("ready", "built", "complete"):
                print(f"  Reusing existing build #{existing_id} (same commit, already {state.get('status')})")
                return 0
            else:
                sim_id = existing_id
                print(f"  Resuming poll for build #{sim_id} (same commit, status was '{state.get('status')}')")
        else:
            reg = client.register_simulator(repo_url, branch, commit)
            sim_id = reg.get("database_id")
            if not sim_id:
                print("ERROR: sms-api returned no database_id")
                return 1
            print(f"  Registered as simulator #{sim_id}")
    except SmsApiError as e:
        print(f"ERROR: sms-api call failed: {e}")
        return 1

    # 4. Poll for build completion
    print(f"\n[4/4] Waiting for Docker image build (poll every {POLL_INTERVAL}s)...")
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        try:
            status = client.simulator_status(sim_id)
            s = status.get("status", "unknown")
            print(f"  [{elapsed:.0f}s] status: {s}")

            if s in ("ready", "built", "complete", "completed"):
                print(f"\nBuild #{sim_id} is {s} ✓")
                save_state({
                    "simulator_id": sim_id,
                    "repo": repo_url,
                    "branch": branch,
                    "commit": commit,
                    "status": s,
                    "built_at": time.time(),
                })
                return 0

            if s in ("failed", "error"):
                print(f"\nBuild #{sim_id} failed: {status.get('message', s)}")
                return 1

        except SmsApiError as e:
            print(f"  Poll error: {e}")

        if elapsed > MAX_WAIT:
            print(f"\nTimed out after {MAX_WAIT}s — build may still be in progress.")
            save_state({
                "simulator_id": sim_id,
                "repo": repo_url,
                "branch": branch,
                "commit": commit,
                "status": "building",
                "built_at": None,
            })
            print("State saved. Re-run this script to resume polling.")
            return 1

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
