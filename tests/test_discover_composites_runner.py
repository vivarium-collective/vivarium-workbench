"""Unit tests for ``vivarium_dashboard.lib.discover_composites_runner``
and the dashboard-side subprocess wrapper / cache in ``composite_lookup``.

The runner runs *inside the workspace's venv* in production; these
tests exercise it directly (no subprocess) so they verify the
contract without needing a real workspace setup.  The subprocess
layer is tested via ``unittest.mock.patch`` over ``subprocess.run``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# discover() — workspace-venv-side discovery function
# ---------------------------------------------------------------------------


class TestDiscoverFunction:
    def _make_ws(self, tmp_path: Path, *, with_pyproject: bool = True) -> tuple[Path, str]:
        ws = tmp_path / "ws"
        ws.mkdir()
        if with_pyproject:
            (ws / "pyproject.toml").write_text(
                '[project]\nname = "testpkg"\n'
                'dependencies = ["bigraph-schema"]\n'
            )
        pkg_root = ws / "testpkg"
        pkg_root.mkdir()
        (pkg_root / "__init__.py").write_text("")
        comps = pkg_root / "composites"
        comps.mkdir()
        (comps / "__init__.py").write_text("")
        (ws / "workspace.yaml").write_text("name: test\npackage_path: testpkg\n")
        return ws, "testpkg"

    def test_empty_workspace_returns_ok_with_zero_composites(self, tmp_path):
        from vivarium_dashboard.lib.discover_composites_runner import discover
        ws, pkg = self._make_ws(tmp_path)
        response = discover(ws, pkg, [])
        assert response["ok"] is True
        assert response["composites"] == []

    def test_file_spec_composite_surfaces_with_kind_spec(self, tmp_path):
        """A ``.composite.json`` file in the workspace should appear with
        kind=spec, no parameters, and a derived module path."""
        from vivarium_dashboard.lib.discover_composites_runner import discover
        ws, pkg = self._make_ws(tmp_path)
        (ws / pkg / "composites" / "demo.composite.json").write_text(json.dumps({
            "name": "demo",
            "description": "test composite",
            "state": {"x": {"_type": "integer", "default": 1}},
        }))
        response = discover(ws, pkg, [])
        assert response["ok"] is True
        specs = [c for c in response["composites"] if c.get("kind") == "spec"]
        assert any(c["id"] == "testpkg.composites.demo" for c in specs), \
            f"expected testpkg.composites.demo; got: {[c['id'] for c in specs]}"

    def test_discover_handles_missing_pbg_superpowers_gracefully(self, tmp_path, monkeypatch):
        """If pbg-superpowers isn't importable in the workspace venv,
        the runner should still surface file-spec composites and report
        a structured error in the response."""
        from vivarium_dashboard.lib import discover_composites_runner
        ws, pkg = self._make_ws(tmp_path)
        (ws / pkg / "composites" / "demo.composite.json").write_text(json.dumps({
            "name": "demo", "state": {}
        }))

        # Mock import to fail
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if "pbg_superpowers" in name:
                raise ImportError("simulated missing")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        response = discover_composites_runner.discover(ws, pkg, [])
        # Failure surfaces as ok:false BUT file-spec composites are
        # still surfaced (defence in depth — partial result > no result).
        assert response["ok"] is False
        assert "pbg_superpowers" in response["error"]
        assert len(response["composites"]) >= 1  # the demo spec

    def test_runner_main_writes_atomic_response(self, tmp_path):
        """``main()`` should write a JSON response file at the requested
        path and return 0 even on a failing discover() (so the caller
        can read the response file rather than parse exit codes)."""
        from vivarium_dashboard.lib import discover_composites_runner
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "workspace.yaml").write_text("name: t\npackage_path: tp\n")
        scratch = tmp_path / "out.resp.json"

        rc = discover_composites_runner.main([
            "--workspace", str(ws),
            "--pkg", "tp",
            "--response", str(scratch),
        ])
        assert rc == 0
        assert scratch.is_file()
        payload = json.loads(scratch.read_text())
        assert "ok" in payload  # may be True or False — both are controlled
        assert "composites" in payload

    def test_runner_main_handles_render_exception_with_traceback(self, tmp_path, monkeypatch):
        """If discover() itself raises (uncontrolled), main() must still
        write a response file with ok:false + traceback and exit 0."""
        from vivarium_dashboard.lib import discover_composites_runner

        def boom(*a, **kw):
            raise RuntimeError("simulated discover crash")

        monkeypatch.setattr(discover_composites_runner, "discover", boom)
        scratch = tmp_path / "out.resp.json"
        rc = discover_composites_runner.main([
            "--workspace", str(tmp_path),
            "--pkg", "x",
            "--response", str(scratch),
        ])
        assert rc == 0
        payload = json.loads(scratch.read_text())
        assert payload["ok"] is False
        assert "RuntimeError" in payload["error"]
        assert "simulated discover crash" in payload["error"]
        assert "traceback" in payload


# ---------------------------------------------------------------------------
# composite_lookup — TTL cache + mtime invalidation
# ---------------------------------------------------------------------------


class TestCompositesCache:
    def setup_method(self):
        # Each test starts with a clean cache so prior runs don't leak.
        from vivarium_dashboard.lib.composite_lookup import _COMPOSITES_CACHE
        _COMPOSITES_CACHE.clear()

    def _make_ws(self, tmp_path: Path) -> Path:
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "pyproject.toml").write_text('[project]\nname = "x"\n')
        return ws

    def test_cache_get_returns_none_when_empty(self, tmp_path):
        from vivarium_dashboard.lib.composite_lookup import _composites_cache_get
        ws = self._make_ws(tmp_path)
        assert _composites_cache_get(ws, "x") is None

    def test_cache_put_then_get_returns_data(self, tmp_path):
        from vivarium_dashboard.lib.composite_lookup import (
            _composites_cache_get, _composites_cache_put,
        )
        ws = self._make_ws(tmp_path)
        data = {"x.composites.foo": {"id": "x.composites.foo", "kind": "spec"}}
        _composites_cache_put(ws, "x", data)
        assert _composites_cache_get(ws, "x") == data

    def test_cache_invalidates_on_package_path_change(self, tmp_path):
        """Switching the workspace's package_path shouldn't return stale
        data from a different package."""
        from vivarium_dashboard.lib.composite_lookup import (
            _composites_cache_get, _composites_cache_put,
        )
        ws = self._make_ws(tmp_path)
        _composites_cache_put(ws, "old_pkg", {"a": {"id": "a"}})
        assert _composites_cache_get(ws, "new_pkg") is None
        assert _composites_cache_get(ws, "old_pkg") == {"a": {"id": "a"}}

    def test_cache_expires_after_ttl(self, tmp_path, monkeypatch):
        from vivarium_dashboard.lib import composite_lookup
        ws = self._make_ws(tmp_path)
        composite_lookup._composites_cache_put(ws, "x", {"a": {"id": "a"}})
        # Move the clock forward past the TTL
        original = composite_lookup._COMPOSITES_CACHE_TTL_SEC
        monkeypatch.setattr(composite_lookup, "_COMPOSITES_CACHE_TTL_SEC", 0.0)
        try:
            time.sleep(0.01)
            assert composite_lookup._composites_cache_get(ws, "x") is None
        finally:
            monkeypatch.setattr(composite_lookup, "_COMPOSITES_CACHE_TTL_SEC", original)

    def test_cache_invalidates_when_pyproject_mtime_advances(self, tmp_path):
        from vivarium_dashboard.lib.composite_lookup import (
            _composites_cache_get, _composites_cache_put,
        )
        ws = self._make_ws(tmp_path)
        _composites_cache_put(ws, "x", {"a": {"id": "a"}})
        # Touch pyproject so its mtime is later than the cached value
        time.sleep(0.05)
        (ws / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1"\n')
        assert _composites_cache_get(ws, "x") is None


# ---------------------------------------------------------------------------
# discover_via_workspace_subprocess — subprocess error surfacing
# ---------------------------------------------------------------------------


class TestSubprocessWrapper:
    def setup_method(self):
        from vivarium_dashboard.lib.composite_lookup import _COMPOSITES_CACHE
        _COMPOSITES_CACHE.clear()

    def test_missing_response_file_raises_discovery_error(self, tmp_path, monkeypatch):
        """If subprocess.run completes but doesn't write the response
        file, we raise CompositeDiscoveryError with stderr context."""
        from vivarium_dashboard.lib import composite_lookup

        ws = tmp_path / "ws"
        ws.mkdir()

        proc_result = MagicMock(returncode=1, stdout="", stderr="boom")
        with patch("subprocess.run", return_value=proc_result):
            with pytest.raises(
                composite_lookup.CompositeDiscoveryError,
                match="no response file",
            ):
                composite_lookup.discover_via_workspace_subprocess(
                    ws, "x", timeout_s=5,
                )

    def test_response_file_with_ok_false_raises_with_message(self, tmp_path):
        """When the runner writes ok:false, the wrapper surfaces the
        ``error`` field verbatim."""
        from vivarium_dashboard.lib import composite_lookup

        ws = tmp_path / "ws"
        ws.mkdir()
        scratch = ws / ".pbg" / "discover-composites"
        scratch.mkdir(parents=True)

        # We need to capture the response_path the wrapper synthesises;
        # the easiest seam is a fake subprocess.run that writes the
        # response file to the path the wrapper passes via argv.
        def fake_run(cmd, **kwargs):
            # cmd ends with [..., "--response", "<path>"]
            resp_idx = cmd.index("--response") + 1
            resp_path = Path(cmd[resp_idx])
            resp_path.parent.mkdir(parents=True, exist_ok=True)
            resp_path.write_text(json.dumps({
                "ok": False,
                "error": "simulated workspace failure",
                "composites": [],
            }))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(
                composite_lookup.CompositeDiscoveryError,
                match="simulated workspace failure",
            ):
                composite_lookup.discover_via_workspace_subprocess(
                    ws, "x", timeout_s=5,
                )

    def test_successful_response_returns_generator_entries_only(self, tmp_path):
        """The wrapper filters response entries to kind=generator only
        (the caller already has the file-spec composites from its own
        in-process scan)."""
        from vivarium_dashboard.lib import composite_lookup

        ws = tmp_path / "ws"
        ws.mkdir()

        def fake_run(cmd, **kwargs):
            resp_idx = cmd.index("--response") + 1
            resp_path = Path(cmd[resp_idx])
            resp_path.parent.mkdir(parents=True, exist_ok=True)
            resp_path.write_text(json.dumps({
                "ok": True,
                "composites": [
                    {"id": "x.composites.foo", "kind": "spec"},
                    {"id": "x.composites.bar.bar", "kind": "generator",
                     "parameters": {"seed": {"type": "integer", "default": 0}}},
                ],
            }))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            entries = composite_lookup.discover_via_workspace_subprocess(
                ws, "x", timeout_s=5,
            )
        assert len(entries) == 1
        assert entries[0]["id"] == "x.composites.bar.bar"
        assert entries[0]["parameters"]["seed"]["default"] == 0

    def test_timeout_raises_discovery_error(self, tmp_path):
        """subprocess.TimeoutExpired surfaces as CompositeDiscoveryError."""
        import subprocess
        from vivarium_dashboard.lib import composite_lookup

        ws = tmp_path / "ws"
        ws.mkdir()

        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
            with pytest.raises(
                composite_lookup.CompositeDiscoveryError,
                match="timed out",
            ):
                composite_lookup.discover_via_workspace_subprocess(
                    ws, "x", timeout_s=1,
                )
