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

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI
from pydantic_ai import ApprovalRequired, RunContext

from vivarium_workbench.lib.workspace_paths import WorkspacePaths

# Operations the model must never see or call. Auth: it would be acting on the
# user's GitHub identity. AI: it would be editing its own credentials. Downloads:
# binary/HTML bodies. The path rules cover: workspace/source switching and
# process start/stop (change what the server is bound to), pushing to remotes,
# the chat route itself (recursion), and the SSE streams (never terminate).
EXCLUDED_TAGS = frozenset({"Auth", "AI", "Downloads"})
EXCLUDED_PATH_PREFIXES = (
    "/api/source/",
    "/api/workspaces/",
    "/api/chat",
    "/api/ai",
    "/api/events",
)
EXCLUDED_PATHS = frozenset({"/api/branch/push", "/api/work-push"})

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


def is_excluded(path: str, tags: list[str] | tuple[str, ...]) -> bool:
    return (
        path in EXCLUDED_PATHS
        or path.startswith(EXCLUDED_PATH_PREFIXES)
        or bool(EXCLUDED_TAGS.intersection(tags))
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
            if is_excluded(path, tags):
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


def audit_path(ws_root: Path) -> Path:
    return WorkspacePaths.load(ws_root).pbg / "ai-actions.jsonl"


def append_audit(ws_root: Path, record: dict[str, Any]) -> None:
    """Append one JSON line, fsync'd (same durability as ``lib/event_log.append``).
    ``events.jsonl`` is not used: its schema admits only four event types."""
    path = audit_path(ws_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


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
    resp = await deps.client.request(
        e["method"], path, params=query or None,
        json=body if body is not None else None, headers=headers)
    if e["mutating"]:
        append_audit(deps.ws_root, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": deps.session_key, "provider": deps.provider, "model": deps.model,
            "operation_id": operation_id, "method": e["method"], "path": path,
            "status": resp.status_code, "approved": True,
        })
    return _shape(resp)


TOOLS = [list_operations, describe_operation, call_operation]
