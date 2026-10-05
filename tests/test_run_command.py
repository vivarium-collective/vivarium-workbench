"""The chat's command runner (``lib/run_command.py``): what is refused, what runs, and what cannot get out.

Nothing is mocked at the layer in question: the validator is exercised on hostile inputs, and the runner starts REAL
processes (a hanging ``cat`` on a fifo, a flooding ``cat``, a shell that backgrounds a child, a git repository whose
own config tries to run a program) and checks what actually happened on disk and in the process table.
"""
import asyncio
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from vivarium_workbench.lib import run_command as rc
from vivarium_workbench.lib.run_command import CommandRefused, Plan, build_plan

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("alpha\nbeta\ngamma\n")
    (root / "sub" / "b.py").write_text("print('b')\n")
    (root / ".env").write_text("TOKEN=hunter2\n")
    (root / ".pbg").mkdir()
    (root / ".pbg" / "ai-actions.jsonl").write_text("{}\n")
    return root


def run(plan: Plan, **kw):
    return asyncio.run(rc.run(plan, **kw))


def plan(ws: Path, *argv: str, **kw) -> Plan:
    return build_plan(ws, list(argv), **kw)


# --- what is allowed --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["ls"], ["ls", "-la"], ["ls", "-la", "sub"], ["pwd"], ["wc", "-l", "a.txt"], ["head", "-n", "2", "a.txt"],
    ["tail", "-n", "1", "a.txt"], ["which", "ls"], ["cat", "a.txt"], ["cat", "-n", "a.txt"],
    ["grep", "-n", "beta", "a.txt"], ["grep", "-e", "beta", "a.txt"], ["grep", "-rn", ".env", "sub"],
    ["find", ".", "-name", "*.py", "-maxdepth", "2"], ["find", "sub", "-type", "f"],
])
def test_inspectors_are_allowed(ws, argv):
    assert build_plan(ws, argv).exe.startswith("/")


def test_a_grep_pattern_is_not_a_path(ws):
    """The pattern '.env' looks like a secret path but is only text to search for."""
    p = plan(ws, "grep", "-n", ".env", "a.txt")
    assert ".env" in p.args


def test_only_the_fixed_inspectors_are_rememberable(ws):
    assert plan(ws, "ls").rememberable and plan(ws, "pwd").rememberable
    assert not plan(ws, "cat", "a.txt").rememberable
    assert not plan(ws, "grep", "x", "a.txt").rememberable
    assert not plan(ws, "find", ".").rememberable


# --- what is refused --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["python", "-c", "print(1)"], ["sh", "-c", "id"], ["bash"], ["rm", "-rf", "x"], ["curl", "http://x"], ["env"],
    ["make"], ["uv", "run", "x"], ["pip", "install", "x"], ["ssh", "host"], ["sudo", "ls"],
    ["/bin/ls"], ["../ls"], ["ls;rm"], ["ls && id"], ["$(id)"], [""], [],
])
def test_anything_off_the_allow_list_is_refused(ws, argv):
    with pytest.raises(CommandRefused):
        build_plan(ws, argv)


@pytest.mark.parametrize("argv", [
    ["find", ".", "-exec", "id", ";"], ["find", ".", "-delete"], ["find", ".", "-fprint", "x"],
    ["find", ".", "-ok", "id", ";"], ["find", ".", "-name"],
    ["git", "-c", "core.pager=id", "status"], ["git", "-C", "/", "status"], ["git", "--upload-pack=id", "status"],
    ["git", "diff", "--ext-diff"], ["git", "diff", "--output=x"], ["git", "log", "--format=%H"], ["git", "config", "-l"],
    ["git", "push"], ["git", "commit", "-m", "x"], ["git", "checkout", "x"], ["git", "branch", "-D", "x"],
    ["git", "diff", "/etc/passwd", "/etc/hosts"], ["git", "show", "--output=x"], ["git"],
    ["ls", "--color=always"], ["ls", "-z"], ["tail", "-f", "a.txt"], ["head", "-n", "x", "a.txt"],
    ["grep", "-f", "a.txt", "a.txt"], ["grep", "beta"], ["grep"], ["cat", "-v", "a.txt"], ["which", "../x"],
    ["wc", "--files0-from=a.txt"], ["file", "-C"],
])
def test_options_that_run_code_or_change_state_are_refused(ws, argv):
    with pytest.raises(CommandRefused):
        build_plan(ws, argv)


