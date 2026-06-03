"""Unit tests for ``vivarium_dashboard.lib.run_render_viz``.

These tests exercise the runner directly (no subprocess) — the
subprocess layer is the dashboard's HTTP handler's responsibility and
is tested separately.  Here we verify the producer/consumer contract:
``core_bootstrap`` resolution, fallback chains, error surfacing.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# _build_core — dotted-path resolution + fallback
# ---------------------------------------------------------------------------


class TestBuildCore:
    def test_uses_core_bootstrap_when_declared(self, monkeypatch):
        from vivarium_dashboard.lib.run_render_viz import _build_core

        fake_core = object()

        class FakeMod:
            @staticmethod
            def make_core():
                return fake_core

        def fake_import(name):
            assert name == "myws.hpc"
            return FakeMod

        monkeypatch.setattr(
            "vivarium_dashboard.lib.run_render_viz.importlib.import_module",
            fake_import,
        )
        core, source = _build_core("myws.hpc:make_core", pkg="myws")
        assert core is fake_core
        assert "core_bootstrap=myws.hpc:make_core" in source

    def test_supports_dot_separator_in_core_bootstrap(self, monkeypatch):
        """``module.function`` and ``module:function`` both work."""
        from vivarium_dashboard.lib.run_render_viz import _build_core

        fake_core = object()

        class FakeMod:
            @staticmethod
            def make_core():
                return fake_core

        monkeypatch.setattr(
            "vivarium_dashboard.lib.run_render_viz.importlib.import_module",
            lambda name: FakeMod if name == "myws.hpc" else (_ for _ in ()).throw(ImportError(name)),
        )
        core, source = _build_core("myws.hpc.make_core", pkg=None)
        assert core is fake_core
        assert "core_bootstrap=myws.hpc.make_core" in source

    def test_falls_back_to_pkg_core_build_core(self, monkeypatch):
        """No core_bootstrap → try ``{pkg}.core.build_core()``."""
        from vivarium_dashboard.lib.run_render_viz import _build_core

        fake_core = object()
        fake_core_mod = MagicMock()
        fake_core_mod.build_core.return_value = fake_core

        monkeypatch.setattr(
            "vivarium_dashboard.lib.run_render_viz.importlib.import_module",
            lambda name: fake_core_mod if name == "myws.core" else (_ for _ in ()).throw(ImportError(name)),
        )
        core, source = _build_core(None, pkg="myws")
        assert core is fake_core
        assert source == "myws.core.build_core"

    def test_raises_when_core_bootstrap_module_missing(self, monkeypatch):
        from vivarium_dashboard.lib.run_render_viz import _build_core

        def fake_import(name):
            raise ImportError(f"no module {name}")

        monkeypatch.setattr(
            "vivarium_dashboard.lib.run_render_viz.importlib.import_module",
            fake_import,
        )
        with pytest.raises(RuntimeError, match="core_bootstrap module not importable"):
            _build_core("nonexistent:fn", pkg=None)

    def test_raises_when_core_bootstrap_function_missing(self, monkeypatch):
        from vivarium_dashboard.lib.run_render_viz import _build_core

        class FakeMod:
            pass  # No `make_core` attribute

        monkeypatch.setattr(
            "vivarium_dashboard.lib.run_render_viz.importlib.import_module",
            lambda name: FakeMod,
        )
        with pytest.raises(RuntimeError, match="core_bootstrap target not found"):
            _build_core("myws.hpc:make_core", pkg=None)

    def test_raises_when_pkg_core_missing_with_informative_hint(self, monkeypatch):
        """Error message points at core_bootstrap as the recommended fix."""
        from vivarium_dashboard.lib.run_render_viz import _build_core

        def fake_import(name):
            raise ImportError(f"no module {name}")

        monkeypatch.setattr(
            "vivarium_dashboard.lib.run_render_viz.importlib.import_module",
            fake_import,
        )
        with pytest.raises(RuntimeError, match="core_bootstrap"):
            _build_core(None, pkg="myws")

    def test_raises_when_pkg_core_has_no_build_core(self, monkeypatch):
        from vivarium_dashboard.lib.run_render_viz import _build_core

        class CoreModWithoutBuildCore:
            pass  # No `build_core` attribute

        monkeypatch.setattr(
            "vivarium_dashboard.lib.run_render_viz.importlib.import_module",
            lambda name: CoreModWithoutBuildCore,
        )
        with pytest.raises(RuntimeError, match="has no build_core"):
            _build_core(None, pkg="myws")

    def test_raises_when_nothing_resolves(self):
        from vivarium_dashboard.lib.run_render_viz import _build_core

        with pytest.raises(RuntimeError, match="no core_bootstrap declared"):
            _build_core(None, pkg=None)


# ---------------------------------------------------------------------------
# render() — top-level dispatch, error surfacing
# ---------------------------------------------------------------------------


class TestRender:
    def _make_request(self, tmp_path: Path, *, spec_yaml: str, **overrides) -> dict:
        """Build a request payload + matching on-disk files under tmp_path."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "workspace.yaml").write_text("name: test\npackage_path: testpkg\n")
        inv_dir = workspace / "studies" / "test-study"
        inv_dir.mkdir(parents=True)
        spec_path = inv_dir / "study.yaml"
        spec_path.write_text(spec_yaml)
        request = {
            "request_id": "deadbeef",
            "study_name": "test-study",
            "spec_path": str(spec_path),
            "inv_dir": str(inv_dir),
            "workspace": str(workspace),
            "pkg": "testpkg",
            "core_bootstrap": None,
        }
        request.update(overrides)
        return request

    def test_empty_visualizations_returns_ok_with_zero(self, tmp_path, monkeypatch):
        """A spec with no visualizations:[] should short-circuit cleanly.

        (The HTTP handler also has a pre-flight for this; the runner's
        own short-circuit is defence in depth.)
        """
        from vivarium_dashboard.lib import run_render_viz

        request = self._make_request(
            tmp_path,
            spec_yaml=(
                "name: test-study\nschema_version: 3\n"
                "baseline:\n  - name: b\n    composite: testpkg.composites.x\n"
                "visualizations: []\n"
            ),
        )
        # No core to build because empty visualizations → render_visualizations
        # returns [] before any core use.  Mock _build_core anyway so the
        # test doesn't import a real workspace package.
        fake_core = MagicMock()
        fake_core.link_registry = {}
        monkeypatch.setattr(
            run_render_viz, "_build_core",
            lambda cb, pkg: (fake_core, "mocked"),
        )
        # Also stub process_bigraph so we don't need the real dep.
        import sys
        fake_pb = MagicMock()
        monkeypatch.setitem(sys.modules, "process_bigraph", fake_pb)

        response = run_render_viz.render(request)
        assert response["ok"] is True
        assert response["n_visualizations"] == 0
        assert response["viz_paths"] == []

    def test_core_build_failure_surfaces_as_controlled_error(self, tmp_path, monkeypatch):
        """A core_bootstrap that raises RuntimeError → ok:false + error string."""
        from vivarium_dashboard.lib import run_render_viz

        request = self._make_request(
            tmp_path,
            spec_yaml=(
                "name: test-study\nschema_version: 3\n"
                "baseline:\n  - name: b\n    composite: testpkg.composites.x\n"
                "visualizations:\n  - name: foo\n    address: local:Foo\n"
            ),
            core_bootstrap="bad.module:does_not_exist",
        )
        monkeypatch.setattr(
            run_render_viz, "_build_core",
            lambda cb, pkg: (_ for _ in ()).throw(
                RuntimeError("core_bootstrap module not importable: 'bad.module'")
            ),
        )
        response = run_render_viz.render(request)
        assert response["ok"] is False
        assert "bad.module" in response["error"]
        assert response["n_visualizations"] == 0

    def test_unexpected_core_exception_includes_type_name(self, tmp_path, monkeypatch):
        """Non-RuntimeError exceptions during core build get type-named."""
        from vivarium_dashboard.lib import run_render_viz

        request = self._make_request(
            tmp_path,
            spec_yaml=(
                "name: test-study\nschema_version: 3\n"
                "baseline:\n  - name: b\n    composite: testpkg.composites.x\n"
                "visualizations:\n  - name: foo\n    address: local:Foo\n"
            ),
        )
        monkeypatch.setattr(
            run_render_viz, "_build_core",
            lambda cb, pkg: (_ for _ in ()).throw(
                ValueError("simdata cache is stale")
            ),
        )
        response = run_render_viz.render(request)
        assert response["ok"] is False
        assert "ValueError" in response["error"]
        assert "stale" in response["error"]


