"""Validate and run ONE approved command for the chat (docs/plan-autonomous-bash-commands.md).

Nothing here decides whether a command may run: the user does, on an approval card, and the Claude Code MCP
handler asks them (``lib/claude_mcp``). This module makes sure that what the card shows is what runs, and that
what runs is small:

* an explicit **allow-list** of programs and flags (never a deny-list: ``git -c``, ``find -exec``, ``python -c``
  run code), and no shell, ever: the command is an argv list handed straight to ``exec``;
* every path (the working folder, path arguments, approved extra folders) is resolved with ``realpath`` and must
  sit inside the workspace or an approved extra folder: checked here, at the point of use, never by field name;
* commands that name a known secret location (~/.ssh, ~/.config, ~/.aws, .env, git credentials ...) are refused
  outright. Best effort, and said so: a command can build a path itself;
* the child gets a **scrubbed environment** built from an allow-list (never inherited), a throw-away HOME, closed
  stdin, its own process group, a deadline and an output cap; it is stopped by exact pid.

Without an OS sandbox (a decision), the card is the only control over what an approved command can read; the
allow-list and the checks above shrink what there is to approve.
"""
from __future__ import annotations

import asyncio
import atexit
import os
import re
import signal
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vivarium_workbench.lib.proc_group import signal_group

TIMEOUT_S = 30.0
OUTPUT_CAP = 64 * 1024               # bytes kept per stream; a child that writes more is stopped
MAX_EXTRA_DIRS = 3
MAX_ARGS = 40
MAX_ARG_LEN = 500
# Where the programs are looked up: fixed, so a PATH in the server's environment cannot redirect `ls` or `git`.
SAFE_PATH = ("/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin")

# Remembered for the rest of the chat session once approved (read-only, run no code, reach no network).
REMEMBERABLE = frozenset({"ls", "pwd", "wc", "head", "tail", "file", "which"})

_SECRET = re.compile(
    r"(^|/)(\.ssh|\.aws|\.gnupg|\.kube|\.docker|\.netrc|\.git-credentials|\.npmrc|\.pypirc|\.config|\.local/share/keyrings"
    r"|Library/Keychains)(/|$)"
    r"|(^|/)\.env(\.[^/]*)?(/|$)"
    r"|(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$"
    r"|\.(pem|p12|pfx|key)$"
    r"|(^|/)(credentials|secrets?)(\.[a-z]+)?$",
    re.IGNORECASE)
_PROTECTED = frozenset({".git", ".pbg"})      # the audit/claim store and the repository internals: never a path argument
_REV = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/~^@{}:-]*$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_NUM = re.compile(r"^[0-9]{1,6}$")


class CommandRefused(ValueError):
    """The command is not allowed; the message is written for the model (and the user) to read."""


@dataclass(frozen=True)
class Plan:
    """A validated command: exactly what the card shows and what runs."""
    argv: tuple[str, ...]            # as the model sent it (the display form)
    exe: str                         # absolute path of the program
    args: tuple[str, ...]            # what is actually passed to it (flags we add are included)
    cwd: Path                        # realpath
    roots: tuple[Path, ...]          # realpaths the command may touch: the workspace first, then approved extra folders
    extra_dirs: tuple[Path, ...]
    rememberable: bool
    limits: dict[str, Any] = field(default_factory=lambda: {"timeout_s": TIMEOUT_S, "output_cap_bytes": OUTPUT_CAP})

    @property
    def key(self) -> str:
        """Identity of this exact command for a remembered approval: argv + realpath cwd + extra folders."""
        return "\x1f".join([*self.argv, "\x1e", str(self.cwd), "\x1e", *map(str, self.extra_dirs)])


# --- paths -------------------------------------------------------------------------------------------------


def _inside(p: Path, roots: tuple[Path, ...]) -> bool:
    return any(p == r or p.is_relative_to(r) for r in roots)


