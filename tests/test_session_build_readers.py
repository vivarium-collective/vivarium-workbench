"""Goldens for the core's readers of the session build (docs/backend-adapters.md step P2, Builds B1).

The expected values were taken on the base commit (each reader opened ``.viv-build.json`` itself), before the
readers moved onto ``backends.session_build``; they pin that providers-on behaviour stays the same. A cell that
changed on purpose says so next to its value.

Readers: ``run_core.run_target_for``, the rail chip (``report.render_workspace_report``), the provenance manifest
(``provenance_manifest.build_manifest``), the ParCa notice (``composite_state_views``), the readouts soft-degrade
(``readouts_views.build_study_readouts``) and the composite preview (``composite_resolve``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vivarium_workbench.lib import backends

_FULL = {"branch": "feat/x", "commit": "0123456789abcdef", "simulator_id": 42,
         "repo_url": "https://github.com/o/r", "repo": "r"}

#: Stamp contents by case; ``None`` = no stamp, a ``str`` is written as is (not JSON).
STAMPS: "dict[str, object]" = {
    "absent": None,
    "full": _FULL,
    "repo_only": {"repo": "https://github.com/o/r2", "commit": "c"},
    "missing_id": {"branch": "b", "commit": "abcdef1234567"},
    "null_id": {"simulator_id": None, "commit": "abc"},
    "unreadable": "not json{",
    "non_dict": [1, 2],
    "empty": {},
}


@pytest.fixture(autouse=True)
def _providers_on(monkeypatch):
    """These goldens are the providers-on behaviour, whatever the environment says."""
    from vivarium_workbench.lib.backends import provider as _provider

    monkeypatch.delenv(_provider.PROVIDERS_ENV, raising=False)
    backends.reset_registry()
    yield
    backends.reset_registry()


def _ws(tmp_path: Path, case: str) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    stamp = STAMPS[case]
    if isinstance(stamp, str):
        (ws / ".viv-build.json").write_text(stamp, encoding="utf-8")
    elif stamp is not None:
        (ws / ".viv-build.json").write_text(json.dumps(stamp), encoding="utf-8")
    return ws


# --- run_core.run_target_for -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("case", list(STAMPS))
def test_run_target_for(tmp_path, case):
    from vivarium_workbench.lib import run_core

    assert run_core.run_target_for(_ws(tmp_path, case)) == ("local" if case == "absent" else "deployment")


# --- the rail chip -----------------------------------------------------------------------------------------------------

CHIP = {
    "absent": ("", False),
    "full": ("feat/x @ 0123456789 · remote build #42", True),
    "repo_only": ("@ c · remote build #?", True),
    "missing_id": ("b @ abcdef1234 · remote build #?", True),
    "null_id": ("@ abc · remote build #None", True),
    "unreadable": ("remote build", True),
    "non_dict": ("remote build", True),
    "empty": ("· remote build #?", True),
}


@pytest.mark.parametrize("case", list(STAMPS))
def test_rail_chip(tmp_path, monkeypatch, case):
    import jinja2

    from vivarium_workbench.lib import report

    seen: dict = {}

    def capture(self, *a, **k):
        seen.update(k)
        return ""

    monkeypatch.setattr(jinja2.environment.Template, "render", capture)
    report.render_workspace_report(_ws(tmp_path, case), today="2026-01-01")
    assert (seen["workspace_branch"], seen["workspace_is_remote"]) == CHIP[case]


# --- the provenance manifest -------------------------------------------------------------------------------------------

#: (repo, commit, branch, simulator_id); ``None`` = the git fallback (the same as with no stamp).
MANIFEST = {
    "absent": None,
    "full": ("https://github.com/o/r", "0123456789abcdef", "feat/x", 42),
    "repo_only": ("https://github.com/o/r2", "c", "", None),
    "missing_id": ("", "abcdef1234567", "b", None),
    "null_id": ("", "abc", "", None),
    "unreadable": None,
    "non_dict": None,
    "empty": ("", "", "", None),
}


@pytest.mark.parametrize("case", list(STAMPS))
def test_manifest(tmp_path, case):
    from vivarium_workbench.lib import provenance_manifest as pm

    m = pm.build_manifest(_ws(tmp_path, case))
    got = (m["repo"], m["commit"], m["branch"], m["simulator_id"])
    if MANIFEST[case] is None:
        plain = pm.build_manifest(_ws(tmp_path / "plain", "absent"))
        assert got == (plain["repo"], plain["commit"], plain["branch"], None)
    else:
        assert got == MANIFEST[case]


# --- the ParCa notice (composite_state_views) --------------------------------------------------------------------------

_TAIL = (" has no local ParCa cache, so its wiring preview can't be built here — run it on the Cloud, or provision "
         "a local out/cache.")

#: the notice, ``None`` when there is none, or an exception type the base raised.
PARCA = {
    "absent": None,
    "full": "remote build #42 @ 0123456" + _TAIL,
    "repo_only": "this remote build @ c" + _TAIL,
    "missing_id": "this remote build @ abcdef1" + _TAIL,
    "null_id": "this remote build @ abc" + _TAIL,
    "unreadable": None,
    "non_dict": AttributeError,
    "empty": None,
}


@pytest.mark.parametrize("case", list(STAMPS))
def test_parca_notice(tmp_path, monkeypatch, case):
    from vivarium_workbench.lib import composite_state_views as csv

    csv.clear_cache()
    ws = _ws(tmp_path, case)
    monkeypatch.setattr(csv, "composite_state_via_subprocess",
                        lambda ws, ref, overrides=None: {"__build_error__": "Cache at 'out/cache' is stale or unversioned"})
    want = PARCA[case]
    if isinstance(want, type):
        with pytest.raises(want):
            csv.build_composite_state(ws, "gen")
        return
    body, status = csv.build_composite_state(ws, "gen")
    assert status == 200
    be = body["build_error"]
    assert be.get("notice") == want
    assert be.get("remote_no_cache") is (True if want else None)


# --- the readouts soft-degrade -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("case", list(STAMPS))
def test_readouts_soft_degrade(tmp_path, monkeypatch, case):
    import yaml

    from vivarium_workbench.lib import readouts_views as rv

    ws = _ws(tmp_path, case)
    sd = ws / "studies" / "rdemo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "name": "rdemo", "baseline": [{"composite": "nonexistent.composite"}],
        "readouts": [{"name": "panel-x", "store_path": "listeners.foo.bar"}]}))
    monkeypatch.setattr(rv, "_available_observables_for_ref",
                        lambda ws, ref: (_ for _ in ()).throw(FileNotFoundError("out/cache/initial_state.json")))
    body, status = rv.build_study_readouts(ws, "rdemo")
    if case == "absent":
        assert status == 422 and "remote_build" not in body
    else:
        assert status == 200 and body["remote_build"] is True


# --- the composite preview (composite_resolve) -------------------------------------------------------------------------

#: "error" = the "no simulator_id" error body; "local" = resolved like a local workspace; or the base's exception.
RESOLVE = {
    "absent": "local",
    "full": "local",
    "repo_only": "error",
    "missing_id": "error",
    "null_id": "error",
    "unreadable": "error",
    "non_dict": AttributeError,
    "empty": "error",
}


@pytest.mark.parametrize("case", list(STAMPS))
def test_composite_preview(tmp_path, monkeypatch, case):
    from vivarium_workbench.lib import composite_resolve as cr

    ws = _ws(tmp_path, case)
    monkeypatch.setattr(cr, "resolve_composite", lambda ws, spec_id, overrides=None: {"resolved": spec_id})
    want = RESOLVE[case]
    if isinstance(want, type):
        with pytest.raises(want):
            cr.resolve_composite_for_request(ws, "x.y")
        return
    got = cr.resolve_composite_for_request(ws, "x.y")
    assert got == ({"error": "remote build has no simulator_id stamp"} if want == "error" else {"resolved": "x.y"})
