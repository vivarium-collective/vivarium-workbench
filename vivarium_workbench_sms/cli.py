"""The SMS provider's subcommands (docs/backend-adapters.md §2.2, step P2 CLI): ``run-remote``.

The core owns ``argparse`` and adds these at the slot ``run-remote`` always had (right after
``prepare-investigation``), so ``vivarium-workbench --help`` is unchanged while providers are on; with providers off
the command does not exist. ``SmsProvider.cli()`` calls :func:`commands` on every call (never cached), and
:func:`commands` reads this module's globals at call time, so a test can patch :func:`cmd_run_remote` here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vivarium_workbench.lib.backends.provider import Command


def add_run_remote_arguments(p_remote: argparse.ArgumentParser) -> None:
    """The ``run-remote`` arguments (moved verbatim from ``vivarium_workbench/cli.py``)."""
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


def cmd_run_remote(args: argparse.Namespace) -> int:
    """CLI handler for the run-remote subcommand.

    Validates the workspace git tree is clean and pushed, exports the named
    composite to a .pbg document, submits it to sms-api, polls until
    completion, and lands results.zip in the workspace.
    """
    from vivarium_workbench.lib.remote_run import run_remote
    from vivarium_workbench_sms.sms_api_client import SmsApiClient, SmsApiError, sms_api_base

    workspace = Path(args.workspace).resolve()
    if not (workspace / "workspace.yaml").is_file():
        print(f"ERROR: not a workspace (no workspace.yaml): {workspace}", file=sys.stderr)
        return 2

    base_url = getattr(args, "sms_api_url", None) or sms_api_base()
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


def commands() -> "tuple[Command, ...]":
    """The SMS provider's subcommands, built fresh on every call."""
    return (
        Command(
            "run-remote",
            help="Export a composite and run it on sms-api (requires pushed git tree)",
            configure=add_run_remote_arguments,
            run=cmd_run_remote,
            uses_backend=True,
        ),
    )
