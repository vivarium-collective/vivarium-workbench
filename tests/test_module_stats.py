"""Unit tests for lib.module_stats.module_content_stats / _norm /
_installed_composites_by_norm.

Uses the shared ws_federation_demo fixture (a linked "donor-repo" module with
1 composite + 1 study + 1 investigation) for the content-count assertions,
and a TEMP workspace (tmp_path) — never mutating the shared fixture — for the
n_used assertion, since that requires adding an own study that references
one of the module's items.

Stats are keyed by ``_norm(name)`` (lowercased, dashes -> underscores), so
"donor-repo" surfaces as "donor_repo" throughout.
"""
from __future__ import annotations

import shutil
import sys
import textwrap
from pathlib import Path

import yaml

from vivarium_workbench.lib.module_stats import (
    _installed_composites_by_norm,
    _norm,
    _processes_by_norm,
    module_content_stats,
)

# A synthetic registry payload spanning several owning packages + non-process
# kinds, so the process-count attribution can be asserted without a live venv /
# build_core. Mirrors the real /api/registry shape (each entry: name, address,
# kind).
_FAKE_REGISTRY = {
    "processes": [
        # viva-ketchup contributes 2 (one process, one step); pbg_ spelling must
        # collapse to the same "ketchup" identity.
        {"name": "KetchupEstimator", "kind": "process",
         "address": "viva_ketchup.processes.KetchupEstimator"},
        {"name": "KetchupDynamicEstimator", "kind": "step",
         "address": "pbg_ketchup.steps.KetchupDynamicEstimator"},
        # viva-copasi contributes 3.
        {"name": "BaseCopasi", "kind": "process", "address": "pbg_copasi.processes.BaseCopasi"},
        {"name": "CopasiODE", "kind": "process", "address": "viva_copasi.processes.CopasiODE"},
        {"name": "CopasiStep", "kind": "step", "address": "viva_copasi.steps.CopasiStep"},
        # A workspace's own package.
        {"name": "MillardPDMPMetabolism", "kind": "step",
         "address": "v2ecoli.steps.millard_pdmp_metabolism.MillardPDMPMetabolism"},
        # Framework class — attributes to its OWN key, never double-counted onto
        # a module card.
        {"name": "Emitter", "kind": "process", "address": "process_bigraph.emitter.Emitter"},
        # Non-process kinds must be excluded from the count.
        {"name": "ParquetEmitter", "kind": "emitter", "address": "pbg_emitters.parquet.ParquetEmitter"},
        {"name": "SomeChart", "kind": "visualization", "address": "viva_copasi.viz.SomeChart"},
        {"name": "AType", "kind": "type", "address": "viva_ketchup.types.AType"},
    ]
}

FIX = Path(__file__).parent / "_fixtures" / "ws_federation_demo"


def test_norm_equates_dash_underscore_and_case_variants():
    # dash/underscore/case all fold together.
    assert _norm("Spatio-Flux") == _norm("spatio_flux") == "spatio_flux"
    # First dot-segment only, so a dotted suffix doesn't break the match.
    assert _norm("v2ecoli.composites") == "v2ecoli"
    assert _norm(None) == ""
    assert _norm("") == ""


def test_norm_collapses_pbg_viva_alias_to_same_identity():
    # The two rebrand spellings of one module normalize to the SAME identity.
    assert _norm("pbg-copasi") == _norm("viva_copasi") == _norm("copasi") == "copasi"
    assert _norm("pbg_ketchup") == _norm("Viva-ketchup") == "ketchup"
    assert _norm("pbg_ketchup.composites") == "ketchup"
    assert _norm("Viva-munk") == _norm("pbg_munk") == "munk"
    # Conservative: only the pbg_/viva_ prefix is collapsed, not longer common
    # substrings, so unrelated names stay distinct.
    assert _norm("pbg_ketchup") != _norm("pbg_copasi")
    assert _norm("spatio_flux") == "spatio_flux"      # no alias prefix
    # The bare prefix alone must not collapse to empty.
    assert _norm("pbg_") == "pbg_"
    assert _norm("viva") == "viva"