def _refuse_secret(text: str, what: str) -> None:
    if _SECRET.search(text.replace("\\", "/")):
        raise CommandRefused(f"{what} names a secret location ({text!r}); commands touching credentials, keys or "
                             ".env files are refused")


def _resolve(arg: str, cwd: Path, roots: tuple[Path, ...], *, what: str = "path") -> Path:
    if not arg or "\x00" in arg:
        raise CommandRefused(f"empty or invalid {what}")
    if arg.startswith("~"):
        raise CommandRefused(f"{what} {arg!r}: '~' is not expanded; use a path inside the working folder")
    _refuse_secret(arg, what)
    p = Path(os.path.realpath(arg if os.path.isabs(arg) else os.path.join(cwd, arg)))
    if not _inside(p, roots):
        raise CommandRefused(f"{what} {arg!r} resolves outside the workspace and the approved folders ({p})")
    _refuse_secret(str(p), what)
    root = next(r for r in roots if _inside(p, (r,)))
    if _PROTECTED.intersection(p.relative_to(root).parts):
        raise CommandRefused(f"{what} {arg!r}: .git and .pbg are not accessible to commands")
    return p


def _extra_dirs(raw: object, ws: Path) -> tuple[Path, ...]:
    if raw in (None, [], ()):
        return ()
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_EXTRA_DIRS:
        raise CommandRefused(f"extra_dirs must be a list of at most {MAX_EXTRA_DIRS} absolute folders")
    out: list[Path] = []
    home = Path(os.path.realpath(os.path.expanduser("~")))
    for d in raw:
        if not isinstance(d, str) or not os.path.isabs(d) or "\x00" in d:
            raise CommandRefused(f"extra folder {d!r} must be an absolute path")
        _refuse_secret(d, "extra folder")
        p = Path(os.path.realpath(d))
        _refuse_secret(str(p), "extra folder")
        if not p.is_dir():
            raise CommandRefused(f"extra folder {d!r} is not an existing folder")
        if p == Path(p.anchor) or p == home:
            raise CommandRefused(f"extra folder {d!r} is too broad (the filesystem root or your home folder)")
        if p != ws and p not in out:
            out.append(p)
    return tuple(out)


# --- the allow-list ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Cmd:
    short: frozenset[str] = frozenset()                  # single-letter flags (may be combined: -la)
    long: frozenset[str] = frozenset()                   # exact long flags with no value
    num_short: frozenset[str] = frozenset()              # -n 5 / -n5 (a number follows)
    num_long: frozenset[str] = frozenset()               # --max-count=5
    pattern_short: frozenset[str] = frozenset()          # -e PATTERN
    positional: str = "paths"                            # "paths" | "names" | "none"
    first_is_pattern: bool = False                       # grep: the first positional is the pattern unless -e given


_CMDS: dict[str, _Cmd] = {
    "ls": _Cmd(short=frozenset("lahA1tSrdF"), positional="paths"),
    "pwd": _Cmd(short=frozenset("LP"), positional="none"),
    "wc": _Cmd(short=frozenset("lwcm"), positional="paths"),
    "head": _Cmd(num_short=frozenset("nc"), positional="paths"),
    "tail": _Cmd(num_short=frozenset("nc"), positional="paths"),
    "file": _Cmd(short=frozenset("b"), long=frozenset({"--brief"}), positional="paths"),
    "which": _Cmd(short=frozenset("a"), positional="names"),
    "cat": _Cmd(short=frozenset("n"), positional="paths"),
    "grep": _Cmd(short=frozenset("nirlcvwFEHh"), num_short=frozenset("m"), pattern_short=frozenset("e"),
                 positional="paths", first_is_pattern=True),
}

