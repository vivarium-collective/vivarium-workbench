"""A local stand-in for a viva-core backend, GENERATED from its own OpenAPI document.

The routes, the request validation and the default response bodies all come from
``tests/_contracts/viva_core_openapi_0.1.9.json`` -- the ``/viva/v1/openapi.json`` a
real deployment (viva-core 0.1.9) serves, fetched with GETs only. Nothing here
restates a field name by hand, so a client that drifts from the contract fails against
this stub exactly where it would fail against the deployment.

Strictness, deliberately beyond what pydantic enforces server-side: a request whose
object carries a property the schema does not declare is refused (HTTP 422), because
"the client sent a field the contract never heard of" is the drift to catch. A
response override (``respond``) is validated against the response schema too, so a
test cannot make the stub lie about the contract.

Requests are recorded (``calls``) so a test can assert what a dispatch WOULD send.
"""
from __future__ import annotations

import copy
import json
import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jsonschema

SPEC_PATH = Path(__file__).parent / "_contracts" / "viva_core_openapi_0.1.9.json"


def load_spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def _strictify(node: Any) -> Any:
    """Every object schema that lists ``properties`` and says nothing about
    ``additionalProperties`` now forbids unknown ones."""
    if isinstance(node, dict):
        out = {k: _strictify(v) for k, v in node.items()}
        if "properties" in out and "additionalProperties" not in out:
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_strictify(v) for v in node]
    return node


def _resolve(spec: dict, schema: dict) -> dict:
    while "$ref" in schema:
        node: Any = spec
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def example(spec: dict, schema: dict, depth: int = 0) -> Any:
    """A minimal instance of ``schema``: required fields filled, optional nullables null."""
    if depth > 6:
        return None
    schema = _resolve(spec, schema)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if _resolve(spec, o).get("type") != "null"]
            return example(spec, options[0], depth + 1) if options else None
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        out: dict = {}
        for name, sub in props.items():
            resolved = _resolve(spec, sub)
            nullable = any(_resolve(spec, o).get("type") == "null" for o in resolved.get("anyOf", []))
            if name in required or not nullable:
                out[name] = example(spec, sub, depth + 1)
            else:
                out[name] = None
        return out
    if t == "array":
        items = schema.get("items")
        return [example(spec, items, depth + 1)] if items and depth < 3 else []
    if t == "string":
        if schema.get("format") == "date-time":
            return "2026-01-01T00:00:00Z"
        return "x" * max(1, schema.get("minLength", 1))
    if t == "integer":
        return schema.get("minimum", 0)
    if t == "number":
        return float(schema.get("minimum", 0))
    if t == "boolean":
        return False
    return None


def _typed(v: str) -> Any:
    if v in ("true", "false"):
        return v == "true"
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            continue
    return v


@dataclass
class Call:
    method: str
    path: str
    query: dict
    body: Any
    operation_id: str | None
    status: int