def test_module_content_stats_counts_donor_repo_content():
    stats = module_content_stats(FIX)
    key = _norm("donor-repo")
    assert key in stats
    rec = stats[key]
    assert rec["n_composites"] == 1
    assert rec["n_studies"] == 1
    assert rec["n_investigations"] == 1
    # host_ws has no OWN studies, but the donor's own (federated) study
    # references the donor composite, so cross-workspace usage counts it (1).
    assert rec["n_used"] == 1


def test_module_content_stats_never_raises_with_no_external_dir(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: solo\n")
    # No `external/` links, so nothing federation-sourced can appear;
    # whatever installed-package composites the current interpreter happens
    # to have (best-effort, environment-dependent) must not include the
    # donor fixture's identity, and the call must not raise.
    stats = module_content_stats(tmp_path)
    assert isinstance(stats, dict)
    assert _norm("donor-repo") not in stats
    assert _norm("solo") not in stats


def test_module_content_stats_n_used_counts_own_study_reference(tmp_path):
    # Build a fresh host workspace (do NOT mutate the shared FIX fixture):
    # link the same donor-repo module, then add an own study whose baseline
    # composite references one of the module's composites.
    (tmp_path / "workspace.yaml").write_text("name: host2\n")
    ext = tmp_path / "external"
    ext.mkdir()
    shutil.copytree(FIX / "external" / "donor", ext / "donor")

    studies = tmp_path / "studies" / "my_study"
    studies.mkdir(parents=True)
    (studies / "study.yaml").write_text(
        "name: my_study\n"
        "description: References the donor module's composite.\n"
        "baseline:\n"
        "  - {name: base, composite: donor.composites.donor}\n"
    )

    stats = module_content_stats(tmp_path)
    key = _norm("donor-repo")
    assert key in stats
    rec = stats[key]
    assert rec["n_composites"] == 1
    assert rec["n_studies"] == 1
    assert rec["n_investigations"] == 1
    # Both this workspace's own `my_study` AND the donor's own federated study
    # reference the donor composite, so n_used counts both (2).
    assert rec["n_used"] == 2

    # The shared fixture must be untouched.
    assert not (FIX / "studies").exists()


def test_installed_composites_by_norm_finds_a_synthetic_package(tmp_path, monkeypatch):
    """Build a synthetic *installed-looking* package -- a real package dir
    plus a minimal .dist-info so ``importlib.metadata.distributions()`` picks
    it up -- with a packaged ``composites/`` dir, and confirm the generalized
    (non-pbg-only) scanner finds it grouped under its normalized name. This
    proves wheel-installed modules that don't follow the `pbg-`/`viva-`
    naming convention still count (the scanner enumerates ALL distributions,
    not just pbg-prefixed ones).
    """
    dist_name = "zzsynthetic-mod-stats-pkg"
    pkg_name = "zzsynthetic_mod_stats_pkg"

    pkg_dir = tmp_path / pkg_name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    composites_dir = pkg_dir / "composites"
    composites_dir.mkdir()
    (composites_dir / "demo.composite.yaml").write_text(
        textwrap.dedent(
            """\
            name: demo
            state: {}
            """
        )
    )

    dist_info = tmp_path / f"{pkg_name}-0.0.1.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {dist_name}\nVersion: 0.0.1\n"
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    import importlib

    importlib.invalidate_caches()
    try:
        by_norm = _installed_composites_by_norm()
    finally:
        sys.modules.pop(pkg_name, None)

    key = _norm(dist_name)
    assert key == pkg_name
    assert key in by_norm
    assert any(spec_id.endswith(".composites.demo") for spec_id in by_norm[key])


def test_build_catalog_merges_stats_via_normalized_name(tmp_path, monkeypatch):
    """catalog.build_catalog's module-cards merge looks up module_content_stats
    by ``_norm(name)``, so registry entries spelled with dashes ("pbg-copasi")
    and ones spelled with underscores ("pbg_ketchup") both pick up the right
    stats record, even though both spelling conventions coexist in the
    registry (see module docstring)."""
    from vivarium_workbench.lib import catalog as _catalog

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "name": "t", "package_path": "pkg_t",
    }))
    (ws / "pkg_t").mkdir()

    monkeypatch.setattr(_catalog, "_detect_workspace_venv_distributions", lambda _w: {})
    monkeypatch.setattr(_catalog, "_check_installed_module_sync", lambda ws, pkg, path: None)
    monkeypatch.setattr("viva_superpowers.catalog.load_registry", lambda _w: [
        {"name": "pbg-copasi", "package": "pbg_copasi", "description": "c"},
        {"name": "pbg_ketchup", "package": "pbg_ketchup", "description": "k"},
    ])
    # module_content_stats keys are alias-collapsed (pbg_/viva_ prefix stripped),
    # so "pbg-copasi" -> "copasi" and "pbg_ketchup" -> "ketchup".
    monkeypatch.setattr(_catalog, "module_content_stats", lambda ws_root: {
        "copasi": {
            "n_composites": 3, "n_investigations": 0, "n_studies": 1,
            "n_used": 1, "last_updated": None,
        },
        "ketchup": {
            "n_composites": 5, "n_investigations": 2, "n_studies": 0,
            "n_used": 0, "last_updated": None,
        },
    })

    modules = _catalog.build_catalog(ws)["modules"]
    by_name = {m["name"]: m for m in modules}

    cp = by_name["pbg-copasi"]
    assert cp["n_composites"] == 3
    assert cp["n_studies"] == 1
    assert cp["n_used"] == 1

    ket = by_name["pbg_ketchup"]
    assert ket["n_composites"] == 5
    assert ket["n_investigations"] == 2


