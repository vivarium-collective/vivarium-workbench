from pbg_superpowers import study_status


def _passed(parent):
    counts = study_status.count_test_outcomes(parent, parent.get("runs"))
    return counts["fail"] == 0 and counts["pass"] > 0


def test_gate_agrees_with_pills_on_canonical_run():
    # canonical run PASSes both; a later scratch run FAILs one.
    parent = {
        "tests": [{"name": "t1"}, {"name": "t2"}],
        "runs": [
            {"name": "canon", "status": "completed", "canonical": True,
             "outcomes": {"t1": {"result": "PASS"}, "t2": {"result": "PASS"}}},
            {"name": "scratch", "status": "completed",
             "outcomes": {"t1": {"result": "FAIL"}}},
        ],
    }
    # gate and pills must agree: both read the canonical run
    assert _passed(parent) is True
