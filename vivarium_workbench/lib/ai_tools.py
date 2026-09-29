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

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI
from pydantic_ai import ApprovalRequired, RunContext
from pydantic_ai.messages import ModelResponse, ToolCallPart

from vivarium_workbench.lib.workspace_paths import WorkspacePaths

# Operations the model must never see or call.
#   * Auth: it would act on the user's GitHub identity. AI: it would edit its own
#     credentials.
#   * Downloads (tag, GET only): binary/HTML bodies. Binary reads tagged elsewhere
#     are excluded by path below. (Downloads-tagged POSTs, e.g. figures-build, stay.)
#   * Anything that changes what the server is bound to (workspace/source
#     switching, other servers' start/stop), writes to a remote or under the
#     user's remote identity (push, PR creation), or installs/uninstalls software
#     on the host (arbitrary code execution) — approval on a card is not enough
#     for those.
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
    "/api/catalog-install", "/api/catalog-uninstall", "/api/import-install",
    "/api/system-deps-install",
    "/api/simulation-run-download", "/api/study-analysis-zip",
    "/api/composite-run/{run_id}/download",
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


def make_client(app: FastAPI) -> httpx.AsyncClient:
    """An in-process client for ``app``. No ``Origin`` header is ever sent, so
    the CSRF guard's existing rule (absent Origin ⇒ allowed) applies."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://chat.internal",
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


def append_audit(ws_root: Path, record: dict[str, Any]) -> None:
    """Append one JSON line, fsync'd (same durability as ``lib/event_log.append``).
    ``events.jsonl`` is not used: its schema admits only four event types."""
    path = audit_path(ws_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _already_executed(ws_root: Path, tool_call_id: str, digest: str) -> bool:
    path = audit_path(ws_root)
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("phase") == "intent" and r.get("tool_call_id") == tool_call_id and r.get("digest") == digest:
            return True
    return False


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
    words = (query or "").lower().split()
    hits = []
    for e in get_index(ctx.deps.app).values():
        if tag and e["tag"] != tag:
            continue
        hay = f'{e["operation_id"]} {e["path"]} {e["summary"]}'.lower()
        if all(w in hay for w in words):
            hits.append(_brief(e))
    return {"total": len(hits), "truncated": len(hits) > MAX_LIST_RESULTS,
            "operations": hits[:MAX_LIST_RESULTS]}


async def describe_operation(ctx: RunContext[ChatDeps], operation_id: str) -> dict[str, Any]:
    """Describe one operation: its parameters and request-body JSON schema. Call
    this before ``call_operation`` on anything you have not used yet."""
    e = get_index(ctx.deps.app).get(operation_id)
    if e is None:
        return {"error": f"unknown operation '{operation_id}' — use list_operations"}
    schemas = ctx.deps.app.openapi().get("components", {}).get("schemas", {})
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
        out = out.replace(seg, quote(str(path_params[name]), safe="" if seg == f"{{{name}}}" else "/"))
    return out


def _shape(resp: httpx.Response) -> dict[str, Any]:
    ctype = resp.headers.get("content-type", "")
    if "json" in ctype:
        try:
            text = json.dumps(resp.json(), separators=(",", ":"), default=str)
        except ValueError:
            text = resp.text
        out: dict[str, Any] = {"status": resp.status_code}
        if len(text) > MAX_RESPONSE_CHARS:
            return {**out, "truncated": True, "chars": len(text),
                    "body_preview": text[:MAX_RESPONSE_CHARS]}
        return {**out, "body": json.loads(text) if text[:1] in "{[" else text}
    out = {"status": resp.status_code, "content_type": ctype, "bytes": len(resp.content)}
    if ctype.startswith("text/"):
        out["text_preview"] = resp.text[:2000]
    return out


async def call_operation(ctx: RunContext[ChatDeps], operation_id: str,
                         path_params: dict[str, Any] | None = None,
                         query: dict[str, Any] | None = None,
                         body: Any = None) -> dict[str, Any]:
    """Call one workbench API operation. GET runs immediately; every other
    method pauses until the user approves it (you'll be told if they decline).
    Pass ``path_params``, ``query`` and a JSON ``body`` exactly as
    ``describe_operation`` specifies. Check the returned ``status`` — a failed
    operation can still come back as a normal result."""
    deps = ctx.deps
    e = get_index(deps.app).get(operation_id)
    if e is None:
        return {"error": f"unknown operation '{operation_id}' — use list_operations"}
    path = _resolve_path(e["path"], path_params)
    if isinstance(path, dict):
        return path
    if e["mutating"] and not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={
            "operation_id": operation_id, "method": e["method"], "path": path,
            "summary": e["summary"], "query": query or {}, "body": body,
        })
    headers = {"X-VW-Session": deps.session_key} if deps.session_key else {}
    base = {"session": session_tag(deps.session_key), "provider": deps.provider, "model": deps.model,
            "tool_call_id": ctx.tool_call_id or "", "operation_id": operation_id,
            "method": e["method"], "path": path, "approved": True,
            "digest": _call_digest(operation_id, path, query, body, _call_epoch(ctx))}
    if e["mutating"]:
        # Claim the approval (single use) and record intent BEFORE dispatching.
        with _AUDIT_LOCK:
            if _already_executed(deps.ws_root, base["tool_call_id"], base["digest"]):
                return {"error": "this approved call was already executed "
                                 f"(tool_call_id {base['tool_call_id']}); not running it again"}
            try:
                append_audit(deps.ws_root, {**base, "phase": "intent",
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
    out = _shape(resp)
    if warning:
        out["audit_warning"] = warning
    return out


TOOLS = [list_operations, describe_operation, call_operation]
