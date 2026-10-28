"""Gate-class rigor split — pins vs acceptance criteria vs expected-fail controls.

viva-superpowers (PR #285) distinguishes, on each ``behavior_tests[]`` entry:

* ``gate_class: regression_pin`` — a pin that freezes known-good behavior;
* ``gate_class: acceptance_criterion`` — a forward-looking scientific gate;
* expected-fail controls — recognized by ANY of the three markers
  ``expected_result: fail`` / ``classification: diagnostic`` /
  ``control: negative`` — whose *correct* behavior is to FAIL.

Its ``study_verdict.verdict_count_split(spec)`` returns the split verdict
ledger::

    {regression_pins: {total, pass, fail},
     acceptance_criteria: {total, pass, fail},
     expected_fail: {total, behaved},
     narrated, unclassified, committed_rerunnable, label}

so a report can say "pins 2/2 · acceptance 1/2 · expected-fail behaved 1/1"
instead of one conflated "gate: passed".

Every public function here DELEGATES to ``viva_superpowers.study_verdict``
when the corresponding function is importable (i.e. once #285 lands in the
installed viva-superpowers), and otherwise mirrors the same classification
locally from the spec fields — expected-fail markers checked FIRST so a
negative control is never counted as an acceptance pass. Pure; no I/O; never
raises (tolerant of malformed specs).
"""
from __future__ import annotations

# The two recognized gate classes (viva-superpowers #285 vocabulary).
GATE_CLASSES = ("regression_pin", "acceptance_criterion")


def _outcomes_by_test(spec: dict) -> dict:
    """Merge per-run ``outcomes`` keyed by test name; a ``canonical`` run wins.

    Mirrors ``behavior_test_card._outcomes_by_test`` (duplicated here rather
    than imported so ``behavior_test_card`` can import this module without a
    cycle).
    """
    merged: dict = {}
    for run in (spec.get("runs") or []) if isinstance(spec, dict) else []:
        if not isinstance(run, dict):
            continue
        oc = run.get("outcomes")
        if not isinstance(oc, dict):
            continue
        canonical = bool(run.get("canonical"))
        for tname, o in oc.items():
            if canonical or tname not in merged:
                merged[tname] = o if isinstance(o, dict) else {}
    return merged


def is_expected_fail(test) -> bool:
    """True when a behavior test is an expected-fail control.

    Recognized via ANY of the three #285 markers:
    ``expected_result: fail`` OR ``classification: diagnostic`` OR
    ``control: negative``. Delegates to viva_superpowers when available.
    """
    try:
        from discovery_engine.study_verdict import is_expected_fail as _ief
    except ImportError:
        pass  # local mirror below — can delegate once viva-superpowers #285 lands
    else:
        try:
            return bool(_ief(test))
        except Exception:  # noqa: BLE001 — fall back to the local mirror
            pass
    if not isinstance(test, dict):
        return False
    if str(test.get("expected_result") or "").strip().lower() == "fail":
        return True
    if str(test.get("classification") or "").strip().lower() == "diagnostic":
        return True
    if str(test.get("control") or "").strip().lower() == "negative":
        return True
    return False


def test_gate_class(test) -> str | None:
    """The normalized ``gate_class`` of one behavior test, or None.

    Returns ``"regression_pin"`` / ``"acceptance_criterion"`` when declared
    (tolerant of dash spelling), None when absent or unrecognized. An
    expected-fail control (see :func:`is_expected_fail`) never gets a gate
    class — the markers are checked first so a control can never be counted
    as an acceptance pass.
    """
    if not isinstance(test, dict) or is_expected_fail(test):
        return None
    v = str(test.get("gate_class") or "").strip().lower().replace("-", "_")
    return v if v in GATE_CLASSES else None


def classify_gates(spec: dict) -> dict:
    """Bucket a study's behavior tests by gate class.

    Returns ``{regression_pins: [test, ...], acceptance_criteria: [...],
    expected_fail: [...], unclassified: [...]}`` — each value the test dicts
    themselves, in declaration order. Expected-fail markers are checked FIRST,
    so a control never lands in a gate_class bucket. Delegates to
    viva_superpowers when available.
    """
    try:
        from discovery_engine.study_verdict import classify_gates as _cg
    except ImportError:
        pass  # local mirror below — can delegate once viva-superpowers #285 lands
    else:
        try:
            out = _cg(spec)
            if isinstance(out, dict):
                return out
        except Exception:  # noqa: BLE001
            pass
    return _classify_local(spec)


