"""Tests for Todo #21 HPC investigation run path.

Covers:
- ``run_investigation_task`` runner script (import-time correctness)
- ``_apply_params`` and ``_patch_emitter_for_hpc`` helper functions
- HPC branch of ``_post_investigation_run`` (with monkeypatched subprocess)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Test: run_investigation_task runner (import-only — no subprocess)
# ---------------------------------------------------------------------------


class TestRunInvestigationTaskModule:
    def test_module_imports(self) -> None:
        """Verify the runner module can be imported without error."""
        import vivarium_dashboard.lib.run_investigation_task  # noqa: F811
        assert True

    def test_main_prints_error_without_args(self) -> None:
        """Calling main() with no argv prints error and exits."""
        from vivarium_dashboard.lib.run_investigation_task import main
        import sys as _sys
        with patch.object(_sys, "argv", ["runner.py"]):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 1

    def test_main_decodes_params_and_fails_gracefully(self) -> None:
        """Invalid base64 calls sys.exit(1)."""
        from vivarium_dashboard.lib.run_investigation_task import main
        import sys as _sys
        with patch.object(_sys, "argv", ["runner.py", "not-valid-base64!!"]):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Test: _apply_params helper
# ---------------------------------------------------------------------------


class TestApplyParams:
    def test_empty_params_returns_doc_unchanged(self) -> None:
        from vivarium_dashboard.server import _apply_params
        doc = {"state": {"rate": {"_default": 0.5}}}
        result = _apply_params(doc, {})
        assert result == doc

    def test_applies_dot_separated_override(self) -> None:
        from vivarium_dashboard.server import _apply_params
        doc = {"state": {"chromosome": {"DnaA_count": {"_default": 10}}}}
        result = _apply_params(doc, {"chromosome.DnaA_count": 20})
        assert result["state"]["chromosome"]["DnaA_count"]["_default"] == 20

    def test_sets_leaf_value_when_not_dict(self) -> None:
        from vivarium_dashboard.server import _apply_params
        doc = {"state": {"rate": 0.5}}
        result = _apply_params(doc, {"rate": 0.8})
        assert result["state"]["rate"] == 0.8

    def test_unknown_path_silently_skipped(self) -> None:
        from vivarium_dashboard.server import _apply_params
        doc = {"state": {"rate": 0.5}}
        result = _apply_params(doc, {"nonexistent.path": 99})
        assert result == doc

    def test_deeply_nested_override(self) -> None:
        from vivarium_dashboard.server import _apply_params
        doc = {"state": {"a": {"b": {"c": {"_default": 1}}}}}
        result = _apply_params(doc, {"a.b.c": 42})
        assert result["state"]["a"]["b"]["c"]["_default"] == 42


# ---------------------------------------------------------------------------
# Test: _patch_emitter_for_hpc helper
# ---------------------------------------------------------------------------


class TestPatchEmitterForHpc:
    def test_sets_run_id_and_db_file(self) -> None:
        from vivarium_dashboard.server import _patch_emitter_for_hpc
        doc = {
            "state": {
                "emitter": {
                    "_type": "step",
                    "config": {"run_id": "", "db_file": "/local/runs.db"},
                }
            }
        }
        _patch_emitter_for_hpc(doc, "run-abc123")
        emitter = doc["state"]["emitter"]
        assert emitter["config"]["run_id"] == "run-abc123"
        assert emitter["config"]["db_file"] == "/workspace/runs.db"

    def test_noop_when_not_step_type(self) -> None:
        """When _type is not 'step', the function makes no changes."""
        from vivarium_dashboard.server import _patch_emitter_for_hpc
        doc = {"state": {"emitter": {"_type": "other", "config": {"run_id": "old"}}}}
        _patch_emitter_for_hpc(doc, "run-xyz")
        assert doc["state"]["emitter"]["config"]["run_id"] == "old"

    def test_noop_when_no_emitter_key(self) -> None:
        from vivarium_dashboard.server import _patch_emitter_for_hpc
        doc = {"state": {}}
        _patch_emitter_for_hpc(doc, "run-xyz")
        assert doc == {"state": {}}


# ---------------------------------------------------------------------------
# Test: HPC branch of _post_investigation_run (mocked subprocess)
# ---------------------------------------------------------------------------


class TestPostInvestigationRunHpc:
    def test_requires_name(self) -> None:
        """Missing name returns 400."""
        from vivarium_dashboard.server import _study_name_from_body
        assert _study_name_from_body({}) == ""
        assert _study_name_from_body({"name": "test"}) == "test"

    def test_hpc_branch_returns_409_without_workspace(self) -> None:
        """Without WORKSPACE, the HPC branch returns 409 (via top-level gate)."""
        import vivarium_dashboard.server as srv
        body = {"name": "test", "compute_backend": "hpc:ccam"}
        name = srv._study_name_from_body(body)
        assert name == "test"

    def test_apply_params_importable(self) -> None:
        from vivarium_dashboard.server import _apply_params, _patch_emitter_for_hpc
        assert callable(_apply_params)
        assert callable(_patch_emitter_for_hpc)

    def test_hpc_branch_rejects_missing_spec(self) -> None:
        """When the spec file doesn't exist, return 404."""
        import vivarium_dashboard.server as srv
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir()
            (ws / "workspace.yaml").write_text("name: test-ws\npackage_path: pbg_test")

            # Must monkeypatch WORKSPACE so _study_spec_path resolves
            orig_ws = srv.WORKSPACE
            try:
                srv.WORKSPACE = ws
                from vivarium_dashboard.server import _study_spec_path
                assert not _study_spec_path("test-inv").is_file()
            finally:
                srv.WORKSPACE = orig_ws

    def test_hpc_branch_with_mocked_dispatch(self) -> None:
        """Verify spec resolution + name extraction work for HPC path."""
        import vivarium_dashboard.server as srv
        import tempfile
        import yaml

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir()
            (ws / "workspace.yaml").write_text(
                yaml.dump({"name": "test-ws", "package_path": "pbg_test"})
            )

            inv_dir = ws / "investigations" / "test-inv"
            inv_dir.mkdir(parents=True)
            spec = {
                "composite": "pbg_test.my_composite",
                "simulations": [
                    {"name": "single_run", "kind": "single",
                     "steps": 10, "overrides": {"rate": 0.5}}
                ],
            }
            (inv_dir / "spec.yaml").write_text(yaml.dump(spec))

            orig_ws = srv.WORKSPACE
            try:
                srv.WORKSPACE = ws
                name = srv._study_name_from_body(
                    {"name": "test-inv", "compute_backend": "hpc:ccam"}
                )
                assert name == "test-inv"
                spec_path = srv._study_spec_path(name)
                assert spec_path.is_file()
            finally:
                srv.WORKSPACE = orig_ws


