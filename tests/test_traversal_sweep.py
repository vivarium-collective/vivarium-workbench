"""Path-containment sweep over the whole API surface.

The proposition this file falsifies: *no request that puts a parent-directory, absolute or otherwise
path-like value into an identifier or file field reads, writes, deletes or moves anything outside the
workspace, changes a protected file (``.git/config``, hooks, ``.pbg/ai-actions.jsonl``), or returns the
contents of a file outside the workspace or of a workspace outside.*

Three independent observations decide a request: (1) an audit hook records every filesystem touch (open, scandir,
mkdir, remove, rename, sqlite connect ...) that lands outside the workspace — reads included, so a route that
merely *looks* at a directory it should not is caught even when nothing leaks; (2) a before/after snapshot of
everything outside the workspace and of the protected files inside it; (3) sentinels that must never appear in a
response. The process working directory is a directory outside the workspace, so a path that resolves against
the CWD instead of the workspace is caught too.

It is generated from the live OpenAPI schema (``app.openapi()``), so a route added later with a path-like
parameter is covered without anyone remembering to add a test. Everything runs in a temp dir against the real
app; nothing is mocked.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys
import re
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from vivarium_workbench.api import app as appmod
from vivarium_workbench.lib import _root

# --- what counts as a path-like input -------------------------------------------------------------------

# Values that name a directory entry (study / investigation / run ...). Legitimate values are plain names.
IDENT_FIELDS = {
    "study", "investigation", "inv", "slug", "name", "new_name", "target_name", "composite_name",
    "run_id", "uid", "item_id", "bib_key", "simulator_id", "class_name", "job_id", "spec_id",
    "source_prefix", "target_prefix", "n", "ext", "point_id", "mode", "names", "run_ids",
}
# Values that name a file (workspace-relative, or an id that resolves to one).
FILE_FIELDS = {
    "ref", "path", "source_path", "filename", "file", "key", "store_path", "source", "module",
    "id", "composite_id", "dest", "source_db", "db_path", "config_filename",
}
FIELDS = IDENT_FIELDS | FILE_FIELDS

# Operations that reach outside the local filesystem or spawn long-lived work. They take no path-like input
# that this sweep is about, and running them here would only test the network.
SKIP_PREFIXES = ("/api/ai/", "/api/chat/")

# Benign values for the *other* fields of a request, so that it gets past validation and reaches the code that
# touches the filesystem (a dataset upload needs bytes, a rename needs a new name, a source save needs source).
COMPANIONS = {
    "new_name": "renamed-study", "file_b64": base64.b64encode(b"sweep").decode(), "filename": "upload.txt",
    "name": "sweep-item", "lang": "python", "source": "# sweep\n",
}

MARKER_OUTSIDE = "OUTSIDE-MARKER-7f3a"
MARKER_DOTENV = "DOTENV-MARKER-7f3a"
MARKER_GITCONFIG = "GITCONFIG-MARKER-7f3a"
MARKER_AUDIT = "AUDIT-LOG-MARKER-7f3a"


def _payloads(outside: Path) -> dict[str, list[str]]:
    # Routes join a value onto directories of different depths (studies/<x>, datasets/<slug>/<x>,
    # investigations/<i>/studies/<x> ...), so climb by several depths rather than guessing one.
    up = ["../" * n for n in range(1, 6)]
    return {
        "ident": ["..", ".", str(outside / "outside-dir"), "a/../../outside-dir", "a\\..\\..\\outside-dir", "x\x00y"]
        + [u + "outside-dir" for u in up],
        "file": [str(outside / "outside.txt"), ".git/config", ".pbg/ai-actions.jsonl", ".env",
                 "studies/../../outside.txt"] + [u + "outside.txt" for u in up],
    }


# --- audit hook: what the server touches on disk -------------------------------------------------------

_WATCH: dict = {"root": None, "ws": None, "armed": False, "hits": []}
_PATH_EVENTS = {
    "open": (0,), "os.listdir": (0,), "os.scandir": (0,), "os.mkdir": (0,), "os.remove": (0,), "os.rmdir": (0,),
    "os.rename": (0, 1), "os.chdir": (0,), "os.truncate": (0,), "shutil.rmtree": (0,), "shutil.copyfile": (0, 1),
    "shutil.copytree": (0, 1), "shutil.move": (0, 1), "sqlite3.connect": (0,), "os.symlink": (0, 1),
}


def _audit(event, args):
    root = _WATCH["root"]
    idx = _PATH_EVENTS.get(event)
    if root is None or idx is None or not _WATCH["armed"]:
        return
    ws = _WATCH["ws"]
    for i in idx:
        p = args[i] if i < len(args) else None
        if not isinstance(p, (str, bytes, os.PathLike)):
            continue  # a file descriptor, or an in-memory database
        try:
            real = os.path.realpath(os.fsdecode(p))
        except (OSError, ValueError):
            continue
        under_ws = real == str(ws) or real.startswith(str(ws) + os.sep)
        rel = os.path.relpath(real, ws).split(os.sep) if under_ws else []
        protected = under_ws and (rel[0].casefold() == ".git" or rel[0] == ".env"
                                  or rel[:2] == [".pbg", "ai-actions.jsonl"])
        outside = real == str(root) or real.startswith(str(root) + os.sep)
        if protected or (outside and not under_ws):
            _WATCH["hits"].append(f"{event} {real}")


sys.addaudithook(_audit)  # cannot be removed; inert (one dict lookup) unless a sweep test has armed it


# --- workspace + observation --------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path, monkeypatch):
    """``root/ws`` is the workspace. Outside it: ``outside.txt``, an ``outside-dir/`` directory shaped like a study /
    investigation / spec directory (so routes that look for their files find them), a second workspace, and the
    process working directory ``root/cwd``."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    root = (tmp_path / "root").resolve()
    ws = root / "ws"
    for d in ("studies", "investigations", "datasets", ".pbg", ".git/hooks", "outside-dir"):
        (ws / d if d != "outside-dir" else root / d).mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: sweep\n")
    (ws / ".env").write_text(f"TOKEN={MARKER_DOTENV}\n")
    (ws / ".git" / "config").write_text(f"[core]\n\trepositoryformatversion = 0\n# {MARKER_GITCONFIG}\n")
    (ws / ".pbg" / "ai-actions.jsonl").write_text(f'{{"audit": "{MARKER_AUDIT}"}}\n')
    (root / "outside.txt").write_text(MARKER_OUTSIDE + "\n")
    (root / "outside-dir" / "keep.txt").write_text("keep\n")
    for name in ("study.yaml", "investigation.yaml", "spec.yaml"):
        (root / "outside-dir" / name).write_text(f"name: outside-dir\nnote: {MARKER_OUTSIDE}\n")
    (root / "outside-dir" / "viz").mkdir()
    (root / "outside-dir" / "tests").mkdir()
    (root / "outside-dir" / "tests" / "test_x.py").write_text("def test_x():\n    pass\n")
    (root / "other-ws" / "investigations" / "foo").mkdir(parents=True)
    (root / "other-ws" / "workspace.yaml").write_text("name: other\n")
    (root / "other-ws" / "investigations" / "foo" / "investigation.yaml").write_text("name: foo\nmembers: []\n")
    (root / "cwd").mkdir()
    monkeypatch.chdir(root / "cwd")

    saved = _root.get_workspace_root()
    _root.set_workspace_root(ws)
    app = appmod.create_app()
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    _WATCH.update(root=str(root), ws=str(ws), hits=[])
    try:
        yield root, ws, app
    finally:
        _WATCH.update(root=None, ws=None, armed=False, hits=[])
        _root._WS_ROOT = saved
        _root._WS_PATHS = None


