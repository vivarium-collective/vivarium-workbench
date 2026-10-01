"""A study that belongs to multiple investigations must open in the investigation
you clicked from, not reroute to its first/primary one."""
import re
from pathlib import Path

WJ = (Path(__file__).resolve().parents[1] /
      "vivarium_workbench" / "static" / "walkthrough.js").read_text()


def _fn(name):
    m = re.search(r"function %s\([^)]*\)\s*\{" % re.escape(name), WJ)
    assert m, f"{name} not found"
    i = m.end() - 1
    depth = 0
    for j in range(i, len(WJ)):
        if WJ[j] == "{":
            depth += 1
        elif WJ[j] == "}":
            depth -= 1
            if depth == 0:
                return WJ[i:j + 1]
    raise AssertionError("unbalanced")


def test_membership_helper_exists():
    body = _fn("_studyInInvestigation")
    # membership is resolved through the iset index (now via the _isetByName map,
    # which is built from window._isetIndex) + the studies list + indexOf.
    assert "_isetByName" in body and ".studies" in body and "indexOf" in body


def test_open_prefers_current_investigation():
    body = _fn("_openStudyEmbeddedNewTab")
    # must consult the CURRENT investigation and the membership helper before
    # falling back to the first-membership lookup.
    assert "_wsInvestigation" in body
    assert "_studyInInvestigation" in body
    assert "_investigationForStudy(name)" in body
    # the preference expression: current-if-member else first
    assert re.search(r"_studyInInvestigation\([^)]*\)\s*\?\s*\w+\s*:\s*_investigationForStudy", body)
