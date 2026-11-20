"""Goldens for the core's readers of the session build (docs/backend-adapters.md step P2, Builds B1).

The expected values were taken on the base commit (each reader opened ``.viv-build.json`` itself), before the
readers moved onto ``backends.session_build``; they pin that providers-on behaviour stays the same. A cell that
changed on purpose says ``CHANGED in B1`` next to its value: the ``{}`` stamp's ParCa notice (declared), a null id on
the rail chip ("?" like a missing one), and two crashes on a non-dict stamp. No writer produces any of those stamps.

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


def _cases(*same_without_the_plugin: str) -> list:
    """``STAMPS``'s cases for a parametrize: those whose answer the stamp changes are read through the SMS provider's
    ``session_build`` (the registry), so they are marked ``plugin``; the named ones answer the same with no provider
    (no stamp, or one the reader falls back past), so they stay core (step P3, K2c)."""
    return [c if c in same_without_the_plugin else pytest.param(c, marks=pytest.mark.plugin) for c in STAMPS]


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

@pytest.mark.parametrize("case", _cases("absent"))
def test_run_target_for(tmp_path, case):
    from vivarium_workbench.lib import run_core

    assert run_core.run_target_for(_ws(tmp_path, case)) == ("local" if case == "absent" else "deployment")


# --- the rail chip -----------------------------------------------------------------------------------------------------

CHIP = {
    "absent": ("", False),
    "full": ("feat/x @ 0123456789 · remote build #42", True),
    "repo_only": ("@ c · remote build #?", True),
    "missing_id": ("b @ abcdef1234 · remote build #?", True),
    "null_id": ("@ abc · remote build #?", True),  # CHANGED in B1 (base: "#None"): a null id reads as a missing one
    "unreadable": ("remote build", True),
    "non_dict": ("remote build", True),
    "empty": ("· remote build #?", True),
}


@pytest.mark.parametrize("case", _cases("absent"))
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


@pytest.mark.parametrize("case", _cases("absent", "unreadable", "non_dict", "empty"))
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

#: the notice, ``None`` when there is none.
PARCA = {
    "absent": None,
    "full": "remote build #42 @ 0123456" + _TAIL,
    "repo_only": "this remote build @ c" + _TAIL,
    "missing_id": "this remote build @ abcdef1" + _TAIL,
    "null_id": "this remote build @ abc" + _TAIL,
    "unreadable": None,
    "non_dict": None,  # CHANGED in B1 (base: AttributeError -- a non-dict stamp crashed the degrade path)
    "empty": "this remote build" + _TAIL,  # CHANGED in B1 (base: no notice) -- the declared ``{}``-stamp change
}


@pytest.mark.parametrize("case", _cases("absent", "unreadable", "non_dict"))
def test_parca_notice(tmp_path, monkeypatch, case):
    from vivarium_workbench.lib import composite_state_views as csv

    csv.clear_cache()
    ws = _ws(tmp_path, case)
    monkeypatch.setattr(csv, "composite_state_via_subprocess",
                        lambda ws, ref, overrides=None: {"__build_error__": "Cache at 'out/cache' is stale or unversioned"})
    want = PARCA[case]
    body, status = csv.build_composite_state(ws, "gen")
    assert status == 200
    be = body["build_error"]
    assert be.get("notice") == want
    assert be.get("remote_no_cache") is (True if want else None)


# --- the readouts soft-degrade -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("case", _cases("absent"))
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

#: "error" = the "no simulator_id" error body; "local" = resolved like a local workspace.
RESOLVE = {
    "absent": "local",
    "full": "local",
    "repo_only": "error",
    "missing_id": "error",
    "null_id": "error",
    "unreadable": "error",
    "non_dict": "error",  # CHANGED in B1 (base: AttributeError): an unreadable stamp has no id, like any other
    "empty": "error",
}


@pytest.mark.parametrize("case", _cases("absent", "full"))
def test_composite_preview(tmp_path, monkeypatch, case):
    from vivarium_workbench.lib import composite_resolve as cr

    ws = _ws(tmp_path, case)
    monkeypatch.setattr(cr, "resolve_composite", lambda ws, spec_id, overrides=None: {"resolved": spec_id})
    got = cr.resolve_composite_for_request(ws, "x.y")
    assert got == ({"error": "remote build has no simulator_id stamp"} if RESOLVE[case] == "error"
                   else {"resolved": "x.y"})


# --- the provider's answer (SmsProvider.session_build -> remote_pinned.session_build) ----------------------------------

#: The SessionBuild each stamp maps to: (readable, id, repo_url, commit, branch); ``None`` = no session build.
MAPPING = {
    "absent": None,
    "full": (True, 42, "https://github.com/o/r", "0123456789abcdef", "feat/x"),
    "repo_only": (True, None, "https://github.com/o/r2", "c", None),
    "missing_id": (True, None, None, "abcdef1234567", "b"),
    "null_id": (True, None, None, "abc", None),
    "unreadable": (False, None, None, None, None),
    "non_dict": (False, None, None, None, None),
    "empty": (True, None, None, None, None),
}


@pytest.mark.plugin
@pytest.mark.parametrize("case", list(STAMPS))
def test_the_sms_provider_maps_the_stamp(tmp_path, case):
    from vivarium_workbench_sms.provider import PROVIDER

    ws = _ws(tmp_path, case)
    got = PROVIDER.session_build(ws)
    want = MAPPING[case]
    assert (None if got is None else (got.readable, got.id, got.repo_url, got.commit, got.branch)) == want
    assert backends.session_build(ws) == got  # the core asks the provider and passes its answer on unchanged


@pytest.mark.plugin
def test_the_sms_provider_claims_a_session_build_through_session_build(tmp_path, monkeypatch):
    """``SmsProvider.select`` asks ``remote_pinned.session_build`` (through the module, so this patch is live), not
    ``run_core.run_target_for`` (which would loop core -> provider -> core)."""
    from vivarium_workbench.lib.backends.provider import SessionBuild
    from vivarium_workbench_sms import remote_pinned

    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: False)
    monkeypatch.setattr(backends, "named", lambda: None)
    assert backends.select(tmp_path) is None
    monkeypatch.setattr(remote_pinned, "session_build",
                        lambda ws: SessionBuild(readable=False, id=None, repo_url=None, commit=None, branch=None))
    assert backends.select(tmp_path) is backends.SMS_LEGACY


# --- providers off: a stamped workspace reads as a plain local one (declared) ------------------------------------------

def test_with_providers_off_a_stamped_workspace_reads_as_local(tmp_path, monkeypatch):
    _assert_a_stamped_workspace_reads_as_local(tmp_path, monkeypatch)


@pytest.mark.plugin
def test_with_providers_off_the_plugin_is_never_asked_about_the_session_build(tmp_path, monkeypatch):
    from vivarium_workbench_sms import remote_pinned

    def never(ws):
        raise AssertionError("the plugin was asked about the session build with providers off")

    monkeypatch.setattr(remote_pinned, "session_build", never)
    _assert_a_stamped_workspace_reads_as_local(tmp_path, monkeypatch)


def _assert_a_stamped_workspace_reads_as_local(tmp_path, monkeypatch):
    import jinja2

    from vivarium_workbench.lib import composite_resolve as cr
    from vivarium_workbench.lib import composite_state_views as csv
    from vivarium_workbench.lib import provenance_manifest as pm
    from vivarium_workbench.lib import report, run_core
    from vivarium_workbench.lib.backends import provider as _provider

    monkeypatch.setenv(_provider.PROVIDERS_ENV, "none")
    backends.reset_registry()

    ws = _ws(tmp_path, "full")
    plain = _ws(tmp_path / "plain", "absent")
    assert backends.session_build(ws) is None
    assert run_core.run_target_for(ws) == "local"

    m, p = pm.build_manifest(ws), pm.build_manifest(plain)
    assert (m["repo"], m["commit"], m["branch"], m["simulator_id"]) == (p["repo"], p["commit"], p["branch"], None)

    seen: dict = {}

    def capture(self, *a, **k):
        seen.update(k)
        return ""

    monkeypatch.setattr(jinja2.environment.Template, "render", capture)
    report.render_workspace_report(ws, today="2026-01-01")
    assert (seen["workspace_branch"], seen["workspace_is_remote"]) == CHIP["absent"]

    csv.clear_cache()
    monkeypatch.setattr(csv, "composite_state_via_subprocess",
                        lambda ws, ref, overrides=None: {"__build_error__": "Cache at 'out/cache' is stale or unversioned"})
    body, _ = csv.build_composite_state(ws, "gen")
    assert "notice" not in body["build_error"] and "remote_no_cache" not in body["build_error"]

    monkeypatch.setattr(cr, "resolve_composite", lambda ws, spec_id, overrides=None: {"resolved": spec_id})
    assert cr.resolve_composite_for_request(ws, "x.y") == {"resolved": "x.y"}


# --- anti-seam: the core never names the provider's record --------------------------------------------------------------

def _docstring_nodes(tree) -> set:
    import ast

    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def test_no_core_string_literal_names_the_session_build_stamp():
    """``.viv-build.json`` is the SMS provider's record: no core string literal (docstrings aside) names it, except
    ``lib/remote_run.py``'s git-dirtiness exclusion (core bookkeeping, kept on purpose)."""
    import ast

    import vivarium_workbench

    root = Path(vivarium_workbench.__file__).parent
    allowed = {root / "lib" / "remote_run.py"}
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs \
                    and ".viv-build.json" in node.value:
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not offenders, f"core code names the session-build stamp (ask backends.session_build): {offenders}"
    # the scan is live: the one allowed file does name it
    assert ".viv-build.json" in (root / "lib" / "remote_run.py").read_text(encoding="utf-8")


@pytest.mark.plugin
def test_a_deeply_nested_stamp_is_unreadable_not_an_error(tmp_path):
    """``Provider.session_build`` never raises: a stamp nested past the parser's recursion limit is ``readable=False``
    (review of wb#1680; the base's readers caught it with ``except Exception``)."""
    from vivarium_workbench_sms import remote_pinned

    (tmp_path / ".viv-build.json").write_text("[" * 100000 + "]" * 100000)
    sb = remote_pinned.session_build(tmp_path)
    assert sb is not None and sb.readable is False