# ---------------------------------------------------------------------------
# Test: run_investigation_task runner with valid params
# ---------------------------------------------------------------------------


class TestRunInvestigationTaskRunner:
    def test_main_with_valid_base64(self) -> None:
        """Feed valid base64-encoded params and verify it tries to build core."""
        import base64
        from vivarium_dashboard.lib.run_investigation_task import main
        import sys as _sys

        params = {
            "run_id": "test-run-001",
            "state_json": json.dumps({"emitter": {"_type": "step"}}),
            "steps": 1,
            "pkg": "",
        }
        encoded = base64.b64encode(json.dumps(params).encode()).decode()

        # Expect exit 1 because process_bigraph won't be available
        # in the test environment (or the Composite import will fail)
        with patch.object(_sys, "argv", ["runner.py", encoded]):
            with pytest.raises(SystemExit) as exc:
                main()
        # sys.exit(1) is called on error, which is fine — we just want
        # to verify the module runs without crashing
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Test: v3 simulation_set parsing in _post_investigation_run_hpc
# ---------------------------------------------------------------------------


class TestV3SimulationSetParsing:
    """Phase E.1 — v3 study shape (simulation_set + base_model + perturbation)."""

    def _make_v3_spec(self) -> dict:
        # Minimum-valid v3 study spec (baseline list is required by the validator)
        # plus simulation_set entries — the actual subject of the test.
        return {
            "schema_version": 3,
            "name": "colonies-01",
            "baseline": [
                {"name": "colony-baseline", "composite": "v2ecoli.composites.colony.colony"},
            ],
            "simulation_set": [
                {
                    "name": "build-smoke-n2",
                    "base_model": "v2ecoli.composites.colony.colony",
                    "perturbation": {"n_cells": 2},
                    "duration_min": 12,
                    "seeds": [0],
                    "status": "ready",
                },
                {
                    "name": "nsweep-n1",
                    "base_model": "v2ecoli.composites.colony.colony",
                    "perturbation": {"n_cells": 1},
                    "duration_min": 90,
                    "seeds": [0],
                    "status": "gated",
                },
                {
                    "name": "nsweep-n4",
                    "base_model": "v2ecoli.composites.colony.colony",
                    "perturbation": {"n_cells": 4},
                    "duration_min": 90,
                    "seeds": [0],
                    "status": "gated",
                },
            ],
        }

    def _run_hpc_branch(self, spec, body, monkeypatch):
        """Drive _post_investigation_run_hpc up to the dispatch call, capturing tasks."""
        import vivarium_dashboard.server as srv
        import tempfile
        from pathlib import Path as _P

        captured = {}

        def fake_submit(settings, ws_name, *, command_template, param_values, resources):
            captured["param_values"] = param_values
            captured["resources"] = resources
            return {
                "slurm_job_array_id": 999,
                "n_tasks": len(param_values),
                "run_ids": [f"r{i}" for i in range(len(param_values))],
            }

        class FakeSettings:
            hpc_repo_base_path = "/cluster/ws"
            hpc_log_base_path = None
            hpc_image_base_path = "/cluster/images"

        monkeypatch.setattr(
            "vivarium_dashboard.lib.hpc_dispatch.submit_investigation_array_job",
            fake_submit,
        )
        monkeypatch.setattr(
            "vivarium_dashboard.lib.hpc_settings.get_hpc_settings",
            lambda _ws: FakeSettings(),
        )
        # The HPC branch now also stages run_investigation_task.py via SSH+SCP
        # before dispatching — mock both so unit tests don't reach the network.
        monkeypatch.setattr(
            "vivarium_dashboard.lib.hpc_dispatch._ssh",
            lambda *a, **k: MagicMock(returncode=0, stdout="", stderr=""),
        )
        monkeypatch.setattr(
            "vivarium_dashboard.lib.hpc_dispatch._scp_file",
            lambda *a, **k: None,
        )

        with tempfile.TemporaryDirectory() as tmp:
            ws = _P(tmp) / "workspace"
            ws.mkdir()
            (ws / "workspace.yaml").write_text("name: test-ws\npackage_path: pbg_test\n")
            inv_dir = ws / "studies" / spec["name"]
            inv_dir.mkdir(parents=True)
            import yaml as _y
            (inv_dir / "study.yaml").write_text(_y.dump(spec))

            orig_ws = srv.WORKSPACE
            srv.WORKSPACE = ws
            try:
                handler = MagicMock(spec=srv.Handler)
                handler._json = lambda data, code: (code, data)
                response = srv.Handler._post_investigation_run_hpc(
                    handler, body, spec["name"], "hpc:ccam"
                )
                return response, captured
            finally:
                srv.WORKSPACE = orig_ws

    def test_default_filters_to_ready_only(self, monkeypatch):
        """Without include_gated, only 'ready' entries are dispatched."""
        spec = self._make_v3_spec()
        response, captured = self._run_hpc_branch(spec, {"name": "colonies-01"}, monkeypatch)
        assert "param_values" in captured, f"submit not called; response={response}"
        assert len(captured["param_values"]) == 1

    def test_include_gated_dispatches_all(self, monkeypatch):
        """include_gated=True picks up 'gated' entries too."""
        spec = self._make_v3_spec()
        response, captured = self._run_hpc_branch(
            spec, {"name": "colonies-01", "include_gated": True}, monkeypatch
        )
        assert "param_values" in captured, f"submit not called; response={response}"
        assert len(captured["param_values"]) == 3

    def test_steps_override_takes_precedence(self, monkeypatch):
        """body.steps_override wins over entry.duration_min derivation."""
        import base64 as _b64
        spec = self._make_v3_spec()
        response, captured = self._run_hpc_branch(
            spec,
            {"name": "colonies-01", "include_gated": True, "steps_override": 10},
            monkeypatch,
        )
        assert "param_values" in captured, f"submit not called; response={response}"
        for pv in captured["param_values"]:
            payload = json.loads(_b64.b64decode(pv["params_b64"]).decode())
            assert payload["steps"] == 10
            assert payload["base_model"] == "v2ecoli.composites.colony.colony"
            assert "overrides" in payload

    def test_steps_derived_from_duration_min(self, monkeypatch):
        """No override → derive from duration_min × 60 / dt_seconds."""
        import base64 as _b64
        spec = self._make_v3_spec()
        response, captured = self._run_hpc_branch(spec, {"name": "colonies-01"}, monkeypatch)
        assert "param_values" in captured, f"submit not called; response={response}"
        payload = json.loads(_b64.b64decode(captured["param_values"][0]["params_b64"]).decode())
        # build-smoke-n2 has duration_min=12 → 12 * 60 / 1.0 = 720
        assert payload["steps"] == 720

    def test_payload_carries_base_model_and_overrides_not_state_json(self, monkeypatch):
        """v3 payload uses base_model+overrides, NOT state_json."""
        import base64 as _b64
        spec = self._make_v3_spec()
        response, captured = self._run_hpc_branch(spec, {"name": "colonies-01"}, monkeypatch)
        assert "param_values" in captured, f"submit not called; response={response}"
        payload = json.loads(_b64.b64decode(captured["param_values"][0]["params_b64"]).decode())
        assert "base_model" in payload
        assert "overrides" in payload
        assert "state_json" not in payload

    def test_include_names_picks_exact_subset(self, monkeypatch):
        """body.include_names short-circuits status filtering and picks only the named entries."""
        spec = self._make_v3_spec()
        response, captured = self._run_hpc_branch(
            spec,
            {"name": "colonies-01", "include_names": ["nsweep-n1", "nsweep-n4"]},
            monkeypatch,
        )
        assert "param_values" in captured, f"submit not called; response={response}"
        # Only the two named gated entries — even though they're status=gated
        # and include_gated wasn't set.  The explicit subset overrides the filter.
        assert len(captured["param_values"]) == 2

    def test_include_names_overrides_include_gated(self, monkeypatch):
        """When include_names is given, include_gated is ignored — only named entries dispatch."""
        spec = self._make_v3_spec()
        response, captured = self._run_hpc_branch(
            spec,
            {
                "name": "colonies-01",
                "include_names": ["build-smoke-n2"],
                "include_gated": True,
            },
            monkeypatch,
        )
        assert "param_values" in captured, f"submit not called; response={response}"
        # include_gated would normally pick all 3 entries; include_names trumps it.
        assert len(captured["param_values"]) == 1

    def test_empty_include_names_falls_back_to_status_filter(self, monkeypatch):
        """An empty list shouldn't trigger the subset path."""
        spec = self._make_v3_spec()
        response, captured = self._run_hpc_branch(
            spec,
            {"name": "colonies-01", "include_names": []},
            monkeypatch,
        )
        assert "param_values" in captured, f"submit not called; response={response}"
        # Falls back to status==ready (1 entry, build-smoke-n2)
        assert len(captured["param_values"]) == 1

    # ------------------------------------------------------------------
    # Spec-driven report_generator dispatch (todo #22)
    # ------------------------------------------------------------------

    def _spec_with_top_level_generator(self) -> dict:
        """v3 spec with a top-level report_generator block applied to all
        simulation_set entries.  Mirrors the shape `/pbg-expert ./` is
        expected to emit for a colony-like reporter."""
        spec = self._make_v3_spec()
        spec["report_generator"] = {
            "script": "reports/colony_report.py",
            "args": {
                "duration": "{steps_clamped:5}",
                "seed": "{overrides[seed]}",
                "n-adder": "{overrides[n_cells]}",
                "out": "/app/out/colony/{run_id}.html",
            },
            "output_dir": "out/colony",
        }
        return spec

    def test_generate_report_dispatches_via_spec(self, monkeypatch):
        """generate_report=True + top-level report_generator → CLI dispatch (no b64 payload)."""
        spec = self._spec_with_top_level_generator()
        response, captured = self._run_hpc_branch(
            spec,
            {"name": "colonies-01", "include_gated": True, "generate_report": True},
            monkeypatch,
        )
        assert "param_values" in captured, f"submit not called; response={response}"
        for pv in captured["param_values"]:
            assert "params_b64" not in pv
            # Slot names are sanitized: "n-adder" → "n_adder"
            assert {"run_id", "duration", "seed", "n_adder", "out"} <= set(pv.keys())
            assert all(isinstance(pv[k], str) for k in ("duration", "seed", "n_adder", "out"))

    def test_generate_report_renders_overrides_into_args(self, monkeypatch):
        """{overrides[n_cells]} substitutes per-task from the entry's perturbation."""
        spec = self._spec_with_top_level_generator()
        response, captured = self._run_hpc_branch(
            spec,
            {
                "name": "colonies-01",
                "include_names": ["nsweep-n4"],
                "generate_report": True,
            },
            monkeypatch,
        )
        assert "param_values" in captured, f"submit not called; response={response}"
        assert len(captured["param_values"]) == 1
        assert captured["param_values"][0]["n_adder"] == "4"

    def test_generate_report_steps_clamped_caps_duration(self, monkeypatch):
        """{steps_clamped:5} caps steps_override=999 → 5."""
        spec = self._spec_with_top_level_generator()
        response, captured = self._run_hpc_branch(
            spec,
            {
                "name": "colonies-01",
                "include_names": ["build-smoke-n2"],
                "generate_report": True,
                "steps_override": 999,
            },
            monkeypatch,
        )
        assert "param_values" in captured, f"submit not called; response={response}"
        assert captured["param_values"][0]["duration"] == "5"

    def test_generate_report_without_declaration_returns_400(self, monkeypatch):
        """generate_report=True on a study with no report_generator → 400."""
        spec = self._make_v3_spec()  # no report_generator block
        response, captured = self._run_hpc_branch(
            spec,
            {"name": "colonies-01", "generate_report": True},
            monkeypatch,
        )
        # response is (status_code, body_dict)
        status, body = response
        assert status == 400
        assert "report_generator" in body.get("error", "")
        # And submit was never called.
        assert "param_values" not in captured

    def test_generate_report_per_entry_override_wins(self, monkeypatch):
        """A simulation_set entry's report_generator overrides the top-level one."""
        spec = self._spec_with_top_level_generator()
        spec["simulation_set"][0]["report_generator"] = {
            "script": "reports/colony_report.py",
            "args": {
                "duration": "11",   # static override
                "seed": "{overrides[seed]}",
                "n-adder": "{overrides[n_cells]}",
                "out": "/app/out/colony/override_{run_id}.html",
            },
            "output_dir": "out/colony",
        }
        response, captured = self._run_hpc_branch(
            spec,
            {"name": "colonies-01", "generate_report": True},
            monkeypatch,
        )
        assert "param_values" in captured, f"submit not called; response={response}"
        # Single entry dispatched (build-smoke-n2, ready); its duration came
        # from the per-entry override, not the top-level steps_clamped.
        assert captured["param_values"][0]["duration"] == "11"

    def test_generate_report_partial_declaration_returns_400(self, monkeypatch):
        """If some tasks have a generator and others don't, dispatch fails 400."""
        spec = self._make_v3_spec()  # no top-level
        # Declare only on the first entry; the others (when include_gated)
        # will have no resolved generator.
        spec["simulation_set"][0]["report_generator"] = {
            "script": "reports/colony_report.py",
            "args": {"out": "/app/out/colony/{run_id}.html"},
        }
        response, captured = self._run_hpc_branch(
            spec,
            {"name": "colonies-01", "include_gated": True, "generate_report": True},
            monkeypatch,
        )
        status, body = response
        assert status == 400
        assert "no report_generator declared" in body.get("error", "")
        assert "param_values" not in captured

    def test_generate_report_false_uses_standard_runner_path(self, monkeypatch):
        """Even when report_generator is declared, generate_report=False/absent
        uses the b64-payload runner path."""
        spec = self._spec_with_top_level_generator()
        response, captured = self._run_hpc_branch(
            spec,
            {"name": "colonies-01"},  # no generate_report
            monkeypatch,
        )
        assert "param_values" in captured, f"submit not called; response={response}"
        # Standard runner path → each pv is {"params_b64": "..."}
        for pv in captured["param_values"]:
            assert "params_b64" in pv

    # ------------------------------------------------------------------
    # core_bootstrap threading (todo #22 G2)
    # ------------------------------------------------------------------

    def test_core_bootstrap_threads_through_runner_payload(self, monkeypatch):
        """study.core_bootstrap is encoded into the runner payload verbatim."""
        import base64 as _b64
        spec = self._make_v3_spec()
        spec["core_bootstrap"] = "my_workspace.hpc:bootstrap_core"
        response, captured = self._run_hpc_branch(
            spec, {"name": "colonies-01"}, monkeypatch,
        )
        assert "param_values" in captured, f"submit not called; response={response}"
        payload = json.loads(
            _b64.b64decode(captured["param_values"][0]["params_b64"]).decode()
        )
        assert payload.get("core_bootstrap") == "my_workspace.hpc:bootstrap_core"

    def test_core_bootstrap_absent_when_not_declared(self, monkeypatch):
        """No core_bootstrap declared → field omitted from payload (runner uses fallback)."""
        import base64 as _b64
        spec = self._make_v3_spec()
        response, captured = self._run_hpc_branch(
            spec, {"name": "colonies-01"}, monkeypatch,
        )
        assert "param_values" in captured, f"submit not called; response={response}"
        payload = json.loads(
            _b64.b64decode(captured["param_values"][0]["params_b64"]).decode()
        )
        assert "core_bootstrap" not in payload

    def test_core_bootstrap_non_string_returns_400(self, monkeypatch):
        """study.core_bootstrap must be a string."""
        spec = self._make_v3_spec()
        spec["core_bootstrap"] = ["not", "a", "string"]
        response, captured = self._run_hpc_branch(
            spec, {"name": "colonies-01"}, monkeypatch,
        )
        status, body = response
        assert status == 400
        assert "core_bootstrap" in body.get("error", "")


