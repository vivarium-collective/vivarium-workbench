"""The chat's command tools for the Claude Code provider: ``run_command`` and ``request_workspace_trust``.

Served by the MCP server in :mod:`lib.claude_mcp` and NOWHERE else: there is no HTTP route for them, so nothing
that can reach the workbench's port can run a command without the chat's approval step (see "Running commands" in docs/ai-chat.md). The gates, in the order they are checked:

1. the server was started with the explicit switch (``--enable-run-command``), because a hosted pod may run as root;
2. Agent mode, and a local (loopback) server: the Claude Code provider only runs there, and its turn preflight
   refuses anything else; the binding records it (``deps.local_only``) so this check cannot be skipped;
3. the command passes :func:`lib.run_command.build_plan` (allow-list, realpath containment, no secret locations);
4. the workspace is trusted, which only the user's explicit answer to its own approval card can grant;
5. the user approves THIS command on its card. What was approved is re-validated just before it runs, and it is
   recorded, before it runs, in the workspace's audit log and in a protected copy outside the workspace.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from vivarium_workbench.lib import run_command, user_state
from vivarium_workbench.lib.env_compat import get_env

if TYPE_CHECKING:
    from vivarium_workbench.lib.claude_mcp import Binding

SWITCH = "ENABLE_RUN_COMMAND"
TOOL_NAMES = ("run_command", "request_workspace_trust")
MODEL_CHARS = 10_000          # per stream, in what the model is shown (the process cap is larger: run_command.OUTPUT_CAP)
_TRUTHY = {"1", "true", "yes", "on"}

RUN_DOC = (
    "Run ONE read-only inspection command on the machine running the workbench, after the user approves it on a card "
    "that shows the exact command. There is no shell: give `argv` as the program name followed by one item per "
    "argument. Allowed programs: ls, pwd, wc, head, tail, file, which, cat, grep, find (name/type/depth tests only), "
    "and read-only git (status, log, diff, show, branch, ls-files, rev-parse). Anything else, any other option, and any "
    "path outside the workspace (or the extra folders you list in `extra_dirs`, each shown to the user) is refused, "
    "as is anything naming a secret location (~/.ssh, ~/.config, ~/.aws, .env, git credentials, keys). `cwd` is a folder "
    "inside the workspace (default: its root). Output is cut off at a size limit and a 30 second deadline applies. "
    "The workspace must be trusted first: if the result says it is not, call request_workspace_trust and wait for the "
    "user. Do not retry a command the user declined.")
TRUST_DOC = (
    "Ask the user whether to trust this workspace to let you run commands. They see a card naming the workspace and "
    "what trust allows, and only their approval grants it. Call this only after run_command says the workspace is "
    "not trusted; do not call it otherwise.")


def enabled() -> bool:
    return (get_env(SWITCH, "") or "").strip().lower() in _TRUTHY


def gate(deps: Any) -> str | None:
    """Why the command tools must not run for this chat, or ``None``."""
    if not enabled():
        return ("Running commands is switched off on this server. The user can enable it by starting the workbench with "
                "--enable-run-command; do not suggest working around it.")
    if deps.mode != "agent":
        return "Commands can only be run in Agent mode."
    if not getattr(deps, "local_only", False):
        return "Commands can only be run on a local (loopback) server."
    return None


# --- the cards ----------------------------------------------------------------------------------------------


def command_card(plan: run_command.Plan) -> dict[str, Any]:
    """The approval-card payload: the program and every argument that will actually be passed (including the ones the
    runner adds or resolves), the real working folder, and each extra folder: what is shown is what runs."""
    return {
        "operation_id": "run_command", "method": "RUN", "path": "on this machine",
        "summary": "Run a command in the workspace", "query": {}, "body": None,
        "effect": {
            "kind": "command",
            "summary": "Runs this program with exactly these arguments, in this folder. No shell is involved.",
            "command_line": [plan.exe, *plan.args], "requested": list(plan.argv), "cwd": str(plan.cwd),
            "extra_dirs": [str(d) for d in plan.extra_dirs], "limits": plan.limits,
            "remember": plan.rememberable,
        }}


def trust_card(ws_root: Any) -> dict[str, Any]:
    real = os.path.realpath(ws_root)
    return {
        "operation_id": "request_workspace_trust", "method": "TRUST", "path": real,
        "summary": "Trust this workspace to let the assistant run commands", "query": {}, "body": None,
        "effect": {
            "kind": "trust",
            "summary": ("Trusting a workspace means the assistant may ask to run commands in it. Text inside a workspace "
                        "(files, notes, run results, skills) can try to steer the assistant, so only trust a workspace "
                        "whose contents you trust. Each command still needs your approval on its own card."),
            "workspace": real,
            "stored": f"{user_state.config_dir()} (outside the workspace)",
        }}


# --- the handlers ---------------------------------------------------------------------------------------------


def _declined(d: Any) -> dict[str, Any]:
    if d.message:
        return {"error": d.message}
    why = d.reason.strip()
    return {"error": "The user declined this action" + (f" (their note: {why})" if why else "") +
                     ". It was NOT done. Do not retry the same call; acknowledge the decision and ask what they want instead."}


def _record(deps: Any, tool_use_id: str, phase: str, extra: dict[str, Any], *, must: bool) -> str | None:
    """Write one audit record to the workspace log and to the protected copy. With ``must`` a failure is returned
    (so the caller refuses to run an unrecorded command); without it the result record is best effort."""
    from vivarium_workbench.lib import ai_tools
    rec = {"session": ai_tools.session_tag(deps.session_key), "provider": deps.provider, "model": deps.model,
           "tool_call_id": tool_use_id, "approved": True, "phase": phase, "ts": datetime.now(timezone.utc).isoformat(), **extra}
    errors = []
    for write in (lambda: ai_tools.append_audit(deps.ws_root, rec), lambda: user_state.append_command_log(deps.ws_root, rec)):
        try:
            write()
        except OSError as e:
            errors.append(str(e))
    if errors and must:
        return "; ".join(errors)
    return None


def _for_model(result: dict[str, Any]) -> dict[str, Any]:
    out = dict(result)
    for k in ("stdout", "stderr"):
        v = out.get(k)
        if isinstance(v, str) and len(v) > MODEL_CHARS:
            out[k] = v[:MODEL_CHARS] + f"\n[... {len(v) - MODEL_CHARS} more characters not shown]"
            out["truncated"] = True
    return out


def _same(a: run_command.Plan, b: run_command.Plan) -> bool:
    return (a.exe, a.args, a.cwd, a.roots) == (b.exe, b.args, b.cwd, b.roots)


async def command_call(b: "Binding", tool_use_id: str, argv: Any, cwd: Any, extra_dirs: Any) -> dict[str, Any]:
    deps = b.deps
    if (why := gate(deps)) is not None:
        return {"error": why}
    try:
        plan = run_command.build_plan(deps.ws_root, argv, cwd, extra_dirs)
    except run_command.CommandRefused as e:
        return {"error": f"refused: {e}"}
    if not user_state.is_trusted(deps.ws_root):
        return {"error": "This workspace is not trusted for running commands. Call request_workspace_trust, wait for the "
                         "user's answer, then try this command again."}
    if not tool_use_id:
        return {"error": "this command cannot be tied to an approval card (the call carried no id); not running it"}
    args = {"argv": list(plan.argv), "cwd": cwd, "extra_dirs": extra_dirs}
    d = await b.coord.ask(tool_use_id, args, command_card(plan), b.approval_ttl)
    if not d.approved:
        return _declined(d)
    # What the user approved is what runs: validate again now, and refuse if anything it touches has moved since.
    try:
        again = run_command.build_plan(deps.ws_root, argv, cwd, extra_dirs)
    except run_command.CommandRefused as e:
        return {"error": f"not run: the command is no longer allowed ({e})"}
    if not _same(plan, again) or not user_state.is_trusted(deps.ws_root):
        return {"error": "not run: the folders involved (or the workspace's trust) changed after you approved; ask again"}
    meta = {"operation_id": "run_command", "method": "RUN", "argv": list(again.argv), "command_line": [again.exe, *again.args],
            "cwd": str(again.cwd), "extra_dirs": [str(x) for x in again.extra_dirs]}
    if (bad := _record(deps, tool_use_id, "intent", meta, must=True)) is not None:
        return {"error": f"audit log unavailable ({bad}); refusing to run an unrecorded command"}
    result = await run_command.run(again)
    _record(deps, tool_use_id, "result", {"operation_id": "run_command", "exit_code": result.get("exit_code"),
                                          "timed_out": result.get("timed_out"), "truncated": result.get("truncated"),
                                          "outcome": "error" if "error" in result else "ran"}, must=False)
    return _for_model(result)


async def trust_call(b: "Binding", tool_use_id: str) -> dict[str, Any]:
    deps = b.deps
    if (why := gate(deps)) is not None:
        return {"error": why}
    if user_state.is_trusted(deps.ws_root):
        return {"trusted": True, "note": "this workspace is already trusted"}
    if not tool_use_id:
        return {"error": "this request cannot be tied to an approval card (the call carried no id); not granting trust"}
    d = await b.coord.ask(tool_use_id, {"workspace": os.path.realpath(deps.ws_root)}, trust_card(deps.ws_root), b.approval_ttl)
    if not d.approved:
        return {"trusted": False, **_declined(d)}
    meta = {"operation_id": "request_workspace_trust", "method": "TRUST", "path": os.path.realpath(deps.ws_root)}
    if (bad := _record(deps, tool_use_id, "intent", meta, must=True)) is not None:
        return {"error": f"audit log unavailable ({bad}); not granting trust"}
    try:
        user_state.grant_trust(deps.ws_root)
    except OSError as e:
        return {"error": f"could not record the trust ({e})"}
    _record(deps, tool_use_id, "result", {"operation_id": "request_workspace_trust", "outcome": "granted"}, must=False)
    return {"trusted": True}
