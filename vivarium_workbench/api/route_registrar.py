"""Table-driven registration for the thin wrapper routes.

``api/app.py`` historically declared ~300 routes as hand-written functions. A large cohort of
them are *pure delegators*: their whole body is one call into a ``lib.*`` function plus a fixed
response shaping. This module lets those be expressed as compact declarative :class:`R` entries
and registered by :func:`register_table`, which reproduces each route's FastAPI contract exactly
— same path, method, operationId, parameters, requestBody, responses and ``response_model`` —
so the generated TypeScript client and the AI tool index are byte-identical (the
``test_openapi_structural_contract`` gate enforces this). Only the per-route *docstring*
(OpenAPI ``description``) is dropped; each entry carries a one-line ``summary`` instead.

Only routes whose body **exactly** matches one of the supported shape/adapter combinations are
table-driven, so the delegation is behavior-preserving by construction. Anything with inline
logic (a guard, a branch, bespoke argument juggling) stays hand-written in ``app.py``.

Shapes (how the lib result becomes the HTTP response):
  * ``"A"``  — ``body, status = fn(...)`` then ``return JSONResponse(status_code=status, content=body)``.
  * ``"B"``  — ``return fn(...)`` (FastAPI serializes against ``response_model``).
  * ``"C"``  — ``return response_model.model_validate(fn(...))``.

Adapters (how the route's params + workspace become the lib call):
  * ``"pos"``          — ``fn(ws, *params)``   (params in declared order).
  * ``"nows_pos"``     — ``fn(*params)``       (no workspace dependency).
  * ``"model_dump"``   — ``fn(ws, req.model_dump())``              (single body param ``req``).
  * ``"model_dump_eu"``— ``fn(ws, req.model_dump(exclude_unset=True))``.
  * ``"model_dump_none"``— ``fn(ws, req.model_dump(exclude_none=True))``.
  * ``"ws_body_or_empty"``— ``fn(ws, req or {})``  (single dict body param that
    defaults to None; a missing/empty body is coerced to ``{}``).
  * ``"body_or_empty_ws_kwarg"``— ``fn(req or {}, ws_root=ws)``  (as above, but the
    workspace is the ``ws_root=`` keyword after the body, not the leading positional).

CONTRACT — late-bind the lib delegate; never capture the callable at registration.
    Give each :class:`R` its delegate as a ``(owner, "attr")`` tuple (``owner`` a module or the
    app module), NOT a bare function object. :func:`register_table` resolves it with ``getattr``
    on *every request*, which is the whole point: the original hand-written routes looked the
    name up at call time, so (a) tests do ``monkeypatch.setattr(lib_mod, "fn", ...)`` and expect
    it to intercept, and (b) several lib delegates are themselves deferred/lazy. Capturing the
    callable once at import time silently defeats both — it was caught here only because it broke
    46 route tests. Do not "optimize" the tuple into a direct callable when adding a batch.

    Batch-adder checklist (two recurring gotchas when table-izing a route):
    - **ruff F401:** a lib fn imported into ``app.py`` by name (``from x import foo``) and moved to
      a string-table entry becomes statically-unused to ruff (it can't see the ``getattr``), so its
      import needs ``# noqa: F401`` — or import the module alias instead and store
      ``(module_alias, "foo")``. Functions already reached via a module alias don't hit this.
    - **source-scanning contract tests:** some tests statically grep ``app.py`` for the route's
      ``@app.<method>("<path>")`` decorator (e.g. ``test_play_buttons_contract``). Moving the route
      to the table removes that decorator, so grep ``tests/`` for the path literal before converting
      a route and teach any such test to also accept an ``R("<method>", "<path>", ...)`` entry.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

_EMPTY = inspect.Parameter.empty

_NEEDS_WS = {"pos", "model_dump", "model_dump_eu", "model_dump_none", "ws_body_or_empty",
             "body_or_empty_ws_kwarg"}
_MODEL_DUMP_KW: dict[str, dict[str, bool]] = {
    "model_dump": {},
    "model_dump_eu": {"exclude_unset": True},
    "model_dump_none": {"exclude_none": True},
}


@dataclass(frozen=True)
class P:
    """One route parameter, reproduced verbatim in the generated signature.

    ``annotation`` is the exact type FastAPI sees (``Optional[str]``, a pydantic body model,
    etc.). ``default`` is the parameter's default — a plain value, a FastAPI marker
    (``Query(...)`` / ``Body(...)`` / ``Path(...)``), or :data:`_EMPTY` for a required param.
    """

    name: str
    annotation: Any
    default: Any = _EMPTY


@dataclass(frozen=True)
class R:
    """A declarative thin route."""

    method: str
    path: str
    # The lib delegate. A ``(owner, "attr")`` tuple is resolved via ``getattr`` at call time
    # (``owner`` is the module or the app module), reproducing the original route's call-time
    # name lookup so ``monkeypatch.setattr(mod, "fn", ...)`` in tests still intercepts it. A
    # bare callable is used directly (late binding not needed).
    fn: Callable[..., Any] | tuple[Any, str]
    name: str  # the original endpoint __name__ — FastAPI derives the operationId from it
    shape: str  # "A" | "B" | "C"
    adapter: str = "pos"
    params: Sequence[P] = ()
    response_model: Any = None
    tags: Sequence[str] = ()
    summary: str | None = None
    status_code: int | None = None
    responses: dict[Any, Any] | None = None
    deprecated: bool | None = None
    include_in_schema: bool = True


def _make_endpoint(spec: R, get_workspace: Callable[..., Any]) -> Callable[..., Any]:
    """Build the sync endpoint callable with a dynamically-generated signature.

    The endpoint is intentionally a *sync* function: the ``lib.*`` delegates are blocking, so
    FastAPI must run them in its threadpool exactly as the original hand-written ``def`` routes
    did (an ``async`` endpoint would move them onto the event loop — a behavior change).
    """
    param_names = [p.name for p in spec.params]
    kw = _MODEL_DUMP_KW.get(spec.adapter)
    needs_ws = spec.adapter in _NEEDS_WS
    shape = spec.shape
    fnref = spec.fn
    response_model = spec.response_model

    def _resolve() -> Callable[..., Any]:
        if isinstance(fnref, tuple):
            owner, attr = fnref
            return getattr(owner, attr)
        return fnref

    def _invoke(ws: Any, bound: dict[str, Any]) -> Any:
        fn = _resolve()
        if spec.adapter in ("pos", "nows_pos"):
            args = [bound[n] for n in param_names]
            raw = fn(ws, *args) if needs_ws else fn(*args)
        elif spec.adapter == "ws_body_or_empty":
            # Single dict body param that defaults to None; the original routes
            # pass ``req or {}`` so a missing/empty body becomes an empty dict.
            req = bound[param_names[0]]
            raw = fn(ws, req or {})
        elif spec.adapter == "body_or_empty_ws_kwarg":
            # Like ws_body_or_empty but the workspace is passed as the keyword
            # ``ws_root=`` after the body: ``fn(req or {}, ws_root=ws)``.
            req = bound[param_names[0]]
            raw = fn(req or {}, ws_root=ws)
        else:  # model_dump family — single body param ``req``
            req = bound[param_names[0]]
            raw = fn(ws, req.model_dump(**(kw or {})))
        if shape == "A":
            body, status = raw
            return JSONResponse(status_code=status, content=body)
        if shape == "C":
            return response_model.model_validate(raw)
        return raw  # shape "B"

    def endpoint(**kwargs: Any) -> Any:
        ws = kwargs.pop("ws", None)
        return _invoke(ws, kwargs)

    sig_params = [
        inspect.Parameter(
            p.name,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=(p.default if p.default is not _EMPTY else _EMPTY),
            annotation=p.annotation,
        )
        for p in spec.params
    ]
    annotations: dict[str, Any] = {p.name: p.annotation for p in spec.params}
    if needs_ws:
        sig_params.append(
            inspect.Parameter(
                "ws",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=Depends(get_workspace),
                annotation=Path,
            )
        )
        annotations["ws"] = Path

    endpoint.__signature__ = inspect.Signature(sig_params)  # type: ignore[attr-defined]
    endpoint.__name__ = spec.name  # operationId source — MUST be the original fn name
    endpoint.__qualname__ = spec.name
    endpoint.__annotations__ = annotations
    endpoint.__doc__ = None
    return endpoint


def register_table(app: FastAPI, table: Sequence[R], get_workspace: Callable[..., Any]) -> None:
    """Register every :class:`R` in ``table`` on ``app`` as a flat ``APIRoute``.

    Uses ``app.router.add_api_route`` (not a decorator) so the generated endpoint's dynamic
    signature drives parameter extraction while ``response_model`` / ``status_code`` / ``tags``
    / ``responses`` are supplied explicitly from the table — FastAPI then derives the identical
    operationId from the endpoint ``__name__`` + path + method. ``operation_id`` is never passed
    explicitly (that would freeze it independently of the name trick).
    """
    for spec in table:
        endpoint = _make_endpoint(spec, get_workspace)
        kwargs: dict[str, Any] = dict(
            methods=[spec.method.upper()],
            tags=list(spec.tags),
            include_in_schema=spec.include_in_schema,
        )
        if spec.response_model is not None:
            kwargs["response_model"] = spec.response_model
        if spec.summary is not None:
            kwargs["summary"] = spec.summary
        if spec.status_code is not None:
            kwargs["status_code"] = spec.status_code
        if spec.responses is not None:
            kwargs["responses"] = spec.responses
        if spec.deprecated is not None:
            kwargs["deprecated"] = spec.deprecated
        app.router.add_api_route(spec.path, endpoint, **kwargs)
