"""Unit tests for ``vivarium_dashboard.lib.report_generator``."""
from __future__ import annotations

import pytest

from vivarium_dashboard.lib.report_generator import (
    ReportGeneratorError,
    build_dispatch,
    render_args,
    resolve_for_entry,
)


# ---------------------------------------------------------------------------
# resolve_for_entry — per-entry overrides top-level
# ---------------------------------------------------------------------------


class TestResolveForEntry:
    def test_per_entry_overrides_top_level(self):
        study = {"report_generator": {"script": "top.py"}}
        entry = {"report_generator": {"script": "per_entry.py"}}
        assert resolve_for_entry(study, entry)["script"] == "per_entry.py"

    def test_top_level_used_when_no_per_entry(self):
        study = {"report_generator": {"script": "top.py"}}
        entry = {"name": "x"}
        assert resolve_for_entry(study, entry)["script"] == "top.py"

    def test_none_when_neither_declared(self):
        assert resolve_for_entry({}, {"name": "x"}) is None

    def test_per_entry_must_be_dict(self):
        # Non-dict per-entry block is ignored; top-level applies.
        study = {"report_generator": {"script": "top.py"}}
        entry = {"report_generator": "not a dict"}
        assert resolve_for_entry(study, entry)["script"] == "top.py"

    def test_returns_shallow_copy(self):
        block = {"script": "x.py", "args": {"a": "b"}}
        study = {"report_generator": block}
        out = resolve_for_entry(study, {})
        out["script"] = "mutated.py"
        assert block["script"] == "x.py"


# ---------------------------------------------------------------------------
# render_args — str.format() with steps_clamped sentinel
# ---------------------------------------------------------------------------


class TestRenderArgs:
    def test_basic_substitution(self):
        out = render_args(
            {"seed": "{overrides[seed]}", "out": "/x/{run_id}.html"},
            run_id="abc",
            overrides={"seed": 7},
            steps=10,
        )
        assert out == {"seed": "7", "out": "/x/abc.html"}

    def test_steps_clamped_caps_at_n(self):
        out = render_args(
            {"duration": "{steps_clamped:5}"},
            run_id="r", overrides={}, steps=999,
        )
        assert out["duration"] == "5"

    def test_steps_clamped_passes_through_when_under_cap(self):
        out = render_args(
            {"duration": "{steps_clamped:10}"},
            run_id="r", overrides={}, steps=3,
        )
        assert out["duration"] == "3"

    def test_steps_clamped_floors_at_one(self):
        out = render_args(
            {"duration": "{steps_clamped:5}"},
            run_id="r", overrides={}, steps=0,
        )
        assert out["duration"] == "1"

    def test_static_string_passes_through(self):
        out = render_args(
            {"x": "literal"},
            run_id="r", overrides={}, steps=1,
        )
        assert out["x"] == "literal"

    def test_missing_key_raises_with_context(self):
        with pytest.raises(ReportGeneratorError) as excinfo:
            render_args(
                {"x": "{overrides[absent]}"},
                run_id="r", overrides={"present": 1}, steps=1,
            )
        assert "absent" in str(excinfo.value)
        assert "available" in str(excinfo.value)

    def test_args_must_be_dict(self):
        with pytest.raises(ReportGeneratorError):
            render_args([], run_id="r", overrides={}, steps=1)  # type: ignore[arg-type]

    def test_null_value_raises(self):
        with pytest.raises(ReportGeneratorError):
            render_args(
                {"x": None}, run_id="r", overrides={}, steps=1,
            )


# ---------------------------------------------------------------------------
# build_dispatch — assembles param_values + cmd_tmpl
# ---------------------------------------------------------------------------


def _gen(script="reports/r.py", args=None, output_dir="out/r"):
    return {
        "script": script,
        "args": args or {"seed": "{overrides[seed]}"},
        "output_dir": output_dir,
    }


def _task(run_id, overrides=None, steps=1):
    return {"run_id": run_id, "overrides": overrides or {"seed": 0}, "steps": steps}


