"""Golden: where a study run goes for each selected backend (step P2 Routes Rt3, taken on the base first).

``study_runs.run_study_baseline`` and ``run_study_variant`` send a run to the backend the workspace selects
(``backends.select`` / ``backends.active``): a backend that runs its own builds (``builds``: sms-legacy) gets the
baseline through ``builds.run_study``; a backend that runs documents (compose-api, viva-core) gets it through
``remote_run_views.remote_run_submit``; nothing selected runs locally (``launch_into_study``). Rt3 restructures
``_named_backend_runs_documents`` so that it names no provider backend; this table must not change.
"""
from __future__ import annotations

import pytest

from vivarium_workbench.lib import backends, remote_run_views, study_runs


def _chosen(kind):
    return {"none": None, "compose-api": backends.COMPOSE, "viva-core": backends.by_kind("viva-core"),
            "sms-legacy": backends.by_kind("sms-legacy")}[kind]


@pytest.fixture
def dispatch(tmp_path, monkeypatch):
    """Patch the three destinations and the selection; returns ``run(kind) -> (destination, status)``.

    Patches the sms-legacy backend's ``builds`` facet, so every test using it needs the SMS plugin (marked ``plugin``).
    """
    sd = tmp_path / "studies" / "demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text("name: demo\nbaseline:\n  - {name: core, composite: pkg.composites.cell}\n")
    calls: list = []

    monkeypatch.setattr(remote_run_views, "remote_run_submit",
                        lambda ws_root, body: (calls.append(("documents", body)) or ({"run_id": "d"}, 202)))
    monkeypatch.setattr(study_runs, "launch_into_study",
                        lambda *a, **k: (calls.append(("local", None)) or ({"run_id": "l"}, 202)))
    sms = backends.by_kind("sms-legacy")
    monkeypatch.setattr(type(sms.builds), "run_study",
                        lambda self, ws_root, **k: (calls.append(("builds", k["study"])) or ({"run_id": "b"}, 202)))

    def run(kind):
        chosen = _chosen(kind)
        monkeypatch.setattr(backends, "select", lambda ws_root: chosen)
        monkeypatch.setattr(backends, "active", lambda ws_root: chosen)
        calls.clear()
        body, status = study_runs.run_study_baseline(tmp_path, {"study": "demo"})
        return [c[0] for c in calls], status, study_runs._named_backend_runs_documents(tmp_path)

    return run


@pytest.mark.parametrize("kind, expected", [
    ("none", (["local"], 202, False)),
    ("compose-api", (["documents"], 202, True)),
    ("viva-core", (["documents"], 202, True)),
    ("sms-legacy", (["builds"], 202, False)),
])
@pytest.mark.plugin
def test_the_study_baseline_goes_where_it_went(dispatch, kind, expected):
    assert dispatch(kind) == expected


@pytest.mark.plugin
def test_the_documents_path_sends_the_composite(dispatch, tmp_path, monkeypatch):
    seen: list = []
    monkeypatch.setattr(remote_run_views, "remote_run_submit",
                        lambda ws_root, body: (seen.append(body) or ({"run_id": "d"}, 202)))
    dispatch("viva-core")
    assert seen == [{"study": "demo", "composite": "pkg.composites.cell", "params": {}, "n_steps": None}]
