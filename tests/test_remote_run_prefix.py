"""Pure-core pins of step P3 I1 (no plugin import; runs in core-only mode): the ``remote-sim-`` run-id prefix is one
core constant, and the study page accepts both refusal literals. The plugin-side pins and the wire goldens are in
``test_remote_ids_goldens.py``.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_core_spells_the_remote_run_prefix_only_in_ids():
    """The ``remote-sim-`` prefix is one core constant (``lib/backends/ids.py``); no other core string constant
    (docstrings included) spells it."""
    core = ROOT / "vivarium_workbench"
    found = []
    for path in core.rglob("*.py"):
        rel = path.relative_to(core).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "remote-sim-" in node.value:
                found.append(rel)
    assert sorted(found) == ["lib/backends/ids.py"]


def test_the_prefix_is_the_external_contract():
    from vivarium_workbench.lib.backends.ids import REMOTE_RUN_PREFIX, remote_run_id

    assert REMOTE_RUN_PREFIX == "remote-sim-"
    assert remote_run_id(9) == "remote-sim-9" and remote_run_id("simulation-V") == "remote-sim-simulation-V"


def test_the_study_page_reads_the_not_offered_refusal():
    """The browser reads ``not-offered`` as a terminal refusal in both places it reads the refusal phase. The pre-I1
    ``not-sms`` literal it also accepted until P3 Z is gone: nothing emits it any more."""
    detail = (ROOT / "vivarium_workbench" / "static" / "study-detail.js").read_text()
    assert "d.phase === 'other-backend' || d.phase === 'not-offered')" in detail
    assert "d.phase !== 'other-backend' && d.phase !== 'not-offered')" in detail
    assert "not-sms" not in detail