# git: subcommand -> flags. Runs with a fixed config (see _git_prefix); nothing here can set config, a pager,
# a git dir, a work tree or an external program.
_GIT: dict[str, _Cmd] = {
    "status": _Cmd(short=frozenset("sb"), long=frozenset({"--short", "--branch", "--porcelain"}), positional="none"),
    "log": _Cmd(short=frozenset("p"), num_short=frozenset("n"), long=frozenset({"--oneline", "--stat", "--name-only", "--no-merges"}),
                num_long=frozenset({"--max-count"}), positional="revs"),
    "diff": _Cmd(long=frozenset({"--stat", "--name-only", "--name-status", "--cached", "--staged", "--shortstat"}),
                 positional="revs"),
    "show": _Cmd(long=frozenset({"--stat", "--name-only", "--name-status", "--oneline"}), positional="revs"),
    "branch": _Cmd(short=frozenset("arv"), long=frozenset({"--list", "--show-current"}), positional="revs"),
    "ls-files": _Cmd(short=frozenset("mos"), positional="paths"),
    "rev-parse": _Cmd(long=frozenset({"--short", "--abbrev-ref", "--show-toplevel", "--is-inside-work-tree", "--verify"}),
                      positional="revs"),
}
_GIT_NO_EXT = frozenset({"log", "diff", "show"})        # never run an external diff driver or text conversion
_FIND_PREDICATES = {"-name": "pattern", "-iname": "pattern", "-type": "type", "-maxdepth": "num", "-mindepth": "num"}


def _parse_flags(cmd: _Cmd, args: list[str], name: str) -> tuple[list[str], list[str], list[str]]:
    """-> (flag tokens to pass through, positionals, positionals that came after ``--``)."""
    flags: list[str] = []
    pos: list[str] = []
    after: list[str] = []
    saw_dd = False
    i = 0
    while i < len(args):
        a = args[i]
        if saw_dd:
            after.append(a)
        elif a == "--":
            saw_dd = True
        elif a.startswith("--"):
            n, eq, v = a.partition("=")
            if n in cmd.long and not eq:
                flags.append(a)
            elif n in cmd.num_long and eq and _NUM.match(v):
                flags.append(a)
            else:
                raise CommandRefused(f"{name}: option {a!r} is not allowed")
        elif a.startswith("-") and len(a) > 1:
            body = a[1:]
            ch = body[0]
            if ch in cmd.num_short:
                rest = body[1:]
                if rest:
                    val = rest
                elif i + 1 < len(args):
                    i += 1
                    val = args[i]
                else:
                    raise CommandRefused(f"{name}: -{ch} needs a number")
                if not _NUM.match(val):
                    raise CommandRefused(f"{name}: -{ch} takes a number, got {val!r}")
                flags += [f"-{ch}", val]
            elif ch in cmd.pattern_short and len(body) == 1:
                if i + 1 >= len(args):
                    raise CommandRefused(f"{name}: -{ch} needs a pattern")
                i += 1
                flags += [f"-{ch}", args[i]]
            elif all(c in cmd.short for c in body):
                flags.append(a)
            else:
                raise CommandRefused(f"{name}: option {a!r} is not allowed")
        else:
            pos.append(a)
        i += 1
    return flags, pos, after


def _check_len(argv: list[str]) -> None:
    if not 1 <= len(argv) <= MAX_ARGS:
        raise CommandRefused(f"a command is 1 to {MAX_ARGS} arguments")
    for a in argv:
        if not isinstance(a, str) or "\x00" in a or len(a) > MAX_ARG_LEN:
            raise CommandRefused(f"each argument must be a text of at most {MAX_ARG_LEN} characters without NUL")


def _paths(positional: list[str], cwd: Path, roots: tuple[Path, ...]) -> list[str]:
    return [str(_resolve(p, cwd, roots, what="path argument")) for p in positional]


