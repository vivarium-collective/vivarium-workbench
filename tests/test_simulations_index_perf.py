"""/api/simulations index cost: YAML parse counts, single-flight, freshness.

The Runs page's ``GET /api/simulations`` spent ~27 s of a 28 s build in
``yaml.safe_load`` on the dev workbench (93 runs, 113 study.yaml -> 524 parses
per ``list_simulations``, twice per request), and overlapping requests each
redid it. These tests pin the fix by COUNTING (parses, builds), never by
timing, so they are not flaky:

* the rows are identical to the pre-change behaviour (memo off, pure-Python
  SafeLoader) on a synthetic workspace with many studies and runs;
* one request parses each YAML file at most once (not runs x studies);
* concurrent identical requests share one build;
* an edited study.yaml is picked up by the next build.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import threading
import time
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import simulations_index as si
from vivarium_workbench.lib import yaml_io
from vivarium_workbench.lib.composite_runs import connect, save_metadata

N_INVESTIGATIONS = 6
STUDIES_PER_INV = 6          # 36 studies
RUNS_PER_STUDY = 4           # ~144 study.yaml runs
PAD_CONDITIONS = 40          # makes each study.yaml a few KB, like real ones


@pytest.fixture(autouse=True)
def _isolate_build_cache():
    si.clear_build_cache()
    yield
    si.clear_build_cache()


def _make_workspace(ws: Path) -> dict:
    """A flat + nested mixed-ownership workspace. Returns counts of YAML files."""
    ws.mkdir(parents=True)
    n_study_yaml = n_inv_yaml = 0
    for i in range(N_INVESTIGATIONS):
        inv = f"inv-{i}"
        inv_dir = ws / "investigations" / inv
        inv_dir.mkdir(parents=True)
        members = []
        for j in range(STUDIES_PER_INV):
            slug = f"s{i}-{j}"
            nested = (j % 3 == 2)          # some studies live under the investigation
            backref = (j % 3 == 1)         # some declare `investigation:` themselves
            sdir = (inv_dir / "studies" / slug) if nested else (ws / "studies" / slug)
            sdir.mkdir(parents=True)
            if not nested and not backref:
                members.append(slug if j % 2 else {"study": slug})  # forward-list only
            spec: dict = {
                "name": slug,
                "conditions": {"baseline": {"composite": f"pkg.comp_{i}",
                                            "params": {"k": i, "rate": 0.5 + j}}},
                "notes": [{"id": n, "text": "x" * 60, "params": {"a": n, "b": [n, n + 1]}}
                          for n in range(PAD_CONDITIONS)],
                "runs": [],
            }
            if backref:
                spec["investigation"] = inv
            for r in range(RUNS_PER_STUDY):
                entry: dict = {"name": f"run-{r}", "status": "completed",
                               "timestamp": f"2026-09-{(r % 27) + 1:02d}T10:00:00",
                               "n_steps": 10 + r, "params": {"seed": r}}
                if r == 0:
                    entry["kind"] = "synthesis"
                spec["runs"].append(entry)
            # Cross-study reference: every study also names a shared run id.
            spec["runs"].append(f"shared-{i}")
            if j == 0:
                # A study with its own run store (runs.db) whose runs are global ids.
                conn = connect(sdir / "runs.db")
                for r in range(2):
                    save_metadata(conn, spec_id=f"pkg.comp_{i}", run_id=f"db-{i}-{r}",
                                  params={"r": r}, label="", started_at=1000.0 + 10 * i + r,
                                  n_steps=3, log_path=None)
                conn.close()
                spec["runs"].append({"name": f"db-{i}-0"})
            (sdir / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False),
                                             encoding="utf-8")
            n_study_yaml += 1
        (inv_dir / "investigation.yaml").write_text(
            yaml.safe_dump({"name": inv, "studies": members}), encoding="utf-8")
        n_inv_yaml += 1
    return {"study": n_study_yaml, "inv": n_inv_yaml}


@contextlib.contextmanager
def _pre_change_yaml(monkeypatch):
    """The behaviour before this change: no parse memo, pure-Python SafeLoader."""
    with monkeypatch.context() as m:
        m.setattr(yaml_io, "parse_scope", contextlib.nullcontext)
        m.setattr(yaml_io, "SafeLoader", yaml.SafeLoader)
        yield


def _canon(obj, ws: Path) -> str:
    return json.dumps(obj, sort_keys=True, default=str).replace(str(ws.resolve()), "<WS>") \
        .replace(str(ws), "<WS>")


def test_list_simulations_identical_to_pre_change(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _make_workspace(ws)
    new = si.list_simulations(ws)
    with _pre_change_yaml(monkeypatch):
        old = si.list_simulations(ws)
    # (store-backed studies' runs are global ids, so a few collide and merge)
    assert len(new) > 100
    assert _canon(new, ws) == _canon(old, ws)
    # Ownership came through all three routes (nested path, back-ref, forward list).
    owners = {r["study_slug"]: r["investigation_slug"] for r in new if r.get("study_slug")}
    assert owners["s0-0"] == "inv-0"   # forward list
    assert owners["s0-1"] == "inv-0"   # back-ref
    assert owners["s0-2"] == "inv-0"   # nested


def test_build_simulations_data_identical_to_pre_change(tmp_path, monkeypatch):
    # build_simulations_data appends to the workspace's JSONL run log, so each
    # side gets its own copy of the same workspace.
    a, b = tmp_path / "a" / "ws", tmp_path / "b" / "ws"
    _make_workspace(a)
    shutil.copytree(a, b)
    new = si.build_simulations_data(a, include_remote=False)
    with _pre_change_yaml(monkeypatch):
        old = si.build_simulations_data(b, include_remote=False)
    assert new["simulations"]
    assert _canon(new, a) == _canon(old, b)
    log_a = (a / ".pbg" / "runs.jsonl").read_text().splitlines()
    log_b = (b / ".pbg" / "runs.jsonl").read_text().splitlines()
    assert len(log_a) == len(log_b)


def test_list_simulations_parses_each_yaml_file_at_most_once(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    counts = _make_workspace(ws)
    n_files = counts["study"] + counts["inv"]

    before = yaml_io._parse_count
    rows = si.list_simulations(ws)
    parses = yaml_io._parse_count - before
    assert parses <= n_files, (parses, n_files)

    # The pre-change path parses per run per reader (runs x studies-ish).
    with _pre_change_yaml(monkeypatch):
        before = yaml_io._parse_count
        si.list_simulations(ws)
        old_parses = yaml_io._parse_count - before
    assert old_parses > 3 * n_files, (old_parses, n_files, len(rows))


def test_build_parses_each_yaml_file_at_most_once(tmp_path):
    """The whole request, including the nested build that analysis-tool
    matching triggers, shares one parse scope and one backfill scan."""
    ws = tmp_path / "ws"
    counts = _make_workspace(ws)
    n_files = counts["study"] + counts["inv"]
    si.build_simulations_data(ws, include_remote=False)  # first build backfills the log

    calls = {"backfill": 0}
    real_backfill = si.backfill_index_into_jsonl

    def counting_backfill(ws_root):
        calls["backfill"] += 1
        return real_backfill(ws_root)

    before = yaml_io._parse_count
    with pytest.MonkeyPatch.context() as m:
        m.setattr(si, "backfill_index_into_jsonl", counting_backfill)
        si.build_simulations_data(ws, include_remote=False)
    parses = yaml_io._parse_count - before
    assert parses <= n_files, (parses, n_files)
    assert calls["backfill"] == 1


def test_concurrent_identical_requests_build_once(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _make_workspace(ws)
    real_build = si.build_simulations_data
    calls = {"n": 0}
    started = threading.Event()

    def slow_counting_build(ws_root, include_remote=True, fresh=False):
        calls["n"] += 1
        started.set()
        time.sleep(0.3)   # hold the flight open so the others must join it
        return real_build(ws_root, include_remote=include_remote, fresh=fresh)

    monkeypatch.setattr(si, "build_simulations_data", slow_counting_build)
    results: list = []
    errors: list = []

    def worker():
        try:
            results.append(si.build_simulations_data_cached(ws, include_remote=False))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    leader = threading.Thread(target=worker)
    leader.start()
    assert started.wait(5)
    followers = [threading.Thread(target=worker) for _ in range(7)]
    for t in followers:
        t.start()
    for t in [leader, *followers]:
        t.join(30)
    assert not errors
    assert calls["n"] == 1
    assert len(results) == 8 and all(r is results[0] for r in results)
    # ...and the result is cached for the next caller.
    assert si.build_simulations_data_cached(ws, include_remote=False) is results[0]
    assert calls["n"] == 1


def test_single_flight_propagates_errors_without_caching(monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def failing_build(ws_root, include_remote=True, fresh=False):
        calls["n"] += 1
        started.set()
        release.wait(5)
        raise RuntimeError("boom")

    monkeypatch.setattr(si, "build_simulations_data", failing_build)
    errs: list = []

    def worker():
        try:
            si.build_simulations_data_cached(tmp_path, include_remote=False)
        except RuntimeError as e:
            errs.append(e)

    threads = [threading.Thread(target=worker)]
    threads[0].start()
    assert started.wait(5)
    threads += [threading.Thread(target=worker) for _ in range(3)]
    for t in threads[1:]:
        t.start()
    time.sleep(0.1)
    release.set()
    for t in threads:
        t.join(10)
    assert len(errs) == 4 and calls["n"] == 1
    # Not cached: the next call builds again.
    monkeypatch.setattr(si, "build_simulations_data",
                        lambda *a, **k: {"simulations": [], "current": None})
    assert si.build_simulations_data_cached(tmp_path, include_remote=False) == \
        {"simulations": [], "current": None}


def test_refresh_does_not_join_a_stale_flight_nor_let_it_repopulate(monkeypatch, tmp_path):
    """?refresh=true clears the cache and must not be answered by (or later be
    overwritten by) a build that started before it."""
    started = threading.Event()
    release = threading.Event()
    seq = {"n": 0}

    def build(ws_root, include_remote=True, fresh=False):
        seq["n"] += 1
        n = seq["n"]
        if not fresh:
            started.set()
            release.wait(5)
        return {"simulations": [], "current": None, "n": n, "fresh": fresh}

    monkeypatch.setattr(si, "build_simulations_data", build)
    out: dict = {}
    t = threading.Thread(target=lambda: out.setdefault(
        "stale", si.build_simulations_data_cached(tmp_path, include_remote=False)))
    t.start()
    assert started.wait(5)
    si.clear_build_cache()                       # what ?refresh=true does first
    fresh = si.build_simulations_data_cached(tmp_path, include_remote=False, fresh=True)
    assert fresh["fresh"] is True
    release.set()
    t.join(10)
    assert out["stale"]["fresh"] is False
    # The cache holds the fresh result, not the stale one that finished later.
    assert si.build_simulations_data_cached(tmp_path, include_remote=False)["n"] == fresh["n"]


def test_edited_study_yaml_is_picked_up(tmp_path):
    ws = tmp_path / "ws"
    _make_workspace(ws)
    ids = {r["run_id"] for r in si.list_simulations(ws)}
    assert "s0-1:brand-new" not in ids

    p = ws / "studies" / "s0-1" / "study.yaml"
    spec = yaml.safe_load(p.read_text(encoding="utf-8"))
    spec["runs"].append({"name": "brand-new", "status": "completed"})
    p.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    assert "s0-1:brand-new" in {r["run_id"] for r in si.list_simulations(ws)}
    data = si.build_simulations_data_cached(ws, include_remote=False, ttl=0)
    assert "s0-1:brand-new" in {r["run_id"] for r in data["simulations"]}

    # And through the TTL cache: ?refresh=true (clear_build_cache) sees an edit.
    cached = si.build_simulations_data_cached(ws, include_remote=False)
    spec["runs"].append({"name": "newer", "status": "completed"})
    p.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    assert si.build_simulations_data_cached(ws, include_remote=False) is cached
    si.clear_build_cache()
    data = si.build_simulations_data_cached(ws, include_remote=False)
    assert "s0-1:newer" in {r["run_id"] for r in data["simulations"]}
