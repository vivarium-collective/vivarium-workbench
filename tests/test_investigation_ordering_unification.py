"""One canonical study-ordering relation across run / composite / rerun.

Before this, the three paths that order an investigation's member studies read
two DIFFERENT edge sources: run_jobs ('Run unblocked') and the composite builder
ordered by ``pipeline_gate.prerequisites``; ``rerun`` ordered by the
``inputs[].from`` data DAG. So a build that declares prerequisites but no
inputs.from (viva-genesis: og-01/og-02 do exactly this) was ordered correctly on
Run and in flat declared order on rerun — it silently dropped the prerequisites.

These pin the fix: ``study_upstreams`` is the UNION of both sources, and every
ordering/gating decision reads through it, so Run, composite, and rerun can no
longer disagree about what must run before what.
"""
from __future__ import annotations

import yaml

from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def _ws(tmp_path, studies: dict[str, dict]):
    """studies: {slug: {"prereqs": [...], "inputs_from": [...]}} (both optional)."""
    (tmp_path / "workspace.yaml").write_text("name: order-ws\n")
    ws = WorkspacePaths.load(tmp_path)
    ws.studies.mkdir(parents=True, exist_ok=True)
    for slug, cfg in studies.items():
        d = ws.studies / slug
        d.mkdir(parents=True, exist_ok=True)
        spec: dict = {"name": slug}
        if cfg.get("prereqs") is not None:
            spec["pipeline_gate"] = {"prerequisites": list(cfg["prereqs"])}
        if cfg.get("inputs_from") is not None:
            spec["inputs"] = [{"from": u} for u in cfg["inputs_from"]]
        (d / "study.yaml").write_text(yaml.safe_dump(spec))
    return ws


# --- the canonical readers ------------------------------------------------- #

def test_study_inputs_from_reads_the_data_dag(tmp_path):
    from vivarium_workbench.lib.run_jobs import study_inputs_from

    ws = _ws(tmp_path, {"b": {"inputs_from": ["a", "x"]}})
    assert study_inputs_from(ws, "b") == ["a", "x"]


def test_study_inputs_from_total_on_missing(tmp_path):
    from vivarium_workbench.lib.run_jobs import study_inputs_from

    ws = _ws(tmp_path, {"a": {}})
    assert study_inputs_from(ws, "nonexistent") == []


def test_study_upstreams_is_the_union_deduped_prereqs_first(tmp_path):
    from vivarium_workbench.lib.run_jobs import study_upstreams

    ws = _ws(tmp_path, {"c": {"prereqs": ["a"], "inputs_from": ["a", "b"]}})
    # 'a' appears in both and must not double; prerequisites come first.
    assert study_upstreams(ws, "c") == ["a", "b"]


def test_study_upstreams_prereqs_only(tmp_path):
    from vivarium_workbench.lib.run_jobs import study_upstreams

    ws = _ws(tmp_path, {"b": {"prereqs": ["a"]}})
    assert study_upstreams(ws, "b") == ["a"]


def test_study_upstreams_inputs_from_only(tmp_path):
    from vivarium_workbench.lib.run_jobs import study_upstreams

    ws = _ws(tmp_path, {"b": {"inputs_from": ["a"]}})
    assert study_upstreams(ws, "b") == ["a"]


# --- the behavior change: every path honors the union ---------------------- #

def test_run_ordering_now_honors_an_inputs_from_edge(tmp_path):
    """order_items_by_prereqs ordered by prerequisites only; a dependent wired
    purely through inputs.from (no pipeline_gate) was NOT ordered after its
    producer. The union fixes it."""
    from vivarium_workbench.lib.run_jobs import order_items_by_prereqs

    ws = _ws(tmp_path, {"a": {}, "b": {"inputs_from": ["a"]}})
    items = [{"study": "b", "variant": "baseline"}, {"study": "a", "variant": "baseline"}]
    assert [it["study"] for it in order_items_by_prereqs(items, ws)] == ["a", "b"]


def test_rerun_order_now_honors_prerequisites(tmp_path):
    """rerun ordered by inputs.from only; members declaring prerequisites but no
    inputs.from (the viva-genesis shape) were replayed in flat declared order.
    The union fixes it — the dependent is moved after its prerequisite."""
    from vivarium_workbench.lib.rerun import _investigation_execution_order

    ws_root = tmp_path
    _ws(tmp_path, {"a": {}, "b": {"prereqs": ["a"]}})
    # investigation.yaml declares the dependent first.
    ws = WorkspacePaths.load(tmp_path)
    ws.investigations.mkdir(parents=True, exist_ok=True)
    (ws.investigations / "inv").mkdir(parents=True, exist_ok=True)
    (ws.investigations / "inv" / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "inv", "members": ["b", "a"]}))
    assert _investigation_execution_order(ws_root, "inv", ["b", "a"]) == ["a", "b"]