# ---------------------------------------------------------------------------
# main() — argparse + file I/O wiring
# ---------------------------------------------------------------------------


class TestMain:
    def test_writes_response_file_alongside_request(self, tmp_path, monkeypatch):
        """main() should write <id>.resp.json next to <id>.req.json."""
        from vivarium_dashboard.lib import run_render_viz

        req_path = tmp_path / "abc123.req.json"
        request = {
            "request_id": "abc123",
            "study_name": "x",
            "spec_path": str(tmp_path / "spec.yaml"),
            "inv_dir": str(tmp_path),
            "workspace": str(tmp_path),
            "pkg": "x",
            "core_bootstrap": None,
        }
        req_path.write_text(json.dumps(request))

        # Stub render() so we don't reach into investigations/process_bigraph.
        monkeypatch.setattr(
            run_render_viz, "render",
            lambda r: {"ok": True, "n_visualizations": 0, "viz_paths": [], "error": None},
        )
        rc = run_render_viz.main([str(req_path)])
        assert rc == 0
        resp_path = tmp_path / "abc123.resp.json"
        assert resp_path.is_file()
        payload = json.loads(resp_path.read_text())
        assert payload["ok"] is True

    def test_missing_request_file_returns_nonzero(self, tmp_path):
        from vivarium_dashboard.lib import run_render_viz

        rc = run_render_viz.main([str(tmp_path / "missing.req.json")])
        assert rc == 2

    def test_malformed_request_file_returns_nonzero(self, tmp_path):
        from vivarium_dashboard.lib import run_render_viz

        req_path = tmp_path / "bad.req.json"
        req_path.write_text("not json {{{")
        rc = run_render_viz.main([str(req_path)])
        assert rc == 2

    def test_render_exception_still_writes_response_file_with_exit_0(
        self, tmp_path, monkeypatch,
    ):
        """If render() itself crashes, main() should still write a
        readable response file (ok:false + traceback) AND exit 0.

        Exit 0 means "subprocess produced a response file"; the caller
        reads the file to discover success vs failure.  Non-zero would
        signal an uncontrolled crash to the caller.
        """
        from vivarium_dashboard.lib import run_render_viz

        req_path = tmp_path / "boom.req.json"
        req_path.write_text(json.dumps({"request_id": "boom"}))

        def boom(_):
            raise KeyError("missing-key")

        monkeypatch.setattr(run_render_viz, "render", boom)
        rc = run_render_viz.main([str(req_path)])
        assert rc == 0  # controlled outcome
        resp_path = tmp_path / "boom.resp.json"
        assert resp_path.is_file()
        payload = json.loads(resp_path.read_text())
        assert payload["ok"] is False
        assert "KeyError" in payload["error"]
        assert "missing-key" in payload["error"]
        assert "traceback" in payload