def _plan_simple(name: str, args: list[str], cwd: Path, roots: tuple[Path, ...]) -> list[str]:
    cmd = _CMDS[name]
    flags, pos, after = _parse_flags(cmd, args, name)
    pos += after
    out = list(flags)
    if cmd.first_is_pattern and not any(f == "-e" for f in flags):
        if not pos:
            raise CommandRefused("grep needs a pattern")
        out += ["-e", pos.pop(0)]
    if cmd.positional == "none":
        if pos:
            raise CommandRefused(f"{name} takes no arguments")
    elif cmd.positional == "names":
        if not pos or not all(_NAME.match(p) for p in pos):
            raise CommandRefused(f"{name} takes program names (letters, digits, . _ + -)")
        out += pos
    else:
        resolved = _paths(pos, cwd, roots)
        if name == "grep" and not resolved:
            raise CommandRefused("grep needs at least one file or folder (it will not read standard input)")
        out += ["--", *resolved] if resolved else []
    return out


def _plan_find(args: list[str], cwd: Path, roots: tuple[Path, ...]) -> list[str]:
    i = 0
    starts: list[str] = []
    while i < len(args) and not args[i].startswith("-"):
        starts.append(args[i])
        i += 1
    out = [*(_paths(starts, cwd, roots) or [str(cwd)])]
    while i < len(args):
        pred = args[i]
        kind = _FIND_PREDICATES.get(pred)
        if kind is None:
            raise CommandRefused(f"find: {pred!r} is not allowed (only -name, -iname, -type, -maxdepth, -mindepth)")
        if i + 1 >= len(args):
            raise CommandRefused(f"find: {pred} needs a value")
        val = args[i + 1]
        if kind == "num" and not _NUM.match(val) or kind == "type" and val not in ("f", "d", "l"):
            raise CommandRefused(f"find: bad value {val!r} for {pred}")
        if kind == "pattern":
            _refuse_secret(val, "a pattern")
        out += [pred, val]
        i += 2
    return out


def _plan_git(args: list[str], cwd: Path, roots: tuple[Path, ...]) -> list[str]:
    if not args or args[0] not in _GIT:
        raise CommandRefused(f"git: only {', '.join(sorted(_GIT))} are allowed")
    sub, rest = args[0], args[1:]
    cmd = _GIT[sub]
    flags, pos, after = _parse_flags(cmd, rest, f"git {sub}")
    out = [sub, *(["--no-ext-diff", "--no-textconv"] if sub in _GIT_NO_EXT else []), *flags]
    if cmd.positional == "none":
        if pos or after:
            raise CommandRefused(f"git {sub} takes no arguments")
    elif cmd.positional == "revs":
        for r in pos:
            if not _REV.match(r):
                raise CommandRefused(f"git {sub}: {r!r} is not a revision or name I accept")
            _refuse_secret(r.replace(":", "/"), "a revision")
            if "/" in r and os.path.exists(os.path.join(cwd, r)):          # path-like: must stay inside
                _resolve(r, cwd, roots, what="revision")
        out += pos
        if after:
            out += ["--", *_paths(after, cwd, roots)]
    else:                                                                  # "paths"
        paths = _paths(pos + after, cwd, roots)
        if paths:
            out += ["--", *paths]
    return out


# --- planning ----------------------------------------------------------------------------------------------


def _locate(name: str) -> str:
    for d in SAFE_PATH:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    raise CommandRefused(f"{name}: program not found in {':'.join(SAFE_PATH)}")