def test_n_used_counts_bare_registered_name_for_uninstalled_repo(tmp_path):
    """A repo whose package isn't importable in this venv but IS imported and
    referenced by a study (by the bare short name it's registered under) still
    gets a non-zero affected-studies count.

    Mirrors the ketchup case: `viva-ketchup` (package `pbg_ketchup`) is imported
    but not importable, and a study references the bare `ketchup_baseline`
    composite that v2ecoli's core.py registers on its behalf. No `external/`
    link and no installed distribution — pure scan-based attribution.
    """
    (tmp_path / "workspace.yaml").write_text(
        "name: host_ket\n"
        "imports:\n"
        "  viva_ketchup:\n"
        "    package: pbg_ketchup\n"
    )
    studies = tmp_path / "studies" / "uses_ketchup"
    studies.mkdir(parents=True)
    (studies / "study.yaml").write_text(
        "name: uses_ketchup\n"
        "description: References ketchup's bare registered composite.\n"
        "baseline:\n"
        "  - {name: base, composite: ketchup_baseline}\n"
    )

    stats = module_content_stats(tmp_path)
    key = _norm("viva-ketchup")            # -> "ketchup"
    assert key == "ketchup"
    assert key in stats
    assert stats[key]["n_used"] == 1


def test_processes_by_norm_attributes_by_package_prefix(tmp_path, monkeypatch):
    """`_processes_by_norm` counts only process/step classes, attributed to the
    alias-collapsed owning package of each class address — so viva-ketchup's and
    viva-copasi's contributed Steps/Processes are counted under `ketchup`/`copasi`
    regardless of pbg_/viva_ spelling, framework classes fall under their own key,
    and emitters/visualizations/types are excluded."""
    import vivarium_workbench.lib.registry as _registry
    monkeypatch.setattr(_registry, "build_registry", lambda ws_root, **kw: _FAKE_REGISTRY)

    by_norm = _processes_by_norm(tmp_path)
    assert len(by_norm["ketchup"]) == 2       # process + step, pbg_/viva_ collapsed
    assert len(by_norm["copasi"]) == 3        # viz excluded, not counted
    assert len(by_norm["v2ecoli"]) == 1
    # Framework class keeps its OWN key — never merged onto a module card.
    assert by_norm.get("process_bigraph") and len(by_norm["process_bigraph"]) == 1
    # Non-process kinds contribute nothing.
    assert "emitters" not in by_norm          # ParquetEmitter (kind=emitter) skipped