def _classify_local(spec) -> dict:
    """Local mirror of the #285 gate classification (shape documented on
    :func:`classify_gates`). Used by the local count-split too, so a partially
    upgraded viva-superpowers can never feed it a foreign bucket shape."""
    buckets: dict = {"regression_pins": [], "acceptance_criteria": [],
                     "expected_fail": [], "unclassified": []}
    spec = spec if isinstance(spec, dict) else {}
    tests = [t for t in (spec.get("behavior_tests") or spec.get("expected_behavior") or [])
             if isinstance(t, dict)]
    for t in tests:
        if is_expected_fail(t):
            buckets["expected_fail"].append(t)
        elif test_gate_class(t) == "regression_pin":
            buckets["regression_pins"].append(t)
        elif test_gate_class(t) == "acceptance_criterion":
            buckets["acceptance_criteria"].append(t)
        else:
            buckets["unclassified"].append(t)
    return buckets


# Run statuses that count as committed/rerunnable evidence (kept in sync with
# study_spec._COMPLETE_STATUSES).
_COMPLETE_STATUSES = {"complete", "completed", "ran", "done"}


def _is_narrated(test: dict) -> bool:
    """A narrative-only expectation: no machine-checkable measure or band."""
    return not (test.get("measure") or test.get("pass_if"))


def verdict_count_split(spec: dict) -> dict:
    """The split verdict ledger — pins vs acceptance vs expected-fail.

    Returns::

        {regression_pins:    {total, pass, fail},
         acceptance_criteria: {total, pass, fail},
         expected_fail:       {total, behaved},
         narrated: int, unclassified: int,
         committed_rerunnable: bool,
         label: str}

    ``behaved`` counts expected-fail controls whose canonical result IS
    ``FAIL`` (the control failed exactly as designed). ``label`` is the
    compact human string, e.g. ``"pins 2/2 · acceptance 1/2 · expected-fail
    behaved 1/1"``. Delegates to ``viva_superpowers.study_verdict`` when its
    ``verdict_count_split`` is importable; otherwise mirrors the same buckets
    locally (can delegate fully once viva-superpowers #285 lands).
    """
    try:
        from discovery_engine.study_verdict import verdict_count_split as _vcs
    except ImportError:
        pass  # local mirror below — can delegate once viva-superpowers #285 lands
    else:
        try:
            out = _vcs(spec)
            if isinstance(out, dict):
                return out
        except Exception:  # noqa: BLE001
            pass

    spec = spec if isinstance(spec, dict) else {}
    outcomes = _outcomes_by_test(spec)
    buckets = _classify_local(spec)

    def _result(t: dict) -> str:
        o = outcomes.get(str(t.get("name") or "")) or {}
        return str(o.get("result") or "PENDING").upper()

    def _tally(tests: list) -> dict:
        results = [_result(t) for t in tests]
        return {
            "total": len(results),
            "pass": sum(1 for r in results if r == "PASS"),
            "fail": sum(1 for r in results if r == "FAIL"),
        }

    pins = _tally(buckets.get("regression_pins") or [])
    acceptance = _tally(buckets.get("acceptance_criteria") or [])
    ef_tests = buckets.get("expected_fail") or []
    expected_fail = {
        "total": len(ef_tests),
        # An expected-fail control BEHAVES by failing.
        "behaved": sum(1 for t in ef_tests if _result(t) == "FAIL"),
    }
    other = buckets.get("unclassified") or []
    narrated = sum(1 for t in other if _is_narrated(t))
    unclassified = len(other) - narrated

    committed_rerunnable = any(
        isinstance(r, dict)
        and str(r.get("status") or "").strip().lower() in _COMPLETE_STATUSES
        for r in (spec.get("runs") or [])
    )

    parts = []
    if pins["total"]:
        parts.append(f'pins {pins["pass"]}/{pins["total"]}')
    if acceptance["total"]:
        parts.append(f'acceptance {acceptance["pass"]}/{acceptance["total"]}')
    if expected_fail["total"]:
        parts.append(
            f'expected-fail behaved {expected_fail["behaved"]}/{expected_fail["total"]}')
    if unclassified:
        parts.append(f"{unclassified} unclassified")
    if narrated:
        parts.append(f"{narrated} narrated")
    label = " · ".join(parts) if parts else "no classified gates"

    return {
        "regression_pins": pins,
        "acceptance_criteria": acceptance,
        "expected_fail": expected_fail,
        "narrated": narrated,
        "unclassified": unclassified,
        "committed_rerunnable": committed_rerunnable,
        "label": label,
    }
