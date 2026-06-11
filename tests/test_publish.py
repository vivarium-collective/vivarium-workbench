"""Tests for vivarium_dashboard.publish — narrative export / "publish".

Sub-project #2: narrative export.
See docs/superpowers/plans/2026-06-10-narrative-export-subproject-2.md.
"""
import json
import re
from pathlib import Path

import yaml
import pytest

from vivarium_dashboard import server


# ---------------------------------------------------------------------------
# Shared fixture — a minimal workspace with an investigation + two studies.
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Workspace with one investigation and two studies for publish tests."""
    ws = tmp_path / "ws"
    (ws / "workspace.yaml").parent.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text(yaml.safe_dump({
        "name": "test-ws",
        "description": "A test workspace.",
    }))
    inv = ws / "investigations" / "main-inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "main-inv",
        "title": "Main Investigation",
        "studies": ["alpha", "beta"],
        "status": "in_progress",
    }))
    for slug in ("alpha", "beta"):
        sd = ws / "studies" / slug
        sd.mkdir(parents=True)
        (sd / "study.yaml").write_text(yaml.safe_dump({
            "name": slug,
            "schema_version": 3,
            "baseline": [{"name": "core", "composite": f"pkg.{slug}.Core"}],
            "variants": [],
            "objective": f"Objective for {slug}.",
            "status": "draft",
        }))
    monkeypatch.setattr(server, "WORKSPACE", ws)
    return ws


# ---------------------------------------------------------------------------
# Task 3: build_bundle produces the correct layout + JSON parity
# ---------------------------------------------------------------------------

def test_build_bundle_structure_and_parity(tmp_workspace, tmp_path):
    """build_bundle writes the bundle layout and study JSON matches the API builder."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    summary = publish.build_bundle(server.WORKSPACE, out)

    # Bundle root files
    assert (out / "index.html").is_file(), "home shell missing"
    assert (out / "config.json").is_file(), "config.json missing"
    assert (out / "assets" / "data-source.js").is_file(), "data-source.js missing"
    assert (out / "api" / "workspace.json").is_file(), "workspace.json missing"

    # At least one study exported
    assert len(summary["studies"]) >= 1
    slug = summary["studies"][0]

    assert (out / "api" / "study" / f"{slug}.json").is_file(), f"study/{slug}.json missing"
    assert (out / "studies" / slug / "index.html").is_file(), f"study shell missing"

    # JSON parity: bundle file == API builder output
    bundle_json = json.loads((out / "api" / "study" / f"{slug}.json").read_text())
    api_json = json.loads(json.dumps(server._study_detail_spec(slug), default=server._json_default))
    assert bundle_json == api_json, "study JSON parity failed"

    # config.json shape
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["mode"] == "snapshot"
    assert "commit" in cfg


def test_build_bundle_investigation_json(tmp_workspace, tmp_path):
    """build_bundle writes api/iset/<name>.json with the same data as _iset_detail_data."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    iset_file = out / "api" / "iset" / "main-inv.json"
    assert iset_file.is_file(), "iset JSON missing"
    iset_data = json.loads(iset_file.read_text())
    assert "studies" in iset_data

    expected = json.loads(json.dumps(server.Handler._iset_detail_data("main-inv"), default=server._json_default))
    assert iset_data == expected, "iset JSON parity failed"


def test_build_bundle_workspace_json(tmp_workspace, tmp_path):
    """build_bundle writes api/workspace.json matching _workspace_home_data."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    ws_file = out / "api" / "workspace.json"
    assert ws_file.is_file()
    ws_data = json.loads(ws_file.read_text())
    expected = json.loads(json.dumps(server._workspace_home_data(server.WORKSPACE), default=server._json_default))
    assert ws_data == expected, "workspace JSON parity failed"


