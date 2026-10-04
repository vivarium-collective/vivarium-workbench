"""Cross-engine comparison view for remote batch runs (``lib/study_comparison``,
``/api/study-results`` ``comparison`` block, ``static/comparison-view.js``).

Fixtures use the REAL state shape of a viva-biomodels run (verified against a
production runs.db): one snapshot step, NULL ``global_time``, top-level
``comparisons.<model>.<job>`` with a diagonal-free NRMSE ``matrix`` plus
``pairs`` blobs, and ``diagnostics.runs.<model>.<job>.<engine>``.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

from vivarium_workbench.lib.results_views import build_study_results

_FIXTURES = Path(__file__).parent / "_fixtures"
RUN = "viva_biomodels.composites.batch_compare_biomodels.batch-compare-biomodels__1__abc"


def _job(matrix, bucket, label, worst, engines=("copasi", "simbio", "tellurium")):
    return {
        "engines": list(engines),
        # Real states carry per-species blobs here; they dominate the size and
        # must NOT reach the payload.
        "pairs": {"copasi__simbio": {"rmse_by_species": {"A": 1e-12}, "nrmse_by_species": {"A": 1e-5}}},
        "matrix": matrix,
        "max_nrmse": max(v for row in matrix.values() for v in row.values() if isinstance(v, float) and v == v),
        "worst_pair": worst,
        "bucket": bucket,
        "bucket_label": label,
        "closeness_bucket_label": "Close (≤1)",
    }


def _state():
    close = {"copasi": {"simbio": 1e-6, "tellurium": 2e-4}, "simbio": {"copasi": 1e-6, "tellurium": 3e-4},
             "tellurium": {"copasi": 2e-4, "simbio": 3e-4}}
    far = {"copasi": {"simbio": 5.5e-6, "tellurium": 0.074}, "simbio": {"copasi": 5.5e-6, "tellurium": 0.1006},
           "tellurium": {"copasi": 0.074, "simbio": 0.1006}}
    nan_cell = {"copasi": {"simbio": float("nan"), "tellurium": 0.02}, "simbio": {"copasi": None, "tellurium": 0.02},
                "tellurium": {"copasi": 0.02, "simbio": 0.02}}
    ok = {"status": "ok", "runtime_s": 0.15, "error": "", "n_points": 1000}
    down = {"status": "unavailable", "runtime_s": 4e-7, "error": "pysces is not installed", "n_points": 1000}
    return {
        "models": {}, "results": {}, "ref_grid": {}, "viz_html": "",
        "comparisons": {
            "BIOMD0000000001": {"auto_ten_seconds": _job(close, "good", "Good (≤1%)", ["simbio", "tellurium"])},
            "BIOMD0000000002": {"auto_ten_seconds": _job(far, "large", "Large diff (>10%)", ["simbio", "tellurium"])},
            "BIOMD0000000003": {"auto_ten_seconds": _job(nan_cell, "ok", "OK (≤5%)", ["copasi", "tellurium"])},
            # A model whose engines all failed: no matrix -> nothing to compare.
            "BIOMD0000000004": {"auto_ten_seconds": {"engines": [], "pairs": {}, "matrix": {}}},
        },
        "diagnostics": {"runs": {
            "BIOMD0000000001": {"auto_ten_seconds": {"copasi": ok, "simbio": ok, "tellurium": ok, "pysces": down}},
            "BIOMD0000000002": {"auto_ten_seconds": {"copasi": ok, "simbio": ok, "tellurium": ok}},
        }, "meta": {"host": "h"}},
    }


def _make_remote_db(db_path: Path, state: dict, run_id: str = RUN) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE runs_meta (run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, label TEXT,
            params_json TEXT, started_at REAL NOT NULL, completed_at REAL, n_steps INTEGER,
            status TEXT NOT NULL, sim_name TEXT);
        CREATE TABLE history (simulation_id TEXT NOT NULL, step INTEGER NOT NULL,
            global_time REAL, state TEXT NOT NULL, PRIMARY KEY (simulation_id, step));
        """
    )
    conn.execute("INSERT INTO runs_meta VALUES (?,?,?,?,?,?,?,?,?)",
                 (run_id, "spec", "Remote run", "{}", 1.0, 2.0, 1, "completed", "compare"))
    # allow_nan so the NaN cell reaches the reader the way a cluster state can.
    conn.execute("INSERT INTO history VALUES (?,?,?,?)", (run_id, 0, None, json.dumps(state, allow_nan=True)))
    conn.commit()
    conn.close()


def _ws(tmp_path: Path, state: dict, slug: str = "demo") -> Path:
    d = tmp_path / "studies" / slug
    d.mkdir(parents=True)
    _make_remote_db(d / "runs.db", state)
    return tmp_path


