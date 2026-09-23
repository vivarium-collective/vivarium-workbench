"""Regression for #857 — the xarray emitter import resolves against the
``viva_emitters`` package.

``vivarium_workbench.lib.emitters._run_xarray`` imports ``XArrayEmitter`` and
``view_from_emit_paths`` from ``viva_emitters.xarray_emitter``. (Before the
emitters-package rename it imported unconditionally from the old package name,
which raised ``ModuleNotFoundError`` in a viva-only venv.)

This test makes ``viva_emitters`` importable (real if installed, otherwise a
stub module tree), drives ``_run_xarray`` far enough to execute the guarded
import, and asserts the emitter import no longer raises ``ModuleNotFoundError``.
"""
from __future__ import annotations

import sys
import types


def _ensure_viva_emitters(monkeypatch):
    """Make ``viva_emitters.xarray_emitter`` importable, returning without
    change if the real package is present, otherwise injecting a minimal stub
    module tree into ``sys.modules`` (auto-removed by monkeypatch)."""
    try:  # prefer the real package when the test env provides it
        import viva_emitters.xarray_emitter  # noqa: F401
        import viva_emitters.xarray_emitter.view  # noqa: F401
        return
    except ImportError:
        pass

    pkg = types.ModuleType("viva_emitters")
    pkg.__path__ = []  # mark as a package
    xe = types.ModuleType("viva_emitters.xarray_emitter")
    xe.__path__ = []

    class XArrayEmitter:  # minimal stand-in; only needs to be importable
        pass

    xe.XArrayEmitter = XArrayEmitter
    view = types.ModuleType("viva_emitters.xarray_emitter.view")
    view.view_from_emit_paths = lambda *a, **k: {}

    monkeypatch.setitem(sys.modules, "viva_emitters", pkg)
    monkeypatch.setitem(sys.modules, "viva_emitters.xarray_emitter", xe)
    monkeypatch.setitem(sys.modules, "viva_emitters.xarray_emitter.view", view)


def test_run_xarray_import_resolves_viva_emitters(tmp_path, monkeypatch):
    from vivarium_workbench.lib import emitters

    _ensure_viva_emitters(monkeypatch)

    # core=None makes execution fail AFTER the guarded emitter import (at the
    # first ``core.register_link`` call) — we only care that the import itself
    # did not raise ModuleNotFoundError for the emitter package.
    try:
        emitters._run_xarray(
            state={},
            run_id="t",
            emit_paths=["anything"],
            out_dir=str(tmp_path),
            core=None,
            steps=[],
            progress_cb=lambda *a, **k: None,
            emitter_config={},
        )
    except ModuleNotFoundError as e:  # the bug we are guarding against
        if "viva_emitters" in str(e):
            raise AssertionError(
                f"xarray emitter import does not resolve against viva_emitters (#857): {e}"
            ) from e
        raise  # an unrelated missing module — surface it
    except Exception:
        pass  # any non-import failure past the guarded import is fine here
