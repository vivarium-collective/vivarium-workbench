"""Federation parity for the STUDY read builders (mirrors #1164's investigation
DETAIL fix). Before this fix, a federated study -- one shipped by a linked
workspace under ``external/<repo>/`` and surfaced read-only by the
federation-aware LIST builders (``federation.federated_studies``) -- 404'd on
every read/action builder that only ever looked under the host's own
``studies/``/``investigations/`` dirs: study detail, the standalone study
report, on-demand grading, and the study zip export. "Shows in the list,
404s on click."

Reuses the checked-in ``ws_federation_demo`` fixture (also used by
``test_federation.py`` / the #1164 investigation-detail test): a host
workspace with NO ``studies/`` dir of its own and a linked workspace at
``external/donor/`` (``workspace.yaml`` name ``donor-repo``) shipping
``studies/donor_study/study.yaml``. A second, richer fixture is built ad hoc
(mirrors ``test_build_iset_detail_native_unaffected``'s in-test tmp_path
construction) with a study that has real ``runs[].outcomes`` and a rendered
viz HTML file, to prove content -- not just the bare spec -- resolves.
"""
from __future__ import annotations

from pathlib import Path

import yaml as _yaml

from vivarium_workbench.lib.study_spec import load_study_detail_spec
from vivarium_workbench.lib.single_study_report import _load_study_spec, _collect_viz_html
from vivarium_workbench.lib.study_grade import grade_study
from vivarium_workbench.lib.download_views import build_study_export, DownloadError

FIX = Path(__file__).parent / "_fixtures" / "ws_federation_demo"


def _make_rich_federated_workspace(tmp_path: Path) -> Path:
    """Host workspace + linked workspace under external/rich-donor/ shipping a
    study with a real graded run (runs[].outcomes) and a rendered viz HTML
    file -- so content resolution (not just the bare spec) can be verified.
    """
    (tmp_path / "workspace.yaml").write_text("name: host\npackage_path: host\n")

    donor_root = tmp_path / "external" / "rich-donor"
    (donor_root).mkdir(parents=True)
    (donor_root / "workspace.yaml").write_text(
        _yaml.safe_dump({"name": "rich-donor-repo", "package_path": "rich_donor"})
    )
    sdir = donor_root / "studies" / "rich_study"
    sdir.mkdir(parents=True)
    (sdir / "study.yaml").write_text(_yaml.safe_dump({
        "name": "rich_study",
        "description": "A donor study with a graded run.",
        "composite": "rich_donor.composites.rich",
        "runs": [{
            "run_id": "run1",
            "name": "run1",
            "status": "completed",
            "outcomes": {
                "test_a": {"result": "PASS"},
                "test_b": {"result": "FAIL"},
            },
        }],
    }))
    viz_dir = sdir / "viz"
    viz_dir.mkdir()
    (viz_dir / "chart.html").write_text("<html><body>donor chart</body></html>")
    rc_dir = viz_dir / "report_card"
    rc_dir.mkdir()
    (rc_dir / "mycard.html").write_text("<html><body>donor report card</body></html>")
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Study DETAIL -- study_spec.load_study_detail_spec
# ---------------------------------------------------------------------------

def test_load_study_detail_spec_resolves_federated_study():
    spec = load_study_detail_spec(FIX, "donor_study")
    assert spec is not None
    assert spec["name"] == "donor_study"
    assert spec["read_only"] is True
    assert spec["origin_repo"] == "donor-repo"


def test_load_study_detail_spec_resolves_qualified_slug():
    """A `<repo>::<name>` id (as listed) also resolves, scoped to that repo --
    even though the HTTP route's SLUG_RE rejects `::` and click-through in
    practice always passes the bare name (per #1164)."""
    spec = load_study_detail_spec(FIX, "donor-repo::donor_study")
    assert spec is not None
    assert spec["read_only"] is True