def _digest(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest() if p.is_file() else "-"


def _snapshot(root: Path, ws: Path) -> dict[str, str]:
    """Everything outside the workspace, plus the protected files inside it."""
    snap: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p == ws or ws in p.parents:
            continue
        snap[str(p.relative_to(root))] = _digest(p) if p.is_file() else "dir"
    protected = [ws / ".git" / "config", ws / ".pbg" / "ai-actions.jsonl", ws / ".env"]
    protected += sorted((ws / ".git" / "hooks").rglob("*"))
    for p in protected:
        snap["ws:" + str(p.relative_to(ws))] = _digest(p)
    snap["ws:.git/hooks"] = ",".join(sorted(x.name for x in (ws / ".git" / "hooks").iterdir()))
    return snap


def _diff(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return [f"{k}: {before.get(k, 'absent')} -> {after.get(k, 'absent')}"
            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]


# --- operations generated from the OpenAPI schema -----------------------------------------------------


def _operations():
    spec = appmod.create_app().openapi()
    comps = spec["components"]["schemas"]

    def resolve(s):
        return comps[s["$ref"].split("/")[-1]] if "$ref" in s else s

    ops = []
    for path, item in spec["paths"].items():
        if path.startswith(SKIP_PREFIXES):
            continue
        for method, op in item.items():
            if method not in ("get", "post", "delete", "put", "patch"):
                continue
            fields: list[tuple[str, str]] = []  # (location, name)
            props: tuple = ()
            for prm in op.get("parameters", []):
                if prm["name"] in FIELDS:
                    fields.append((prm["in"], prm["name"]))
            body = op.get("requestBody")
            freeform = False
            if body:
                sch = body["content"].get("application/json", {}).get("schema")
                if sch:
                    found = resolve(sch).get("properties")
                    if found:
                        props = tuple(found)
                        fields += [("body", k) for k in found if k in FIELDS]
                    else:
                        freeform = True
                    # ``extra="allow"`` models accept keys they do not declare (the route reads them from the
                    # dict), so the schema alone does not say which path-like keys a route consumes
                    if resolve(sch).get("additionalProperties") is True:
                        freeform = True
            if fields or freeform:
                ops.append((method.upper(), path, tuple(fields), freeform, props))
    return ops


OPERATIONS = _operations()


def _kind(name: str) -> str:
    return "ident" if name in IDENT_FIELDS else "file"


def _send(client: TestClient, method: str, path: str, loc: str, name: str, value: str,
          props: tuple, freeform: bool):
    """One request with ``value`` in exactly one field; every required path parameter is otherwise ``x``."""
    url = path
    for pname in re.findall(r"{([^}:]+)(?::[^}]*)?}", path):
        v = value if (loc == "path" and pname == name) else "x"
        url = re.sub(r"{" + re.escape(pname) + r"(?::[^}]*)?}", lambda _match, v=v: quote(v, safe=""), url, count=1)
    params = {name: value} if loc == "query" else None
    kw: dict = {"params": params}
    if loc == "body" and name != "*":
        kw["json"] = {**{k: v for k, v in COMPANIONS.items() if k in props and k != name}, name: value}
    elif freeform:
        kw["json"] = {**{k: value for k in FIELDS if k not in COMPANIONS}, **COMPANIONS}
    return client.request(method, url, **kw)


def _cases():
    for method, path, fields, freeform, props in OPERATIONS:
        for loc, name in fields:
            yield pytest.param(method, path, loc, name, props, freeform, id=f"{method} {path} [{loc}:{name}]")
        if freeform:
            yield pytest.param(method, path, "body", "*", props, freeform, id=f"{method} {path} [body:*]")


@pytest.mark.parametrize("method,path,loc,name,props,freeform", list(_cases()))
def test_path_like_input_cannot_escape_the_workspace(world, method, path, loc, name, props, freeform):
    root, ws, app = world
    client = TestClient(app, raise_server_exceptions=False)
    payloads = _payloads(root)
    kinds = ("ident", "file") if name == "*" else (_kind(name),)
    problems: list[str] = []
    for kind in kinds:
        for value in payloads[kind]:
            before = _snapshot(root, ws)
            _WATCH.update(hits=[], armed=True)
            try:
                resp = _send(client, method, path, loc, name, value, props, freeform)
            finally:
                _WATCH["armed"] = False
            touched = sorted(set(_WATCH["hits"]))
            changed = _diff(before, _snapshot(root, ws))
            leaked = [s for s in (MARKER_OUTSIDE, MARKER_DOTENV, MARKER_GITCONFIG, MARKER_AUDIT)
                      if s in resp.text]
            if changed or leaked or touched:
                problems.append(f"{loc}:{name}={value!r} -> HTTP {resp.status_code}; changed={changed}; "
                                f"leaked={leaked}; touched={touched}")
    assert not problems, "\n".join(problems)


# --- the catch-all static server ----------------------------------------------------------------------

STATIC_PROBES = [
    (".git/config", MARKER_GITCONFIG), (".env", MARKER_DOTENV),
    (".pbg/ai-actions.jsonl", MARKER_AUDIT), ("%2e%2e/outside.txt", MARKER_OUTSIDE),
    ("..%2foutside.txt", MARKER_OUTSIDE), ("studies/../../outside.txt", MARKER_OUTSIDE),
]


@pytest.mark.parametrize("rel,sentinel", STATIC_PROBES, ids=[p[0] for p in STATIC_PROBES])
def test_static_server_does_not_serve_files_it_should_not(world, rel, sentinel):
    app = world[2]
    resp = TestClient(app, raise_server_exceptions=False).get("/" + rel)
    assert sentinel not in resp.text, f"GET /{rel} -> HTTP {resp.status_code} served protected content"