def build_plan(ws_root: Path | str, argv: object, cwd: object = None, extra_dirs: object = None) -> Plan:
    """Validate a request and return the exact plan, or raise :class:`CommandRefused`."""
    if not isinstance(argv, (list, tuple)) or not argv:
        raise CommandRefused("argv must be a non-empty list: the program name, then one item per argument")
    argv_l = list(argv)
    _check_len(argv_l)
    name = argv_l[0]
    if "/" in name or name in ("", ".", ".."):
        raise CommandRefused(f"{name!r}: give the program name only (no path)")
    ws = Path(os.path.realpath(ws_root))
    extras = _extra_dirs(extra_dirs, ws)
    roots = (ws, *extras)
    if cwd in (None, "", "."):
        cwd_p = ws
    else:
        if not isinstance(cwd, str):
            raise CommandRefused("cwd must be a text path")
        cwd_p = _resolve(cwd, ws, roots, what="working folder")
    if not cwd_p.is_dir():
        raise CommandRefused(f"working folder {cwd!r} is not a folder")
    if name == "git":
        body = _plan_git(argv_l[1:], cwd_p, roots)
        args = ["--no-pager", "-c", "core.fsmonitor=false", *body]
    elif name == "find":
        args = _plan_find(argv_l[1:], cwd_p, roots)
    elif name in _CMDS:
        args = _plan_simple(name, argv_l[1:], cwd_p, roots)
    else:
        raise CommandRefused(f"{name!r} is not an allowed program; allowed: "
                             f"{', '.join(sorted([*_CMDS, 'find', 'git']))}")
    return Plan(argv=tuple(argv_l), exe=_locate(name), args=tuple(args), cwd=cwd_p, roots=roots, extra_dirs=extras,
                rememberable=name in REMEMBERABLE)


# --- running -----------------------------------------------------------------------------------------------


def build_env(home: str) -> dict[str, str]:
    """The child's whole environment: an allow-list, never a copy of the server's."""
    return {"PATH": ":".join(SAFE_PATH), "HOME": home, "TMPDIR": home, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat", "GIT_OPTIONAL_LOCKS": "0", "PAGER": "cat", "NO_COLOR": "1"}


_LIVE: set[int] = set()


@atexit.register
def _reap() -> None:
    for pid in list(_LIVE):
        signal_group(pid, signal.SIGKILL)


async def _drain(stream: asyncio.StreamReader, cap: int, over: asyncio.Event) -> tuple[bytes, bool]:
    buf = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        room = cap - len(buf)
        buf += chunk[:room]
        if len(chunk) > room and not truncated:
            truncated = True
            over.set()                 # a flood: the caller stops the child; keep draining (and discarding) to EOF,
            #                            or the unread pipe never closes and asyncio's proc.wait() never returns
    return bytes(buf), truncated


async def run(plan: Plan, *, timeout: float = TIMEOUT_S, cap: int = OUTPUT_CAP) -> dict[str, Any]:
    """Run an already validated (and approved) plan. Never raises for a failing command; reports it."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vwb-cmd-") as home:
        try:
            proc = await asyncio.create_subprocess_exec(
                plan.exe, *plan.args, cwd=str(plan.cwd), env=build_env(home), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        except OSError as e:
            return {"error": f"could not start {plan.argv[0]!r}: {e}"}
        _LIVE.add(proc.pid)
        over = asyncio.Event()
        timed_out = False
        readers = asyncio.gather(_drain(proc.stdout, cap, over), _drain(proc.stderr, cap, over))   # type: ignore[arg-type]
        try:
            waiter = asyncio.ensure_future(proc.wait())
            flood = asyncio.ensure_future(over.wait())
            done, _ = await asyncio.wait({waiter, flood}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if waiter not in done:
                timed_out = not done
                signal_group(proc.pid, signal.SIGKILL)
                await proc.wait()
            (out, out_trunc), (err, err_trunc) = await asyncio.wait_for(readers, 5)
            flood.cancel()
        except BaseException:
            signal_group(proc.pid, signal.SIGKILL)         # incl. a cancelled caller: never leave the group behind
            readers.cancel()
            raise
        finally:
            _LIVE.discard(proc.pid)
            signal_group(proc.pid, signal.SIGKILL)
    return {"exit_code": proc.returncode, "stdout": out.decode("utf-8", "replace"), "stderr": err.decode("utf-8", "replace"),
            "truncated": out_trunc or err_trunc, "timed_out": timed_out,
            "duration_s": round(time.monotonic() - started, 3), "cwd": str(plan.cwd), "argv": list(plan.argv)}