def test_processes_by_norm_degrades_to_empty_on_registry_error(tmp_path, monkeypatch):
    """A failing / unavailable registry must not break the count — degrade to {}."""
    import vivarium_workbench.lib.registry as _registry

    def _boom(ws_root, **kw):
        raise RuntimeError("no venv / build_core failed")

    monkeypatch.setattr(_registry, "build_registry", _boom)
    assert _processes_by_norm(tmp_path) == {}


def test_module_content_stats_includes_n_processes(tmp_path, monkeypatch):
    """`module_content_stats` surfaces `n_processes` per module, so a repo whose
    processes aren't among the loaded composite artifacts (viva-ketchup /
    viva-copasi) still reports a real Processes count instead of `—`."""
    import vivarium_workbench.lib.registry as _registry
    monkeypatch.setattr(_registry, "build_registry", lambda ws_root, **kw: _FAKE_REGISTRY)

    (tmp_path / "workspace.yaml").write_text("name: host_procs\n")
    stats = module_content_stats(tmp_path)

    assert stats["ketchup"]["n_processes"] == 2
    assert stats["copasi"]["n_processes"] == 3
    assert stats["v2ecoli"]["n_processes"] == 1
    # Every emitted record carries the field.
    assert all("n_processes" in rec for rec in stats.values())


def test_build_catalog_surfaces_n_processes(tmp_path, monkeypatch):
    """catalog.build_catalog copies `n_processes` onto each module card so the
    frontend backfill (`b.process = c.n_processes`) has a value to read."""
    from vivarium_workbench.lib import catalog as _catalog

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "name": "t", "package_path": "pkg_t",
    }))
    (ws / "pkg_t").mkdir()

    monkeypatch.setattr(_catalog, "_detect_workspace_venv_distributions", lambda _w: {})
    monkeypatch.setattr(_catalog, "_check_installed_module_sync", lambda ws, pkg, path: None)
    monkeypatch.setattr("viva_superpowers.catalog.load_registry", lambda _w: [
        {"name": "viva-ketchup", "package": "pbg_ketchup", "description": "k"},
    ])
    monkeypatch.setattr(_catalog, "module_content_stats", lambda ws_root: {
        "ketchup": {"n_processes": 2, "n_composites": 3, "n_investigations": 0,
                    "n_studies": 0, "n_used": 0, "n_repos": 1, "last_updated": None},
    })

    modules = _catalog.build_catalog(ws)["modules"]
    by_name = {m["name"]: m for m in modules}
    assert by_name["viva-ketchup"]["n_processes"] == 2
    # A module with no stats record still carries the field, defaulted to 0.
    assert all("n_processes" in m for m in modules if isinstance(m, dict))


def test_alias_join_pbg_and_viva_reference_same_module(tmp_path):
    """A study referencing the `pbg_`-prefixed composite id attributes to the
    SAME identity a `viva_`-prefixed import/lookup resolves to (and vice versa),
    so a rebrand-mismatched repo row fills instead of showing `—`."""
    (tmp_path / "workspace.yaml").write_text(
        "name: host_alias\n"
        "imports:\n"
        "  viva_ketchup:\n"
        "    package: viva_ketchup\n"
    )
    studies = tmp_path / "studies" / "s1"
    studies.mkdir(parents=True)
    # Reference the pbg_-spelled fully-qualified composite id.
    (studies / "study.yaml").write_text(
        "name: s1\n"
        "description: d\n"
        "baseline:\n"
        "  - {name: base, composite: pbg_ketchup.composites.ketchup_baseline}\n"
    )

    stats = module_content_stats(tmp_path)
    # pbg_ketchup (from the composite id) and viva_ketchup (the import) both
    # collapse to "ketchup", so the usage joins the repo row.
    assert _norm("pbg_ketchup") == _norm("viva_ketchup") == "ketchup"
    assert "ketchup" in stats
    assert stats["ketchup"]["n_used"] == 1
    assert stats["ketchup"]["n_composites"] == 1
