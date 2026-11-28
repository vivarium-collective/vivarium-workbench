"""Regression: the investigation-detail Decisions tab must emit balanced <div>s.

The brief concatenates every tab panel into one `.inv-brief-panels` container, so
`_invDecisionTabHtml` (the Decisions panel body) leaking an open <div> makes the
browser re-parent the panels that FOLLOW it (Validation, Needs attention) inside
the decision card — they collapse to zero height and render blank. A
reconciliation card used to open three wrappers and close only two, leaking one
<div> per `reconciliation_log` item.

walkthrough.js isn't node-requirable (full-DOM, no exports), so the balance check
lifts the pure builder via brace-matching and runs it under node (see
tests/js/test_inv_decision_tab_balance.js). A marker assertion pins the fix in
the source so a careless re-edit is caught even without node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "vivarium_workbench/static"


def test_reconciliation_card_closes_all_three_wrappers():
    js = (STATIC / "walkthrough.js").read_text()
    # The reconciliation card opens .eng-decision + the flex row + the flex:1
    # column; it must close all three. Pin the balanced close next to the
    # reconciliation resolve button so a regression to two is caught in-source.
    i_recon = js.index("_resolveEngineReconciliation(this)")
    tail = js[i_recon:i_recon + 1200]
    assert "</div></div></div>" in tail, (
        "the reconciliation card must close .eng-decision + flex + flex:1 "
        "(</div></div></div>); closing only two leaks a <div> and blanks the "
        "Validation / Needs-attention tabs"
    )


def test_inv_decision_tab_div_balance_node():
    node = shutil.which("node")
    if not node:  # pragma: no cover - CI always has node for the JS suite
        pytest.skip("node not available")
    subprocess.run([node, str(ROOT / "tests/js/test_inv_decision_tab_balance.js")],
                   check=True)
