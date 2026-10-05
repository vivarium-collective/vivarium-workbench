"""The chat model's action surface: the app's own live OpenAPI, no hand-kept catalog.

Three generic tools are derived at runtime from ``app.openapi()``:

* ``list_operations``   — discover operations (id, method, path, summary, tag);
* ``describe_operation`` — the resolved parameter / request-body JSON schema;
* ``call_operation``    — execute one, **in-process** through
  ``httpx.ASGITransport(app)`` so it traverses the real middleware stack
  (session → workspace routing, error envelope, read-only filtering).

Every non-GET call raises ``ApprovalRequired`` until the user approves it in the
browser; each executed non-GET call is appended to ``<ws>/.pbg/ai-actions.jsonl``.
The exclusion policy (:func:`is_excluded`) is applied once, when the index is
built, and both listing and calling resolve ids only through that index — so a
forged ``operation_id`` (the transcript lives in the browser) can't reach an
excluded route.
See ``docs/ai-chat.md``.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI
from pydantic import BeforeValidator
from pydantic_ai import ApprovalRequired, RunContext
from pydantic_ai.messages import ModelResponse, ToolCallPart

from vivarium_workbench.lib import ai_auth, ai_skills
from vivarium_workbench.lib.workspace_paths import WorkspacePaths

# Operations the model must never see or call.
#   * Auth: it would act on the user's GitHub identity. AI: it would edit its own
#     credentials.
#   * Downloads (tag, GET only): binary/HTML bodies. Binary reads tagged elsewhere
#     are excluded by path below. (Downloads-tagged POSTs, e.g. figures-build, stay.)
#   * Anything that changes what the server is bound to (workspace/source
#     switching, other servers' start/stop — the turn's workspace and audit
#     invariants assume ONE workspace, and the reads there list other workspaces'
#     paths) and anything that writes to a remote or under the user's remote
#     identity (push, PR creation): the one thing the assistant may never do.
#   * Registry/package installs (catalog-install, import-install, system-deps-install)
#     are ordinary user actions and ARE reachable — behind the approval card.
#   * The chat routes themselves (recursion) and the SSE streams (never terminate).
# Deliberately NOT excluded: local git commits and run launches — approval-gated.
# NOTE the approval rule is a *verb* test (non-GET), not a side-effect test: a GET
# handler that writes (e.g. audit-report?rerun=1) runs unapproved. See docs/ai-chat.md.
EXCLUDED_TAGS = frozenset({"Auth", "AI"})
EXCLUDED_GET_TAGS = frozenset({"Downloads"})
EXCLUDED_PATH_PREFIXES = (
    "/api/source/",
    "/api/workspaces",
    "/api/chat/",
    "/api/ai/",
    "/api/events",
)
EXCLUDED_PATHS = frozenset({
    "/api/branch/push", "/api/work-push", "/api/work-create-pr",
    # These also `git push` (work-link-branch defaults push=true; the remote-run build/start
    # pipelines push the branch to origin before building) — same rule: never.
    "/api/work-link-branch", "/api/remote-run-start", "/api/remote-run-build",
    "/api/simulation-run-download", "/api/study-analysis-zip",
    "/api/composite-run/{run_id}/download",
    # Copies any file the server can read into the workspace (where it is served back) — a user's own action.
    "/api/expert-doc",
})

MAX_RESPONSE_CHARS = 20_000
MAX_LIST_RESULTS = 40
_METHODS = ("get", "post", "put", "patch", "delete")


@dataclass
class ChatDeps:
    """Per-turn dependencies handed to every tool via ``RunContext.deps``."""

    app: FastAPI
    client: httpx.AsyncClient
    ws_root: Path
    session_key: str | None
    provider: str
    model: str
    mode: str = "agent"          # manual (no tools are registered) | ask (reads only) | agent
    local_only: bool = False     # True only for a binding the local-only Claude Code provider made (lib/chat_commands gate)
    skills: dict[str, ai_skills.Skill] = field(default_factory=dict)   # discovered SKILL.md folders, this turn


def make_client(app: FastAPI) -> httpx.AsyncClient:
    """An in-process client for ``app``. No ``Origin`` header is ever sent, so
    the CSRF guard's existing rule (absent Origin ⇒ allowed) applies. The client never leaves the
    process, so it presents a loopback ``Host`` — the DNS-rebinding guard (``lib.csrf.is_host_allowed``)
    refuses any other name on a loopback-bound server."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://127.0.0.1",
    )