class TestBuildDispatch:
    def test_basic_two_task_dispatch(self):
        tasks = [_task("r0", {"seed": 0}), _task("r1", {"seed": 1})]
        gens = [_gen(), _gen()]
        param_values, cmd = build_dispatch(
            tasks, gens, remote_ws="/ws", sif_path="/img.sif",
        )
        assert len(param_values) == 2
        assert param_values[0]["seed"] == "0"
        assert param_values[1]["seed"] == "1"
        assert "/workspace/reports/r.py" in cmd
        assert "--seed {seed}" in cmd
        assert "mkdir -p /app/out/r" in cmd

    def test_apptainer_binds_present(self):
        param_values, cmd = build_dispatch(
            [_task("r")], [_gen()], remote_ws="/ws", sif_path="/img.sif",
        )
        assert "-B /ws/results:/app/results" in cmd
        assert "-B /ws/out:/app/out" in cmd
        assert "-B /ws:/workspace" in cmd

    def test_short_key_uses_dash_not_double_dash(self):
        # Single-character keys get -k val style.
        param_values, cmd = build_dispatch(
            [_task("r")],
            [_gen(args={"n": "{overrides[seed]}"})],
            remote_ws="/ws", sif_path="/img.sif",
        )
        assert " -n {n} " in cmd or cmd.endswith(" -n {n}'")

    def test_hyphen_in_key_sanitized_for_slot(self):
        # "n-adder" → slot "n_adder"
        param_values, cmd = build_dispatch(
            [_task("r")],
            [_gen(args={"n-adder": "{overrides[seed]}"})],
            remote_ws="/ws", sif_path="/img.sif",
        )
        assert "n_adder" in param_values[0]
        assert "--n-adder {n_adder}" in cmd

    def test_mixed_scripts_rejected(self):
        tasks = [_task("r0"), _task("r1")]
        gens = [_gen(script="a.py"), _gen(script="b.py")]
        with pytest.raises(ReportGeneratorError):
            build_dispatch(tasks, gens, remote_ws="/ws", sif_path="/img.sif")

    def test_missing_script_rejected(self):
        with pytest.raises(ReportGeneratorError):
            build_dispatch(
                [_task("r")],
                [{"args": {"seed": "0"}, "output_dir": "out/x"}],
                remote_ws="/ws", sif_path="/img.sif",
            )

    def test_absolute_script_path_rejected(self):
        with pytest.raises(ReportGeneratorError):
            build_dispatch(
                [_task("r")],
                [_gen(script="/etc/passwd")],
                remote_ws="/ws", sif_path="/img.sif",
            )

    def test_script_with_parent_traversal_rejected(self):
        with pytest.raises(ReportGeneratorError):
            build_dispatch(
                [_task("r")],
                [_gen(script="../escape.py")],
                remote_ws="/ws", sif_path="/img.sif",
            )

    def test_output_dir_with_traversal_rejected(self):
        with pytest.raises(ReportGeneratorError):
            build_dispatch(
                [_task("r")],
                [_gen(output_dir="../escape")],
                remote_ws="/ws", sif_path="/img.sif",
            )

    def test_empty_output_dir_omits_mkdir(self):
        param_values, cmd = build_dispatch(
            [_task("r")], [_gen(output_dir="")],
            remote_ws="/ws", sif_path="/img.sif",
        )
        assert "mkdir -p" not in cmd

    def test_arg_key_mismatch_across_tasks_rejected(self):
        # Per-entry overrides could in principle declare different arg sets;
        # build_dispatch requires they all flatten to the same keyset.
        tasks = [_task("r0"), _task("r1")]
        gens = [
            _gen(args={"seed": "0"}),
            _gen(args={"seed": "0", "extra": "x"}),
        ]
        with pytest.raises(ReportGeneratorError):
            build_dispatch(tasks, gens, remote_ws="/ws", sif_path="/img.sif")