def test_comparison_block_replaces_scalar_table(tmp_path):
    """Falsified if a comparison run still returned the flat scalar rows, or the
    block were missing/mis-ordered."""
    payload, status = build_study_results(_ws(tmp_path, _state()), "demo")
    assert status == 200 and payload["present"] is True
    assert payload["stores"] == []
    c = payload["comparison"]
    assert (c["n_models"], c["n_jobs"]) == (3, 3)  # the matrix-less model is skipped
    # worst disagreement first
    assert [j["model"] for j in c["jobs"]] == ["BIOMD0000000002", "BIOMD0000000003", "BIOMD0000000001"]
    worst = c["jobs"][0]
    assert worst["matrix"]["copasi"]["tellurium"] == 0.074
    assert worst["worst_pair"] == ["simbio", "tellurium"] and worst["bucket_label"] == "Large diff (>10%)"
    # buckets summarised best -> worst
    assert [b["label"] for b in c["buckets"]] == ["Good (≤1%)", "OK (≤5%)", "Large diff (>10%)"]
    assert all(b["count"] == 1 for b in c["buckets"])


def test_engine_status_and_unavailable_engines_come_from_diagnostics(tmp_path):
    """An engine that never ran (pysces) has no matrix column but must still be
    reported, with its error, from diagnostics."""
    c = build_study_results(_ws(tmp_path, _state()), "demo")[0]["comparison"]
    assert c["engines"] == ["copasi", "pysces", "simbio", "tellurium"]
    job = next(j for j in c["jobs"] if j["model"] == "BIOMD0000000001")
    assert "pysces" not in job["engines"]
    assert job["runs"]["pysces"]["status"] == "unavailable"
    assert job["runs"]["pysces"]["error"] == "pysces is not installed"
    assert job["runs"]["copasi"]["runtime_s"] == 0.15
    # a model with no diagnostics entry still renders (runs just empty)
    assert next(j for j in c["jobs"] if j["model"] == "BIOMD0000000003")["runs"] == {}


def test_payload_is_json_safe_and_drops_per_species_blobs(tmp_path):
    """Falsified if a NaN cell reached the wire (the JSON response encoder and
    the published bundle both reject it) or if ``pairs`` leaked through."""
    payload = build_study_results(_ws(tmp_path, _state()), "demo")[0]
    text = json.dumps(payload, allow_nan=False)  # raises on NaN/inf
    assert "rmse_by_species" not in text and "pairs" not in text
    nan_job = next(j for j in payload["comparison"]["jobs"] if j["model"] == "BIOMD0000000003")
    assert nan_job["matrix"]["copasi"]["simbio"] is None and nan_job["matrix"]["simbio"]["copasi"] is None
    assert nan_job["matrix"]["copasi"]["tellurium"] == 0.02


def test_state_without_comparisons_keeps_scalar_preview(tmp_path):
    """Falsified if non-comparison runs lost their scalar table."""
    state = {"metrics": {"M1": {"score": 0.25}}}
    payload = build_study_results(_ws(tmp_path, state), "demo")[0]
    assert "comparison" not in payload
    assert [s["path"] for s in payload["stores"]] == ["metrics.M1.score"]


def test_comparisons_with_nothing_comparable_fall_back_to_scalars(tmp_path):
    """Every engine failed -> no matrix anywhere -> no empty heatmap view; the
    scalar preview stays."""
    state = {"comparisons": {"M": {"j": {"engines": [], "matrix": {}, "max_nrmse": 0.5}}}}
    payload = build_study_results(_ws(tmp_path, state), "demo")[0]
    assert "comparison" not in payload
    assert any(s["path"] == "comparisons.M.j.max_nrmse" for s in payload["stores"])


def test_large_corpus_payload_stays_bounded(tmp_path):
    """1000 models x 5 engines: the block must stay a few MB at most (the real
    per-model state is ~13 MB each; none of it may ride along)."""
    mat = {a: {b: 0.01 for b in "abcde" if b != a} for a in "abcde"}
    st = {"comparisons": {f"BIOMD{i:010d}": {"j": {**_job(mat, "ok", "OK (≤5%)", ["a", "b"], tuple("abcde")),
                                                      "pairs": {"a__b": {"x": list(range(500))}}}}
                          for i in range(1000)}}
    payload = build_study_results(_ws(tmp_path, st), "demo")[0]
    assert payload["comparison"]["n_models"] == 1000
    assert len(json.dumps(payload)) < 2_000_000


def test_published_bundle_bakes_comparison_and_ships_the_view(tmp_path):
    """Snapshot mode: the baked per-study JSON carries ``comparison`` and the
    study page loads comparison-view.js (before study-detail.js, which calls it)."""
    from vivarium_workbench import publish

    ws = tmp_path / "ws"
    shutil.copytree(_FIXTURES / "ws_increase_demo", ws)
    shutil.copytree(_FIXTURES / "ws_federation_collision" / "studies" / "shared", ws / "studies" / "shared")
    _make_remote_db(ws / "studies" / "shared" / "runs.db", _state())
    out = tmp_path / "bundle"
    publish.build_bundle(ws, out)
    assert (out / "assets" / "comparison-view.js").is_file()
    baked = json.loads((out / "api" / "study-results" / "shared.json").read_text())
    assert baked["comparison"]["n_jobs"] == 3
    html = (out / "studies" / "shared" / "index.html").read_text()
    def pos(name):  # the <script src> tag, not prose mentions
        m = re.search(r'<script[^>]*\bsrc="[^"]*/' + re.escape(name) + r'[?"]', html)
        assert m, f"{name} not loaded by the published study page"
        return m.start()

    assert pos("comparison-view.js") < pos("study-detail.js")