def is_excluded(path: str, tags: list[str] | tuple[str, ...], method: str = "get") -> bool:
    return (
        path in EXCLUDED_PATHS
        or path.startswith(EXCLUDED_PATH_PREFIXES)
        or bool(EXCLUDED_TAGS.intersection(tags))
        or (method.lower() == "get" and bool(EXCLUDED_GET_TAGS.intersection(tags)))
    )


# ---------------------------------------------------------------------------
# Operation index (built from the LIVE app, never a fixture)
# ---------------------------------------------------------------------------


def build_index(app: FastAPI) -> dict[str, dict[str, Any]]:
    """``operationId -> {operation_id, method, path, tag, summary, mutating, op}``
    for every non-excluded operation the app currently serves."""
    index: dict[str, dict[str, Any]] = {}
    for path, item in app.openapi().get("paths", {}).items():
        for method in _METHODS:
            op = item.get(method)
            if not op:
                continue
            tags = op.get("tags") or []
            if is_excluded(path, tags, method):
                continue
            oid = op["operationId"]
            index[oid] = {
                "operation_id": oid,
                "method": method.upper(),
                "path": path,
                "tag": tags[0] if tags else None,
                "summary": op.get("summary") or "",
                "mutating": method != "get",
                "op": op,
            }
    return index


def get_index(app: FastAPI) -> dict[str, dict[str, Any]]:
    """The app's operation index, built once and cached on ``app.state``."""
    idx = getattr(app.state, "ai_index", None)
    if idx is None:
        idx = app.state.ai_index = build_index(app)
    return idx