def test_load_study_detail_spec_native_unaffected(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: host\npackage_path: host\n")
    sdir = tmp_path / "studies" / "native_study"
    sdir.mkdir(parents=True)
    (sdir / "study.yaml").write_text(
        _yaml.safe_dump({"name": "native_study", "composite": "host.composites.native"})
    )
    spec = load_study_detail_spec(tmp_path, "native_study")
    assert spec is not None
    assert spec["read_only"] is False
    assert spec["origin_repo"] is None


def test_load_study_detail_spec_absent_still_none():
    assert load_study_detail_spec(FIX, "does-not-exist-anywhere") is None


def test_load_study_detail_spec_resolves_report_card_for_federated_content(tmp_path):
    """The report-card url block (rc_dir = resolved_dir / "viz" / "report_card")
    is resolved against the linked workspace too, not just the bare spec --
    proving `resolved_dir` (not just `spec_path`) carries through the rest of
    the function. (Auto-discovered viz via runs.db stays host-scoped -- that's
    run-path territory, explicitly out of scope here.)"""
    ws = _make_rich_federated_workspace(tmp_path)
    spec = load_study_detail_spec(ws, "rich_study")
    assert spec is not None
    assert spec["read_only"] is True
    assert spec["origin_repo"] == "rich-donor-repo"
    rc_urls = spec.get("report_card_urls") or {}
    assert "mycard" in rc_urls
    assert rc_urls["mycard"]["url"].startswith("/external/rich-donor/")


# ---------------------------------------------------------------------------
# 2. Study REPORT -- single_study_report._load_study_spec / _collect_viz_html
# ---------------------------------------------------------------------------

def test_single_study_report_load_spec_resolves_federated_study():
    spec = _load_study_spec(FIX, "donor_study")
    assert spec["name"] == "donor_study"


def test_single_study_report_load_spec_absent_still_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        _load_study_spec(FIX, "does-not-exist-anywhere")


def test_single_study_report_collect_viz_html_resolves_federated_content(tmp_path):
    ws = _make_rich_federated_workspace(tmp_path)
    entries = _collect_viz_html(ws, "rich_study")
    assert len(entries) == 1
    assert entries[0]["name"] == "chart"
    assert "donor chart" in entries[0]["html"]


def test_single_study_report_collect_viz_html_absent_returns_empty():
    assert _collect_viz_html(FIX, "does-not-exist-anywhere") == []


# ---------------------------------------------------------------------------
# 3. Study grade -- study_grade.grade_study
# ---------------------------------------------------------------------------

def test_grade_study_resolves_federated_study_read_only():
    body, status = grade_study(FIX, "donor_study")
    assert status == 200
    assert body["read_only"] is True
    assert body["origin_repo"] == "donor-repo"


def test_grade_study_reads_persisted_outcomes_without_writing(tmp_path):
    ws = _make_rich_federated_workspace(tmp_path)
    spec_path = ws / "external" / "rich-donor" / "studies" / "rich_study" / "study.yaml"
    before = spec_path.read_text(encoding="utf-8")
    before_mtime = spec_path.stat().st_mtime_ns

    body, status = grade_study(ws, "rich_study")

    assert status == 200
    assert body["read_only"] is True
    assert body["outcome_rollup"] == {"PASS": 1, "FAIL": 1, "SKIP": 0, "total": 2}
    assert body["graded"] is True
    # Grading a federated study must never write into the linked workspace.
    assert spec_path.read_text(encoding="utf-8") == before
    assert spec_path.stat().st_mtime_ns == before_mtime


def test_grade_study_absent_still_404():
    body, status = grade_study(FIX, "does-not-exist-anywhere")
    assert status == 404
    assert "error" in body


# ---------------------------------------------------------------------------
# 4. Download / export -- download_views.build_study_export
# ---------------------------------------------------------------------------

def test_build_study_export_resolves_federated_study():
    data, content_type, filename = build_study_export(FIX, "donor_study")
    assert content_type == "application/zip"
    assert filename == "donor_study.zip"
    assert len(data) > 0

    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    assert any(n.endswith("study.yaml") for n in names)


def test_build_study_export_absent_still_404():
    import pytest
    with pytest.raises(DownloadError) as excinfo:
        build_study_export(FIX, "does-not-exist-anywhere")
    assert excinfo.value.status == 404