class VivaV1Stub:
    def __init__(self, spec: dict | None = None) -> None:
        self.spec = spec or load_spec()
        self.calls: list[Call] = []
        self._overrides: dict[tuple[str, str], tuple[int, Any]] = {}
        self._routes: list[tuple[str, re.Pattern, str, dict]] = []
        for tmpl, item in self.spec["paths"].items():
            rx = re.compile("^" + re.sub(r"\{[^/]+\}", r"([^/]+)", tmpl) + "$")
            for method, op in item.items():
                self._routes.append((method.upper(), rx, tmpl, op))
        # static segments before templated ones: "/environments/resolve" must not be "/environments/{id}"
        self._routes.sort(key=lambda r: r[2].count("{"))
        self._strict_components = _strictify(self.spec["components"])
        self._server: ThreadingHTTPServer | None = None

    # -- schema helpers --------------------------------------------------
    def _validator(self, schema: dict, *, strict: bool) -> jsonschema.Draft202012Validator:
        comps = self._strict_components if strict else self.spec["components"]
        root = {"components": comps, **(_strictify(schema) if strict else schema)}
        return jsonschema.Draft202012Validator(root)

    def validate_response(self, method: str, tmpl: str, status: int, body: Any) -> None:
        resp = self.spec["paths"][tmpl][method.lower()]["responses"][str(status)]
        content = (resp.get("content") or {}).get("application/json")
        if content:
            self._validator(content["schema"], strict=False).validate(body)

    def respond(self, method: str, tmpl: str, status: int, body: Any) -> None:
        """Override one operation's answer. The body must satisfy the contract."""
        self.validate_response(method, tmpl, status, body)
        self._overrides[(method.upper(), tmpl)] = (status, body)

    # -- serving ---------------------------------------------------------
    def _match(self, method: str, path: str):
        for m, rx, tmpl, op in self._routes:
            if m == method and (hit := rx.match(path)):
                return tmpl, op
        return None, None

    def handle(self, method: str, raw_path: str, raw_body: bytes) -> tuple[int, str, bytes]:
        parts = urlsplit(raw_path)
        query = parse_qs(parts.query)
        tmpl, op = self._match(method, parts.path)
        body: Any = None
        if raw_body:
            try:
                body = json.loads(raw_body)
            except ValueError:
                body = raw_body.decode("utf-8", "replace")

        def done(status: int, payload: Any, ctype: str = "application/json") -> tuple[int, str, bytes]:
            self.calls.append(Call(method, parts.path, query, body, op and op.get("operationId"), status))
            if isinstance(payload, bytes):
                return status, ctype, payload
            if ctype != "application/json":
                return status, ctype, str(payload).encode()
            return status, ctype, json.dumps(payload).encode()

        if op is None:
            return done(404, {"detail": "Not Found"})
        declared = {p["name"]: p for p in op.get("parameters", []) if p["in"] == "query"}
        unknown = sorted(set(query) - set(declared))
        if unknown:
            return done(422, {"detail": f"undeclared query parameter(s): {unknown}"})
        for name, values in query.items():
            validator = jsonschema.Draft202012Validator(
                {"components": self.spec["components"], **declared[name]["schema"]})
            typed = [_typed(v) for v in values]
            # a query value is text on the wire: accept it typed or raw, alone or as a repeated list
            candidates = [typed, values] + ([typed[0], values[0]] if len(values) == 1 else [])
            if not any(validator.is_valid(c) for c in candidates):
                return done(422, {"detail": f"query {name}={values!r} violates its schema"})
        rb = op.get("requestBody")
        if rb:
            schema = rb["content"]["application/json"]["schema"]
            if raw_body == b"" and rb.get("required"):
                return done(422, {"detail": "request body required"})
            errors = sorted(self._validator(schema, strict=True).iter_errors(body), key=str)
            if errors:
                return done(422, {"detail": [e.message for e in errors][:5]})
        if (method, tmpl) in self._overrides:
            status, payload = self._overrides[(method, tmpl)]
            return done(status, payload)
        for status in sorted(op["responses"]):
            if status.startswith("2"):
                content = op["responses"][status].get("content") or {}
                if "application/json" in content:
                    return done(int(status), example(self.spec, content["application/json"]["schema"]))
                if "text/plain" in content:
                    return done(int(status), "line 1\nline 2\n", "text/plain")
                return done(int(status), b"", "application/octet-stream")
        return done(500, {"detail": "no 2xx response declared"})

    def start(self) -> str:
        stub = self

        class H(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                status, ctype, payload = stub.handle(self.command, self.path, self.rfile.read(n) if n else b"")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_DELETE = do_PUT = _serve

            def log_message(self, *a: Any) -> None:  # silence
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def calls_to(self, operation_id: str) -> list[Call]:
        return [c for c in self.calls if c.operation_id == operation_id]


def deep(o: Any) -> Any:
    return copy.deepcopy(o)