def _deref(node: Any, schemas: dict[str, Any], seen: tuple[str, ...] = ()) -> Any:
    """Inline ``#/components/schemas/*`` refs (cycle-safe) and drop noise keys."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[1]
            if name in seen or name not in schemas:
                return {"$ref": name}
            return _deref(schemas[name], schemas, seen + (name,))
        return {k: _deref(v, schemas, seen) for k, v in node.items()
                if k not in ("title", "examples", "example")}
    if isinstance(node, list):
        return [_deref(v, schemas, seen) for v in node]
    return node


def _brief(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: entry[k] for k in ("operation_id", "method", "path", "tag", "summary", "mutating")}


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
#
# ``.pbg/ai-actions.jsonl`` is INTENT-FIRST: an ``intent`` line is written (and
# fsync'd) *before* a mutation is dispatched, a ``result`` line after. So a
# mutation that runs but whose caller is cancelled (Stop, tab close) is still on
# record, an unwritable log refuses the change instead of letting it run
# unaudited, and the intent line doubles as the single-use claim on an approval.
# The session key is a routing id that scopes hosted credentials, and this file
# is served by the workspace catch-all — so only a short hash of it is recorded.

_AUDIT_LOCK = threading.Lock()


def audit_path(ws_root: Path) -> Path:
    return WorkspacePaths.load(ws_root).pbg / "ai-actions.jsonl"


def session_tag(session: str | None) -> str:
    return hashlib.sha256((session or "").encode()).hexdigest()[:12]


def append_audit(ws_root: Path, record: dict[str, Any], *, sync: bool = True) -> None:
    """Append one JSON line, fsync'd (same durability as ``lib/event_log.append``; ``sync=False`` for the cheap
    read records). ``events.jsonl`` is not used: its schema admits only four event types."""
    path = audit_path(ws_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as f:
        f.seek(0, os.SEEK_END)
        lead = b""
        if f.tell():                         # a crash-truncated last line must not swallow this record
            f.seek(-1, os.SEEK_END)
            lead = b"" if f.read(1) == b"\n" else b"\n"
        f.write(lead + (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8"))
        f.flush()
        if sync:
            os.fsync(f.fileno())


MAX_ARGS_PREVIEW = 2_000

# --- approval-card fidelity -----------------------------------------------------------------------------
# What the user approves must be what runs. Some routes take a small request that expands into something much
# bigger on the server (shell commands from the workspace's catalog overlay, a package from a source URL, a file
# as base64), so the card carries an ``effect`` resolved by the same lookups the route uses, and a body with the
# opaque blobs summarised. Nothing here runs or writes anything.

_B64 = re.compile(r"[A-Za-z0-9+/_-]{1000,}={0,2}")      # a long opaque blob under any key, e.g. ``content`` / ``data``


def _display_body(body: Any, files: list[dict[str, Any]]) -> Any:
    """``body`` with base64 payloads replaced by their size/hash (recorded in ``files``); all other text in full."""
    if isinstance(body, dict):
        out: dict[str, Any] = {}
        for k, v in body.items():
            if isinstance(v, str) and (k.endswith("_b64") or _B64.fullmatch(v)):
                try:
                    raw = base64.b64decode(v, validate=False)
                except ValueError:
                    raw = v.encode()
                files.append({"field": k, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                out[k] = f"<{len(raw)} bytes, sha256 {files[-1]['sha256'][:12]}…>"
            else:
                out[k] = _display_body(v, files)
        return out
    if isinstance(body, list):
        return [_display_body(v, files) for v in body]
    return body            # never clipped: the card scrolls, and what is approved must be what runs


def _registry_entry(ws_root: Path, name: Any) -> dict[str, Any] | None:
    from vivarium_workbench.lib import workspace_deps_views
    name = name.strip() if isinstance(name, str) else name      # the routes strip it; so must the preview
    return next((m for m in workspace_deps_views.module_registry(ws_root) if m.get("name") == name), None)


def _effect_system_deps(ws_root: Path, body: dict[str, Any]) -> dict[str, Any] | None:
    from vivarium_workbench.lib import workspace_deps_views
    entry = _registry_entry(ws_root, body.get("name"))
    if entry is None:
        return None
    plat = workspace_deps_views.platform_key()
    checks = {c.get("name"): c for c in (entry.get("system_dependencies") or {}).get("checks") or [] if c.get("name")}
    cmds = []
    for cn in body.get("check_names") or []:
        block = (checks.get(cn) or {}).get("install")
        spec = block.get(plat) if isinstance(block, dict) else None
        cmds.append({"check": cn, "run": list((spec or {}).get("commands") or [])})
    checks_run = [{"check": cn, "import_check": (checks.get(cn) or {}).get("import_check")} for cn in body.get("check_names") or []]
    return {"summary": f"Runs these commands in a shell on the machine running the workbench ({plat}), then runs each "
                       f"check's Python import snippet in the workspace environment:",
            "commands": cmds, "import_checks": [c for c in checks_run if c["import_check"]]}


def _effect_catalog_install(ws_root: Path, body: dict[str, Any]) -> dict[str, Any] | None:
    entry = _registry_entry(ws_root, body.get("name"))
    if entry is None:
        return None
    pypi, source = entry.get("pypi_name"), entry.get("source")
    # Same rule as lib/catalog_install_views.catalog_install
    from_pypi = bool(pypi) and not (bool(body.get("full_repo")) and source)
    return {"summary": "Installs this package into the workspace environment:",
            "package": pypi, "source": source,
            "mode": "PyPI install" if from_pypi else "git submodule + editable install (runs the cloned repository's build)",
            "system_deps_check": "skipped" if body.get("skip_system_deps_check") else "required first"}


_EFFECTS = {"/api/system-deps-install": _effect_system_deps, "/api/catalog-install": _effect_catalog_install}


def approval_metadata(e: dict[str, Any], ws_root: Path, path: str, query: Any, body: Any) -> dict[str, Any]:
    """The approval-card payload for one pending change."""
    files: list[dict[str, Any]] = []
    shown = _display_body(body, files)
    effect: dict[str, Any] | None = None
    resolver = _EFFECTS.get(e["path"])
    if resolver is not None and isinstance(body, dict):
        try:
            effect = resolver(ws_root, body)
        except Exception:    # noqa: BLE001 — a preview is best effort; the card still shows the raw request
            effect = None
        if effect is None:   # say so: a blank must never read as "nothing more happens"
            effect = {"summary": "The workbench could not work out what this request will run — treat it with caution.",
                      "unresolved": True}
    if files:
        effect = {**(effect or {"summary": "Writes uploaded file content:"}), "files": files}
    meta: dict[str, Any] = {"method": e["method"], "path": path, "summary": e["summary"],
                            "query": query or {}, "body": shown}
    if effect:
        meta["effect"] = effect
    return meta


def _clip(value: Any) -> str:
    """A key-masked, size-capped JSON rendering of one argument, for the audit log."""
    text = ai_auth.mask_key(json.dumps(value, default=str, sort_keys=True))
    return text if len(text) <= MAX_ARGS_PREVIEW else f"{text[:MAX_ARGS_PREVIEW]}…(+{len(text) - MAX_ARGS_PREVIEW} chars)"


def args_record(query: Any, body: Any) -> dict[str, Any]:
    """What an approved change was asked to do: a redacted, capped preview plus the SHA-256 of the full arguments."""
    canonical = json.dumps({"query": query or {}, "body": body}, sort_keys=True, separators=(",", ":"), default=str)
    return {"args": {"query": _clip(query or {}), "body": _clip(body)},
            "args_sha256": hashlib.sha256(canonical.encode()).hexdigest()}


@dataclass
class _ClaimIndex:
    """Approval claims seen so far in one audit file, and how far into it we have read."""
    offset: int = 0
    ident: tuple = ()           # (st_dev, st_ino, first bytes): a different file at the same path is read afresh
    claimed: set = field(default_factory=set)
    results: dict = field(default_factory=dict)


_CLAIMS: dict[str, _ClaimIndex] = {}


def _read_head(path: Path, n: int = 64) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def _read_from(path: Path, offset: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read()


def _find_claim(ws_root: Path, tool_call_id: str, digest: str) -> tuple[bool, dict[str, Any] | None]:
    """``(claimed, result_record)`` for an approved call: was its intent already recorded,
    and if so what did the recorded result say?

    The log also carries a line per read, so it is indexed incrementally: each lookup parses only the bytes
    appended since the previous one (a file that shrank was replaced, so it is read afresh)."""
    path = audit_path(ws_root)
    if not path.exists():
        _CLAIMS.pop(str(path), None)
        return False, None
    st = path.stat()
    ident = (st.st_dev, st.st_ino, _read_head(path))
    idx = _CLAIMS.setdefault(str(path), _ClaimIndex(ident=ident))
    if st.st_size < idx.offset or idx.ident != ident:
        idx = _CLAIMS[str(path)] = _ClaimIndex(ident=ident)
    data = _read_from(path, idx.offset)
    complete = data[:data.rfind(b"\n") + 1]            # a half-written last line is picked up next time
    for line in complete.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        key = (r.get("tool_call_id"), r.get("digest"))
        if r.get("phase") == "intent":
            idx.claimed.add(key)
        elif r.get("phase") == "result":
            idx.results[key] = r
    idx.offset += len(complete)
    key = (tool_call_id, digest)
    return key in idx.claimed, idx.results.get(key)


def _refusal(tool_call_id: str, result: dict[str, Any] | None) -> dict[str, Any]:
    """Tell the model what actually happened to the earlier execution — it may be retrying
    because it never saw the outcome."""
    if result is None:
        what = "no result was recorded (it may have been interrupted) — check with a read before assuming it did or didn't apply"
    elif result.get("status") is None:
        what = f"it was {result.get('outcome', 'interrupted')} and its outcome is unknown — check with a read"
    else:
        what = f"it finished with status {result['status']}"
    return {"error": f"this approved call was already executed (tool_call_id {tool_call_id}); {what}; not running it again"}


def _call_epoch(ctx: RunContext[ChatDeps]) -> str:
    """Timestamp of the model response that issued this tool call. Folded into the
    claim digest so a provider that REUSES tool_call_ids (some local servers emit
    ``call_0`` every turn) can't have a later, legitimately approved identical call
    refused as a replay — while replaying the same transcript (same response, same
    timestamp) is still refused."""
    for m in reversed(ctx.messages or []):
        if isinstance(m, ModelResponse) and any(
                isinstance(p, ToolCallPart) and p.tool_call_id == ctx.tool_call_id for p in m.parts):
            return m.timestamp.isoformat()
    return ""


def _call_digest(operation_id: str, path: str, query: Any, body: Any, epoch: str = "") -> str:
    blob = json.dumps({"op": operation_id, "path": path, "query": query or {}, "body": body,
                       "epoch": epoch}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def list_operations(ctx: RunContext[ChatDeps], tag: str | None = None,
                          query: str | None = None) -> dict[str, Any]:
    """List workbench API operations you may call. Filter by ``tag`` (exact) and/or
    ``query`` (words that must all appear in the id, path or summary). Read
    (GET) operations run immediately; every other method needs the user's
    approval. Results are capped — narrow with ``query`` if ``truncated``."""
    return list_ops(ctx.deps, tag, query)


def list_ops(deps: ChatDeps, tag: str | None, query: str | None) -> dict[str, Any]:
    words = (query or "").lower().split()
    hits = []
    for e in get_index(deps.app).values():
        if tag and e["tag"] != tag:
            continue
        if e["mutating"] and deps.mode != "agent":
            continue                      # Ask mode: the model never even sees the write operations
        hay = f'{e["operation_id"]} {e["path"]} {e["summary"]}'.lower()
        if all(w in hay for w in words):
            hits.append(_brief(e))
    return {"total": len(hits), "truncated": len(hits) > MAX_LIST_RESULTS,
            "operations": hits[:MAX_LIST_RESULTS]}


async def describe_operation(ctx: RunContext[ChatDeps], operation_id: str) -> dict[str, Any]:
    """Describe one operation: its parameters and request-body JSON schema. Call
    this before ``call_operation`` on anything you have not used yet."""
    return describe_op(ctx.deps, operation_id)


def describe_op(deps: ChatDeps, operation_id: str) -> dict[str, Any]:
    e = get_index(deps.app).get(operation_id)
    if e is None:
        return {"error": f"unknown operation '{operation_id}' — use list_operations"}
    schemas = deps.app.openapi().get("components", {}).get("schemas", {})
    op = e["op"]
    body = (op.get("requestBody", {}).get("content", {})
            .get("application/json", {}).get("schema"))
    return {
        **_brief(e),
        "description": (op.get("description") or "")[:1500],
        "parameters": [_deref(p, schemas) for p in op.get("parameters", [])],
        "request_body": _deref(body, schemas) if body else None,
    }


def _resolve_path(template: str, path_params: dict[str, Any] | None) -> str | dict[str, str]:
    """Fill ``{name}`` segments (URL-quoted, ``{x:path}`` converters allowed)."""
    out = template
    for seg in [s for s in template.split("/") if s.startswith("{")]:
        name = seg.strip("{}").split(":")[0]
        if not path_params or name not in path_params:
            return {"error": f"missing path parameter '{name}'"}
        value = str(path_params[name])
        # The exclusion policy judges the *template*, so the routed path must not differ from it: no empty value, no
        # NUL / backslash, and no ``.`` / ``..`` segment (a value may still contain ``/``, which is percent-encoded
        # and so stays inside one segment — ``{x:path}`` routes such as composite-state/{ref} rely on that).
        if not value or "\x00" in value or "\\" in value or any(p in (".", "..") for p in value.split("/")):
            return {"error": f"invalid path parameter '{name}': it must be non-empty and contain no '.' or '..' "
                             f"segment, backslash or NUL"}
        out = out.replace(seg, quote(value, safe="" if seg == f"{{{name}}}" else "/"))
    return out


_TOKEN = re.compile(r"\[(-?\d*)(:)?(-?\d*)\]|([^.\[\]]+)")


def select_path(obj: Any, path: str | None) -> Any:
    """A tiny path into a JSON value: dotted keys, ``[i]`` index, ``[a:b]`` slice, bare integer
    segments for lists (``a.b[1].c``, ``processes[0:25]``, ``rows.2``). ``ValueError`` names
    the keys/length that were available, so a model can correct itself."""
    cur = obj
    text, pos = (path or "").strip(), 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            raise ValueError(f"cannot parse select at position {pos}: {text[pos:pos + 12]!r}")
        pos = m.end()
        if pos < len(text) and text[pos] == ".":
            pos += 1
            if pos == len(text):
                raise ValueError("select ends with '.'")
        a, colon, b, key = m.groups()
        if key is not None:
            if isinstance(cur, dict):
                if key not in cur:
                    raise ValueError(f"no key '{key}' (keys: {', '.join(list(cur)[:40])})")
                cur = cur[key]
            elif isinstance(cur, list):
                try:
                    cur = cur[int(key)]
                except (ValueError, IndexError):
                    raise ValueError(f"'{key}' is not a valid list index (length {len(cur)})") from None
            else:
                raise ValueError(f"'{key}': the value here is not an object or list")
        elif not isinstance(cur, list):
            raise ValueError("[...] needs a list, but the value here is not a list")
        elif colon:
            cur = cur[int(a) if a else None: int(b) if b else None]
        else:
            try:
                cur = cur[int(a)]
            except (ValueError, IndexError):
                raise ValueError(f"list index {a or '?'} out of range (length {len(cur)})") from None
    return cur


def _describe(v: Any) -> str:
    if isinstance(v, list):
        return f"list[{len(v)}]"
    if isinstance(v, dict):
        return f"object[{len(v)} keys]"
    return "null" if v is None else "boolean" if isinstance(v, bool) else \
        "number" if isinstance(v, (int, float)) else "string"


def _shape_of(body: Any) -> dict[str, str]:
    if isinstance(body, dict):
        return {k: _describe(v) for k, v in list(body.items())[:60]}
    return {"(root)": _describe(body)}


def _shape(resp: httpx.Response, select: str | None = None) -> dict[str, Any]:
    ctype = resp.headers.get("content-type", "")
    out: dict[str, Any] = {"status": resp.status_code}
    if "json" in ctype:
        try:
            data = resp.json()
        except ValueError:
            data = None
        else:
            if select and resp.is_success:      # an error body is the message the model needs — never hide it
                try:
                    data = select_path(data, select)
                except ValueError as e:
                    return {**out, "error": f"select failed: {e}"}
            text = json.dumps(data, separators=(",", ":"), default=str)
            if len(text) <= MAX_RESPONSE_CHARS:
                return {**out, "body": data}
            # Too big to show: give a map of it and how to read a part, not a blind cut.
            return {**out, "truncated": True, "chars": len(text), "shape": _shape_of(data),
                    "body_preview": text[:2000],
                    "hint": f"{len(text)} chars is too large to return. Re-call with select='<key>[0:20]' "
                            "(dotted keys, [i] index, [a:b] slice; keys are in `shape`) to read one part."}
        return {**out, "body": resp.text[:MAX_RESPONSE_CHARS]}
    out.update(content_type=ctype, bytes=len(resp.content))
    if ctype.startswith("text/"):
        out["text_preview"] = resp.text[:2000]
    return out


def _decode_json_object(v: Any) -> Any:
    """Local models (e.g. qwen on Ollama) often send an object argument as a JSON *string*. Decode one that is a
    JSON object; leave anything else for validation to reject with an error the model can act on."""
    if isinstance(v, str) and v.strip().startswith("{"):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


# Every request body in the workbench API is a JSON object (or omitted), and so are query and path params.
# Typing them so (rather than `Any`) tells the model the shape, and the decoder means a stringified object reaches
# the approval card and the server as the object it encodes — not a string the server rejects with a 422.
JsonObject = Annotated[dict[str, Any] | None, BeforeValidator(_decode_json_object)]


async def call_operation(ctx: RunContext[ChatDeps], operation_id: str,
                         path_params: JsonObject = None,
                         query: JsonObject = None,
                         body: JsonObject = None, select: str | None = None) -> dict[str, Any]:
    """Call one workbench API operation. GET runs immediately; every other
    method pauses until the user approves it (you'll be told if they decline).
    Pass ``path_params``, ``query`` and ``body`` exactly as ``describe_operation``
    specifies, each as a JSON object (not a string). Check the returned ``status`` — a failed
    operation can still come back as a normal result. A response over ~20k chars comes
    back as a ``shape`` (its keys and sizes) instead of the data: re-call with ``select``
    (a path like ``processes[0:25]`` or ``types.0``) to read just that part."""
    deps = ctx.deps
    prepared = prepare_call(deps, operation_id, path_params)
    if isinstance(prepared, dict):
        return prepared
    e, path = prepared
    if e["mutating"] and not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={"operation_id": operation_id,
                                         **approval_metadata(e, deps.ws_root, path, query, body)})
    return await execute_call(deps, e, path, query, body, select,
                              tool_call_id=ctx.tool_call_id or "", epoch=_call_epoch(ctx))


def prepare_call(deps: ChatDeps, operation_id: str,
                 path_params: dict[str, Any] | None) -> tuple[dict[str, Any], str] | dict[str, Any]:
    """Validate one call up to (not including) approval: ``(index entry, resolved path)``, or the error
    result the model should see. Shared by every front end of these tools (pydantic-ai's ``call_operation``
    and the Claude Code MCP server), so the exclusion list, the path rules and the Ask-mode refusal live once."""
    e = get_index(deps.app).get(operation_id)
    if e is None:
        return {"error": f"unknown operation '{operation_id}' — use list_operations"}
    path = _resolve_path(e["path"], path_params)
    if isinstance(path, dict):
        return path
    if e["mutating"] and deps.mode != "agent":
        return {"error": "read-only mode (Ask): changes are disabled — ask the user to switch the "
                         "mode to Agent if they want you to change the workspace"}
    return e, path


async def execute_call(deps: ChatDeps, e: dict[str, Any], path: str, query: Any, body: Any,
                       select: str | None, *, tool_call_id: str, epoch: str) -> dict[str, Any]:
    """Run an already prepared — and, if it mutates, already APPROVED — call: claim the approval, record
    intent, dispatch in-process, record the result. The caller is responsible for the approval itself."""
    operation_id = e["operation_id"]
    headers = {"X-VW-Session": deps.session_key} if deps.session_key else {}
    base = {"session": session_tag(deps.session_key), "provider": deps.provider, "model": deps.model,
            "tool_call_id": tool_call_id, "operation_id": operation_id,
            "method": e["method"], "path": path, "approved": True,
            "digest": _call_digest(operation_id, path, query, body, epoch)}
    if e["mutating"]:
        # Claim the approval (single use) and record intent BEFORE dispatching.
        with _AUDIT_LOCK:
            claimed, prior = _find_claim(deps.ws_root, base["tool_call_id"], base["digest"])
            if claimed:
                return _refusal(base["tool_call_id"], prior)
            try:
                append_audit(deps.ws_root, {**base, **args_record(query, body), "phase": "intent",
                                            "ts": datetime.now(timezone.utc).isoformat()})
            except OSError as exc:
                return {"error": f"audit log unavailable ({exc}); refusing to run an unaudited change"}
    warning = None

    def record_result(status: int | None, outcome: str) -> None:
        nonlocal warning
        try:
            append_audit(deps.ws_root, {**base, "phase": "result", "status": status, "outcome": outcome,
                                        "ts": datetime.now(timezone.utc).isoformat()})
        except OSError as exc:
            warning = f"could not record the result in the audit log: {exc}"

    try:
        resp = await deps.client.request(
            e["method"], path, params=query or None,
            json=body if body is not None else None, headers=headers)
    except BaseException:                    # incl. CancelledError: the handler may still complete
        if e["mutating"]:
            record_result(None, "interrupted")
        raise
    if e["mutating"]:
        record_result(resp.status_code, "completed")
    else:                                     # reads are logged by target only, best effort: never fail a read
        with contextlib.suppress(OSError):
            append_audit(deps.ws_root, {"session": base["session"], "tool_call_id": base["tool_call_id"],
                                        "operation_id": operation_id, "method": e["method"], "path": path,
                                        "status": resp.status_code, "phase": "read",
                                        "ts": datetime.now(timezone.utc).isoformat()}, sync=False)
    out = _shape(resp, select)
    if warning:
        out["audit_warning"] = warning
    return out


MAX_WAIT_S = 30


async def wait_seconds(ctx: RunContext[ChatDeps], seconds: float) -> dict[str, Any]:
    """Pause for ``seconds`` (at most 30). Use it between polls of a running job instead of
    calling the status endpoint back-to-back."""
    return await pause(seconds)


async def pause(seconds: float) -> dict[str, Any]:
    s = max(0.0, min(float(seconds), MAX_WAIT_S))
    await asyncio.sleep(s)
    return {"waited": s}


def capabilities(app: FastAPI) -> dict[str, Any]:
    """Counts of what the model can reach, from the live index (for the Capabilities popover)."""
    idx = get_index(app)
    return {
        "reads": sum(1 for e in idx.values() if not e["mutating"]),
        "writes": sum(1 for e in idx.values() if e["mutating"]),
        "excluded": [*(f"tag: {t}" for t in sorted(EXCLUDED_TAGS)),
                     *(f"tag: {t} (reads)" for t in sorted(EXCLUDED_GET_TAGS)),
                     *sorted(EXCLUDED_PATH_PREFIXES), *sorted(EXCLUDED_PATHS)],
    }


async def list_skills(ctx: RunContext[ChatDeps]) -> dict[str, Any]:
    """List the skills you can load: reusable step-by-step instructions (name, what it is for, and
    what it needs beyond the workbench API)."""
    return {"skills": ai_skills.summary(ctx.deps.skills)}


async def load_skill(ctx: RunContext[ChatDeps], name: str) -> dict[str, Any]:
    """Load one skill's full instructions by name (see list_skills), then follow them with your
    tools. Load a skill before acting on a request it covers."""
    skill = ctx.deps.skills.get(name)
    if skill is None:
        return {"error": f"unknown skill '{name}'", "available": sorted(ctx.deps.skills)}
    try:
        text = ai_skills.read_skill(skill)
    except (OSError, UnicodeDecodeError) as e:
        return {"error": f"could not read skill '{name}': {e.__class__.__name__}"}
    return {"skill": skill.name, "needs": ai_skills.needs_label(skill),
            "how_to_follow": ai_skills.HOW_TO_FOLLOW, "instructions": text}


TOOLS = [list_operations, describe_operation, call_operation, wait_seconds]
SKILL_TOOLS = [list_skills, load_skill]      # registered only when the workspace/user has skills
