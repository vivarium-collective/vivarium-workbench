"""§2A.8 workstream 8 step 2b — the declared-scale precheck.

Step 1 removed the per-method transport pin that had been acting, accidentally,
as a cost policy. Since then nothing separates a 1x1 study run from a 1000x10 one
on the LOCAL path — which is a subprocess on whichever host serves the workbench.

This checks the scale a study DECLARES (`n_seeds` x `n_generations`) rather than
inferring cost, and refuses up front instead of after forty minutes.
"""
import pytest

from vivarium_workbench.lib.study_runs import (
    _SCALE_BUDGET_DEFAULT,
    _SCALE_BUDGET_SUFFIX,
    _declared_scale_exceeds_budget,
)


# --- the arithmetic ----------------------------------------------------------

@pytest.mark.parametrize("params,expected", [
    ({},                                   None),   # undeclared: silence is not a claim
    ({"n_seeds": 1, "n_generations": 1},   None),
    ({"n_seeds": 10, "n_generations": 5},  None),   # 50 == budget, not over
    ({"n_seeds": 1000, "n_generations": 10}, (10000, _SCALE_BUDGET_DEFAULT)),
    ({"n_seeds": 51},                      (51, _SCALE_BUDGET_DEFAULT)),
])
def test_declared_scale(params, expected):
    assert _declared_scale_exceeds_budget(params) == expected


@pytest.mark.parametrize("params", [
    {"n_seeds": None, "n_generations": None},
    {"n_seeds": "", "n_generations": "abc"},
    {"n_seeds": 0, "n_generations": -3},
    None,
])
def test_absent_or_garbage_knobs_never_block(params):
    """A study that declares nothing must not be refused — the check reads a
    declaration, and an unparseable one is not a declaration of scale."""
    assert _declared_scale_exceeds_budget(params) is None


def test_budget_is_config_overridable(monkeypatch):
    monkeypatch.setenv(_SCALE_BUDGET_SUFFIX.join(["VIVARIUM_WORKBENCH_", ""]), "4")
    assert _declared_scale_exceeds_budget({"n_seeds": 5}) == (5, 4)
    assert _declared_scale_exceeds_budget({"n_seeds": 4}) is None


def test_budget_zero_disables_the_check(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_" + _SCALE_BUDGET_SUFFIX, "0")
    assert _declared_scale_exceeds_budget({"n_seeds": 1000, "n_generations": 10}) is None


def test_unparseable_budget_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_" + _SCALE_BUDGET_SUFFIX, "not-a-number")
    got = _declared_scale_exceeds_budget({"n_seeds": 1000, "n_generations": 10})
    assert got == (10000, _SCALE_BUDGET_DEFAULT)


# --- reached from the real entry point ---------------------------------------

def test_launch_into_study_refuses_an_oversized_local_run(tmp_path, monkeypatch):
    """Proves the guard is on the path, not just importable."""
    from vivarium_workbench.lib import backends, study_runs, run_core

    monkeypatch.setattr(backends, "resolve_run_target", lambda p: "local")
    monkeypatch.setattr(
        run_core, "invoke_run",
        lambda *a, **k: type("P", (), {"target": "local", "run_id": "r"})())

    def _never(*a, **k):
        raise AssertionError("the run must not be launched")

    monkeypatch.setattr(study_runs, "_launch_run_and_flush", _never)

    body, status = study_runs.launch_into_study(
        tmp_path, "s", "spec", {"n_seeds": 1000, "n_generations": 10}, None)
    assert status == 409
    assert body["declared_simulations"] == 10000
    assert "deployment" in body["hint"]


def test_dry_run_is_exempt(tmp_path, monkeypatch):
    """A preview declares scale but does not spend it."""
    from vivarium_workbench.lib import backends, study_runs, run_core

    monkeypatch.setattr(backends, "resolve_run_target", lambda p: "local")
    monkeypatch.setattr(
        run_core, "invoke_run",
        lambda *a, **k: type("P", (), {"target": "local", "run_id": "r"})())
    reached = []
    monkeypatch.setattr(study_runs, "_launch_run_and_flush",
                        lambda *a, **k: (reached.append(1), ({"ok": True}, 200))[1])

    body, status = study_runs.launch_into_study(
        tmp_path, "s", "spec", {"n_seeds": 1000, "n_generations": 10}, None,
        dry_run=True)
    # `status != 409` alone could pass for an unrelated reason — assert the run
    # actually got past the guard.
    assert reached, f"never reached the launch; returned {status} {body}"
    assert status != 409, body