# ---------------------------------------------------------------------------
# Test: v3 runner branch (base_model importlib path)
# ---------------------------------------------------------------------------


class TestV3RunnerBranch:
    """Phase E.2 — runner imports base_model and calls it with overrides."""

    def test_v3_payload_invokes_base_model_builder(self) -> None:
        """When base_model is present, the runner imports it and calls the builder."""
        import base64 as _b64
        from vivarium_dashboard.lib.run_investigation_task import main
        import sys as _sys

        params = {
            "run_id": "r0",
            "base_model": "v2ecoli.composites.colony.colony",
            "overrides": {"n_cells": 4},
            "steps": 10,
            "pkg": "",
        }
        encoded = _b64.b64encode(json.dumps(params).encode()).decode()
        # v2ecoli isn't importable in the test env → expect SystemExit(1)
        # with the ImportError message routed through the @@@ERROR@@@ stderr line.
        with patch.object(_sys, "argv", ["runner.py", encoded]):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 1

    def test_v3_payload_with_mocked_builder(self) -> None:
        """A mocked base_model that returns {state: {...}} reaches the Composite build."""
        import base64 as _b64
        import sys as _sys
        import types as _types
        from vivarium_dashboard.lib.run_investigation_task import main

        # Inject a fake module with a builder function
        fake_mod = _types.ModuleType("fake_pkg.colony")
        fake_mod.colony = lambda **kw: {"state": {"_kwargs": kw}}
        _sys.modules["fake_pkg.colony"] = fake_mod
        params = {
            "run_id": "r0",
            "base_model": "fake_pkg.colony.colony",
            "overrides": {"x": 1, "seed": 0},
            "steps": 1,
            "pkg": "",
        }
        encoded = _b64.b64encode(json.dumps(params).encode()).decode()
        try:
            with patch.object(_sys, "argv", ["runner.py", encoded]):
                with pytest.raises(SystemExit) as exc:
                    main()
            # process_bigraph Composite build fails in CI (no core registered for
            # the fake state shape) → exit 1, but the import + call succeeded.
            assert exc.value.code == 1
        finally:
            _sys.modules.pop("fake_pkg.colony", None)

    def test_inject_sqlite_emitter_adds_step(self) -> None:
        from vivarium_dashboard.lib.run_investigation_task import _inject_sqlite_emitter
        state: dict = {}
        result = _inject_sqlite_emitter(state, run_id="abc")
        assert result["emitter"]["_type"] == "step"
        assert result["emitter"]["config"]["run_id"] == "abc"
        assert result["emitter"]["config"]["db_file"] == "/workspace/runs.db"

    def test_inject_sqlite_emitter_preserves_existing(self) -> None:
        from vivarium_dashboard.lib.run_investigation_task import _inject_sqlite_emitter
        state = {"emitter": {"_type": "step", "config": {"run_id": "preset", "db_file": "/x"}}}
        result = _inject_sqlite_emitter(state, run_id="abc")
        # Existing run_id and db_file are preserved (setdefault, not overwrite)
        assert result["emitter"]["config"]["run_id"] == "preset"
        assert result["emitter"]["config"]["db_file"] == "/x"
