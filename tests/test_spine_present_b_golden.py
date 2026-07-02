"""Thread-B / Task 5: goldens for the connect-the-evidence work.

Two checks against the persisted spine data, no recompute, no AI:

1. The viz_stale lint demote (pbg-superpowers Task 4) on the REAL v2e-invest
   workspace yields at most ONE ``viz_stale_vs_latest_run`` finding per study,
   all at ``info`` severity — so it no longer counts as a gap (gaps =
   error+warning). READ-ONLY: lint_workspace_report only reads the workspace.

2. ``/api/study-observable-check`` returns per-readout validation statuses.
   Exercised via the module worker against a synthetic tmp workspace (never
   v2e-invest) so it is hermetic and writes nothing to the real workspace —
   a bogus composite ref drives the tolerated 422 path, which still returns a
   readout list where every entry carries a ``status``.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml
import pytest

_V2E = Path("/Users/eranagmon/code/v2e-invest")


@pytest.mark.skipif(not _V2E.is_dir(), reason="v2e-invest workspace not present")
def test_viz_stale_demoted_to_one_info_per_study_on_v2e_invest():
    from pbg_superpowers.report_linter import lint_workspace_report

    findings = lint_workspace_report(_V2E)  # read-only
    viz = [f for f in findings if f.check == "viz_stale_vs_latest_run"]
    # At most one viz_stale finding per study, and every one is info-severity
    # (a nudge, not a publication gap).
    per_study = Counter(f.study_slug for f in viz)
    assert all(n <= 1 for n in per_study.values()), dict(per_study)
    assert all(f.level == "info" for f in viz), [f.level for f in viz]


def test_observable_check_returns_per_readout_statuses(tmp_path):
    """The never-fabricate guard returns a status for every readout even when
    the composite can't build (tolerated 422). Synthetic workspace — hermetic."""
    from vivarium_workbench.lib.observables_views import build_study_observable_check

    ws = tmp_path / "ws"
    sdir = ws / "studies" / "s1"
    sdir.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sdir / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "s1",
        "baseline": [{"name": "core", "composite": "no.such.composite.Ref"}],
        "readouts": [
            {"name": "dnaa_count", "store_path": "bulk.DnaA"},
            {"name": "atp_frac", "store_path": "derived.atp_fraction"},
        ],
    }))

    data, status = build_study_observable_check(ws, "s1")
    assert status in (200, 422)
    readouts = data.get("readouts")
    assert isinstance(readouts, list) and len(readouts) == 2
    valid = {"ok", "unresolved", "not_in_structure", "aspirational"}
    for r in readouts:
        assert r.get("status") in valid, r