@pytest.mark.parametrize("argv", [
    ["cat", ".env"], ["cat", "./.env"], ["cat", "sub/../.env"], ["head", "-n", "1", ".env"], ["grep", "T", ".env"],
    ["ls", ".pbg"], ["cat", ".pbg/ai-actions.jsonl"], ["ls", ".git"],
    ["cat", "~/.ssh/id_rsa"], ["cat", "/etc/passwd"], ["ls", ".."], ["ls", "/"], ["cat", "../outside.txt"],
    ["find", "..", "-name", "x"], ["find", ".", "-name", ".env"],
    ["cat", os.path.expanduser("~/.ssh/id_rsa")], ["ls", os.path.expanduser("~/.aws")],
])
def test_secret_locations_and_paths_outside_the_workspace_are_refused(ws, argv):
    with pytest.raises(CommandRefused):
        build_plan(ws, argv)


def test_a_symlink_inside_the_workspace_cannot_lead_out(ws, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("nope")
    (ws / "link").symlink_to(outside)
    with pytest.raises(CommandRefused):
        build_plan(ws, ["cat", "link/x.txt"])
    with pytest.raises(CommandRefused):
        build_plan(ws, ["ls", "link"])
    with pytest.raises(CommandRefused):
        build_plan(ws, ["ls"], cwd="link")


def test_a_symlink_to_a_secret_inside_the_workspace_is_refused(ws):
    (ws / "innocent.txt").symlink_to(ws / ".env")
    with pytest.raises(CommandRefused):
        build_plan(ws, ["cat", "innocent.txt"])


def test_the_working_folder_must_be_inside(ws, tmp_path):
    with pytest.raises(CommandRefused):
        build_plan(ws, ["ls"], cwd="..")
    with pytest.raises(CommandRefused):
        build_plan(ws, ["ls"], cwd=str(tmp_path))
    with pytest.raises(CommandRefused):
        build_plan(ws, ["ls"], cwd="a.txt")        # a file, not a folder
    assert build_plan(ws, ["pwd"], cwd="sub").cwd == (ws / "sub").resolve()


def test_extra_folders_are_explicit_and_checked(ws, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "n.txt").write_text("n")
    # not granted: refused. granted: allowed, and only that folder
    with pytest.raises(CommandRefused):
        build_plan(ws, ["cat", str(other / "n.txt")])
    p = build_plan(ws, ["cat", str(other / "n.txt")], extra_dirs=[str(other)])
    assert p.extra_dirs == (other.resolve(),) and run(p)["stdout"] == "n"
    with pytest.raises(CommandRefused):
        build_plan(ws, ["cat", str(tmp_path / "ws" / "a.txt"), str(tmp_path / "x")], extra_dirs=[str(other)])
    for bad in (["/"], [os.path.expanduser("~")], ["relative"], [str(tmp_path / "missing")],
                [os.path.expanduser("~/.ssh")], [str(ws / ".env")], [str(other)] * 4, "nope", [7]):
        with pytest.raises(CommandRefused):
            build_plan(ws, ["ls"], extra_dirs=bad)


def test_a_grant_does_not_unlock_secrets_inside_it(ws, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / ".env").write_text("X=1")
    with pytest.raises(CommandRefused):
        build_plan(ws, ["cat", str(other / ".env")], extra_dirs=[str(other)])


def test_too_many_or_too_long_arguments_are_refused(ws):
    with pytest.raises(CommandRefused):
        build_plan(ws, ["ls", *["a.txt"] * rc.MAX_ARGS])
    with pytest.raises(CommandRefused):
        build_plan(ws, ["grep", "-e", "x" * (rc.MAX_ARG_LEN + 1), "a.txt"])
    with pytest.raises(CommandRefused):
        build_plan(ws, ["ls", "a\x00b"])


def test_the_remember_key_pins_argv_cwd_and_extra_folders(ws, tmp_path):
    other = tmp_path / "o"
    other.mkdir()
    assert plan(ws, "ls").key == plan(ws, "ls").key
    assert plan(ws, "ls").key != plan(ws, "ls", "-l").key
    assert plan(ws, "ls").key != plan(ws, "ls", cwd="sub").key
    assert plan(ws, "ls").key != plan(ws, "ls", extra_dirs=[str(other)]).key


# --- what actually runs -----------------------------------------------------------------------------------------


def test_commands_run_and_report(ws):
    out = run(plan(ws, "ls"))
    assert out["exit_code"] == 0 and "a.txt" in out["stdout"] and not out["truncated"] and not out["timed_out"]
    assert run(plan(ws, "pwd"))["stdout"].strip() == str(ws.resolve())
    assert run(plan(ws, "head", "-n", "2", "a.txt"))["stdout"] == "alpha\nbeta\n"
    assert run(plan(ws, "wc", "-l", "a.txt"))["stdout"].split()[0] == "3"
    g = run(plan(ws, "grep", "-n", "beta", "a.txt"))
    assert g["exit_code"] == 0 and g["stdout"].strip() == "2:beta"
    assert run(plan(ws, "grep", "zzz", "a.txt"))["exit_code"] == 1        # no match: a result, not an error
    assert "b.py" in run(plan(ws, "find", ".", "-name", "*.py"))["stdout"]
    assert run(plan(ws, "ls", "no-such-file"))["exit_code"] != 0           # a failing command is reported, not raised


def test_standard_input_is_closed(ws):
    t = time.monotonic()
    out = run(plan(ws, "cat"), timeout=5)             # no file: would block forever on a terminal
    assert out["stdout"] == "" and time.monotonic() - t < 4


def test_the_child_environment_is_an_allow_list_with_a_throwaway_home(ws, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_GH_TOKEN", "ghp_secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("PYTHONPATH", "/evil")
    monkeypatch.setenv("LD_PRELOAD", "/evil.so")
    p = Plan(argv=("env",), exe="/usr/bin/env", args=(), cwd=ws.resolve(), roots=(ws.resolve(),), extra_dirs=(),
             rememberable=False)
    seen = dict(line.split("=", 1) for line in run(p)["stdout"].splitlines() if "=" in line)
    assert not ({"ANTHROPIC_API_KEY", "VIVARIUM_WORKBENCH_GH_TOKEN", "AWS_SECRET_ACCESS_KEY", "PYTHONPATH",
                 "LD_PRELOAD"} & set(seen))
    assert set(seen) <= set(rc.build_env("x")) | {"PWD", "SHLVL", "_", "OLDPWD"}
    assert seen["HOME"] != os.path.expanduser("~") and seen["HOME"].startswith(("/tmp", "/var", "/private"))
    assert seen["PATH"] == ":".join(rc.SAFE_PATH)


def test_a_hanging_command_is_stopped_at_the_deadline(ws):
    os.mkfifo(ws / "pipe")                        # opening it for reading blocks until a writer appears: forever
    t = time.monotonic()
    out = run(plan(ws, "cat", "pipe"), timeout=0.6)
    assert out["timed_out"] and time.monotonic() - t < 5
    assert not rc._LIVE                          # nothing left registered


def test_a_flood_is_cut_off_and_marked(ws):
    (ws / "big.txt").write_text("x" * 2_000_000)
    t = time.monotonic()
    out = run(plan(ws, "cat", "big.txt"), cap=5000)
    assert out["truncated"] and len(out["stdout"]) <= 5000 and time.monotonic() - t < 5


def test_the_whole_process_group_is_killed_not_just_the_child(ws):
    pidfile = ws / "child.pid"
    p = Plan(argv=("sh",), exe="/bin/sh", args=("-c", f"sleep 60 & echo $! > {pidfile}; wait"), cwd=ws.resolve(),
             roots=(ws.resolve(),), extra_dirs=(), rememberable=False)
    out = run(p, timeout=1.0)
    assert out["timed_out"]
    child = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(child, signal.SIGKILL)               # do not leave it behind if the assertion below fails
    pytest.fail("the grandchild survived the deadline")


def test_an_unstartable_program_is_reported_not_raised(ws):
    p = Plan(argv=("x",), exe=str(ws / "nope"), args=(), cwd=ws.resolve(), roots=(ws.resolve(),), extra_dirs=(),
             rememberable=False)
    assert "could not start" in run(p)["error"]


# --- git: the repository's own configuration must not run anything -------------------------------------------


@needs_git
def test_git_cannot_be_made_to_run_a_program_by_the_repositorys_own_config(ws):
    marker = ws.parent / "ran"
    hook = ws.parent / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
    hook.chmod(0o755)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def git(*a):
        subprocess.run(["git", "-C", str(ws), *a], check=True, env=env, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("add", "a.txt")
    git("commit", "-q", "-m", "one")
    (ws / "a.txt").write_text("changed\n")
    # only now make the repository hostile (its config names programs for git to run)
    git("config", "diff.external", str(hook))             # an external diff driver
    git("config", "core.fsmonitor", str(hook))           # a filesystem-monitor hook
    git("config", "core.pager", str(hook))
    marker.unlink(missing_ok=True)

    # CONTROL: plain git, run the way a shell would, DOES run the program: the config is genuinely hostile.
    subprocess.run(["git", "-C", str(ws), "status"], env=env, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "diff"], env=env, capture_output=True)
    assert marker.exists(), "control failed: the hostile config did not run anything, so this test proves nothing"
    marker.unlink()

    for argv in (["git", "diff"], ["git", "status"], ["git", "log", "-p"], ["git", "show", "HEAD"], ["git", "diff", "--stat"]):
        out = run(plan(ws, *argv))
        assert out["exit_code"] == 0, (argv, out)
    assert not marker.exists(), "git ran a program named in the repository's config"
    assert "changed" in run(plan(ws, "git", "diff"))["stdout"]
    assert "one" in run(plan(ws, "git", "log", "--oneline"))["stdout"]


@needs_git
def test_git_cannot_read_a_path_outside_through_diff(ws, tmp_path):
    (tmp_path / "x.txt").write_text("1")
    (tmp_path / "y.txt").write_text("2")
    with pytest.raises(CommandRefused):
        build_plan(ws, ["git", "diff", str(tmp_path / "x.txt"), str(tmp_path / "y.txt")])
    with pytest.raises(CommandRefused):
        build_plan(ws, ["git", "show", "HEAD:.env"])


# --- findings from the adversarial review (each one reproduced before it was fixed) ---------------------------------


def test_two_different_requests_never_share_a_remember_key(ws):
    """An argument may contain control characters: they must not let ['ls','a\\x1fb'] pose as ['ls','a','b']."""
    a, b = plan(ws, "ls", "a\x1fb"), plan(ws, "ls", "a", "b")
    assert a.key != b.key
    keys = {plan(ws, *argv).key for argv in (["ls"], ["ls", "-l"], ["ls", "a.txt"], ["ls", "sub"], ["pwd"], ["ls", "a\x1eb"])}
    assert len(keys) == 6


@pytest.mark.parametrize("argv", [["cat", ".PBG/ai-actions.jsonl"], ["ls", ".Pbg"], ["cat", ".GIT/config"], ["ls", ".gIt"],
                                  ["head", "-n", "1", "sub/../.PBG/ai-actions.jsonl"]])
def test_the_internal_folders_are_refused_whatever_their_case(ws, argv):
    """macOS volumes are case-insensitive: .PBG is the same folder as .pbg."""
    with pytest.raises(CommandRefused):
        build_plan(ws, argv)


@pytest.fixture
def secrets_ws(ws):
    (ws / "sub" / "ok.txt").write_text("visible TOKEN here\n")
    (ws / ".env").write_text("TOKEN=hunter2\n")
    (ws / ".pbg" / "ai-actions.jsonl").write_text('{"TOKEN":"audit"}\n')
    (ws / ".ssh").mkdir()
    (ws / ".ssh" / "id_ed25519").write_text("TOKEN-private-key\n")
    (ws / "secrets.yaml").write_text("TOKEN: s3cret\n")
    (ws / "sub" / "server.pem").write_text("TOKEN-pem\n")
    (ws / "sub" / ".env.local").write_text("TOKEN=local\n")
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("TOKEN-gitconfig\n")
    return ws


def test_a_recursive_grep_never_enters_or_reads_the_secret_places(secrets_ws):
    out = run(plan(secrets_ws, "grep", "-rn", "TOKEN", "."))["stdout"]
    assert "sub/ok.txt" in out, out
    for leaked in ("hunter2", "audit", "private-key", "s3cret", "TOKEN-pem", "local", "gitconfig"):
        assert leaked not in out, f"a recursive grep read {leaked!r}"


def test_a_recursive_find_prunes_the_internal_and_secret_folders(secrets_ws):
    out = run(plan(secrets_ws, "find", ".", "-name", "*"))["stdout"]
    assert "ok.txt" in out
    for pruned in (".pbg/", ".ssh/", ".git/", "ai-actions.jsonl", "id_ed25519"):
        assert pruned not in out, f"find descended into {pruned!r}"
    # tests and depth limits still work together with the pruning
    out2 = run(plan(secrets_ws, "find", ".", "-maxdepth", "2", "-type", "f", "-name", "ok.txt"))["stdout"]
    assert out2.strip().endswith("sub/ok.txt")


def test_the_recursion_excludes_are_added_only_for_a_recursive_grep(ws):
    assert not any(a.startswith("--exclude") for a in plan(ws, "grep", "-n", "x", "a.txt").args)
    assert not any(a.startswith("--exclude") for a in plan(ws, "grep", "-e", "r", "a.txt").args)      # 'r' is a pattern here
    assert any(a == "--exclude-dir=.pbg" for a in plan(ws, "grep", "-rn", "x", "sub").args)


def test_files_in_a_dot_claude_folder_inside_the_workspace_stay_readable(ws):
    (ws / ".claude").mkdir()
    (ws / ".claude" / "settings.json").write_text("{}\n")
    assert run(plan(ws, "cat", ".claude/settings.json"))["stdout"] == "{}\n"


def test_extra_folders_that_are_too_broad_or_per_user_state_are_refused(ws):
    home = Path(os.path.expanduser("~"))
    for bad in ("/etc", "/usr", "/private/etc", str(home.parent), str(home / ".claude"), str(home / ".azure")):
        with pytest.raises(CommandRefused):
            build_plan(ws, ["ls"], extra_dirs=[bad])


# --- git: confined to the workspace, read-only, and the repository's own config/attributes cannot run anything -----


def _git(path: Path, *a: str, check: bool = True):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-C", str(path), *a],
                          check=check, env=env, capture_output=True, text=True)


@needs_git
def test_git_in_a_workspace_that_sits_under_a_parent_repo_is_refused(tmp_path):
    parent = tmp_path / "parent"
    (parent / "ws").mkdir(parents=True)
    (parent / "topsecret.txt").write_text("outside the workspace")
    _git(parent, "init", "-q")
    _git(parent, "add", "-A")
    _git(parent, "commit", "-q", "-m", "x")
    for argv in (["git", "show", "HEAD:topsecret.txt"], ["git", "status"], ["git", "log", "--oneline"]):
        with pytest.raises(CommandRefused, match="outside the workspace"):
            build_plan(parent / "ws", argv)


@needs_git
def test_git_whose_directory_was_redirected_outside_the_workspace_is_refused(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.txt").write_text("x")
    _git(tmp_path, "init", "-q", f"--separate-git-dir={tmp_path / 'elsewhere.git'}", str(ws))
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "x")
    assert (ws / ".git").is_file(), "setup: .git is a gitfile pointing outside"
    with pytest.raises(CommandRefused, match="git directory"):
        build_plan(ws, ["git", "status"])


@needs_git
def test_git_branch_cannot_create_a_branch(ws):
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "x")
    for argv in (["git", "branch", "newbranch"], ["git", "branch", "newbranch", "HEAD"]):
        with pytest.raises(CommandRefused, match="create"):
            build_plan(ws, argv)
    assert "main" in run(plan(ws, "git", "branch", "-a"))["stdout"]
    assert "main" in run(plan(ws, "git", "branch", "--list"))["stdout"]
    assert "newbranch" not in _git(ws, "branch").stdout