def test_build_bundle_summary_keys(tmp_workspace, tmp_path):
    """summary dict has investigations, studies, out keys."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    summary = publish.build_bundle(server.WORKSPACE, out)
    assert "investigations" in summary
    assert "studies" in summary
    assert "out" in summary
    assert "alpha" in summary["studies"]
    assert "beta" in summary["studies"]
    assert "main-inv" in summary["investigations"]


# ---------------------------------------------------------------------------
# Task 4: every asset URL in every shell resolves to a file in the bundle
# ---------------------------------------------------------------------------

def test_bundle_shell_asset_urls_resolve(tmp_workspace, tmp_path):
    """Every src/href asset URL in every shell (home + per-study) must resolve
    to an existing file in the bundle — no broken relative or root paths."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    shells = [out / "index.html"] + list(out.glob("studies/*/index.html"))
    assert shells, "no shells found"

    for shell in shells:
        html = shell.read_text()
        for m in re.finditer(r'(?:src|href)="(/[^"]+\.(?:js|css))"', html):
            url = m.group(1)
            rel = url.lstrip("/")
            candidate = out / rel
            assert candidate.is_file(), (
                f"{shell.relative_to(out)}: {url!r} does not resolve in bundle "
                f"(expected {candidate})"
            )
        # Snapshot config present in every shell
        assert 'mode: "snapshot"' in html or '"mode":"snapshot"' in html or \
               '"mode": "snapshot"' in html, f"{shell.name}: missing snapshot config"


# ---------------------------------------------------------------------------
# Task 1 (read-only viewer): export the five read resources
# ---------------------------------------------------------------------------

def test_bundle_exports_full_read_surface(tmp_workspace, tmp_path):
    """build_bundle writes api/{iset-list,catalog,composites,registry}.json
    and api/inputs/<inv>.json per investigation; iset-list parity."""
    from vivarium_dashboard import publish

    out = tmp_path / "bundle"
    publish.build_bundle(server.WORKSPACE, out)

    assert (out / "api" / "iset-list.json").is_file(), "iset-list.json missing"
    assert (out / "api" / "catalog.json").is_file(), "catalog.json missing"
    assert (out / "api" / "composites.json").is_file(), "composites.json missing"
    assert (out / "api" / "registry.json").is_file(), "registry.json missing"

    # inputs per investigation
    isets = json.loads((out / "api" / "iset-list.json").read_text())["investigations"]
    if isets:
        inv = isets[0]["name"]
        assert (out / "api" / "inputs" / f"{inv}.json").is_file(), \
            f"inputs/{inv}.json missing"

    # parity for iset-list
    assert json.loads((out / "api" / "iset-list.json").read_text()) == \
        json.loads(json.dumps(
            {"investigations": server._build_iset_summary_for_test(server.WORKSPACE)},
            default=server._json_default,
        )), "iset-list.json parity failed"


# ---------------------------------------------------------------------------
# Task 5: golden on a real workspace (v2e-invest), skipif absent
# ---------------------------------------------------------------------------

_V2E_INVEST = Path("/Users/eranagmon/code/v2e-invest")


@pytest.mark.skipif(not _V2E_INVEST.is_dir(), reason="v2e-invest not present")
def test_golden_v2e_invest(tmp_path):
    """Export the real v2e-invest workspace; assert real content + JSON parity +
    asset resolution + commit sha.  v2e-invest must stay UNTOUCHED."""
    from vivarium_dashboard import publish

    # Confirm read-only: record the workspace git status BEFORE
    import subprocess
    pre = subprocess.run(
        ["git", "-C", str(_V2E_INVEST), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    pre_dirty = pre.stdout.strip()

    out = tmp_path / "bundle"
    summary = publish.build_bundle(_V2E_INVEST, out)

    # Confirm v2e-invest is still clean
    post = subprocess.run(
        ["git", "-C", str(_V2E_INVEST), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    assert post.stdout.strip() == pre_dirty, "v2e-invest was modified!"

    # At least one investigation + one study exported
    assert len(summary["investigations"]) >= 1, "no investigations exported"
    assert len(summary["studies"]) >= 1, "no studies exported"

    # JSON parity for the first study
    slug = summary["studies"][0]
    bundle_json = json.loads((out / "api" / "study" / f"{slug}.json").read_text())

    # Temporarily set WORKSPACE to v2e-invest to call _study_detail_spec
    orig_ws = server.WORKSPACE
    server.WORKSPACE = _V2E_INVEST
    server._WP_CACHE.clear()
    try:
        api_json = json.loads(json.dumps(server._study_detail_spec(slug), default=server._json_default))
    finally:
        server.WORKSPACE = orig_ws
        server._WP_CACHE.clear()

    assert bundle_json == api_json, "JSON parity failed for real study"

    # All shell asset URLs resolve
    shells = [out / "index.html"] + list(out.glob("studies/*/index.html"))
    for shell in shells:
        html = shell.read_text()
        for m in re.finditer(r'(?:src|href)="(/[^"]+\.(?:js|css))"', html):
            url = m.group(1)
            rel = url.lstrip("/")
            assert (out / rel).is_file(), f"{shell.name}: {url!r} missing in bundle"

    # config.json has a real commit sha
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["mode"] == "snapshot"
    commit = cfg.get("commit")
    assert commit and len(commit) >= 7, f"config.commit is not a sha: {commit!r}"