@needs_git
def test_a_repositorys_attributes_cannot_make_git_run_a_filter(ws):
    marker = ws.parent / "filter-ran"
    hook = ws.parent / "filter.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    hook.chmod(0o755)
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "one")
    _git(ws, "config", "filter.x.clean", str(hook))
    (ws / ".gitattributes").write_text("a.txt filter=x\n")
    (ws / "a.txt").write_text("ALPHA\nBETA\n")           # same length as before: git must READ the file to see a change
    os.utime(ws / "a.txt", (1, 1))                        # and its timestamp no longer matches the index
    marker.unlink(missing_ok=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "-C", str(ws), "status"], env=env, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "diff"], env=env, capture_output=True)
    assert marker.exists(), "control failed: plain git did not run the in-tree filter, so this test proves nothing"
    marker.unlink()
    for argv in (["git", "status"], ["git", "diff"], ["git", "log", "-p"], ["git", "show", "HEAD"]):
        assert run(plan(ws, *argv))["exit_code"] == 0
    assert not marker.exists(), "git ran a filter named by the repository's own attributes and config"


@needs_git
def test_history_and_diffs_leave_out_tracked_secrets(ws):
    (ws / "keys").mkdir()
    (ws / "keys" / "id_rsa").write_text("PRIVATE-KEY-ONE\n")
    (ws / "keys" / "server.pem").write_text("PEM-ONE\n")
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "add", "-A", "-f")
    _git(ws, "commit", "-q", "-m", "one")
    (ws / "a.txt").write_text("alpha CHANGED\n")
    (ws / ".env").write_text("TOKEN=hunter2-CHANGED\n")
    (ws / "keys" / "id_rsa").write_text("PRIVATE-KEY-TWO\n")
    _git(ws, "add", "-A", "-f")
    _git(ws, "commit", "-q", "-m", "two")
    (ws / "a.txt").write_text("alpha UNCOMMITTED\n")
    (ws / ".env").write_text("TOKEN=hunter2-UNCOMMITTED\n")
    for argv in (["git", "log", "-p"], ["git", "show", "HEAD"], ["git", "diff"], ["git", "diff", "HEAD~1"], ["git", "show", "HEAD~1"]):
        out = run(plan(ws, *argv))
        assert out["exit_code"] == 0, (argv, out)
        for leaked in ("hunter2", "PRIVATE-KEY", "PEM-ONE"):
            assert leaked not in out["stdout"], f"{argv} printed {leaked}"
    assert "alpha CHANGED" in run(plan(ws, "git", "show", "HEAD"))["stdout"]          # ordinary content is still shown
    assert "alpha UNCOMMITTED" in run(plan(ws, "git", "diff"))["stdout"]
    assert "a.txt" in run(plan(ws, "git", "diff", "HEAD~1", "--stat"))["stdout"]
