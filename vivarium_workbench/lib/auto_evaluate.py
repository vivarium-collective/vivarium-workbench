"""auto_evaluate.py — auto-evaluate a study's behavior tests on canonical-run
completion and record per-test results into ``runs[].outcomes``.

Kills expert-feedback friction pattern #4 ("Tests haven't run yet" /
"run the tests as well", see
``docs/conventions/expert-feedback-friction-analysis.md`` in the workspace):
instead of the expert asking the agent to run the tests after a run, the
dashboard's run-completion path calls
:func:`evaluate_on_run_completion` and the just-completed run's
``runs[].outcomes`` is filled with normalized PASS / FAIL / PARTIAL / SKIP
verdicts — so the report never shows a "pending" pill for a study that ran.

Deterministic + dashboard-callable orchestration. The behavior-test evaluation
is pluggable via an injected ``test_runner`` callable so the orchestration is
unit-testable without a live dashboard or a real ``RunReader``. The default
runner reuses :func:`viva_superpowers.study_evaluator.evaluate_study` against a
``RunReader`` opened on the run's store.

Relationship to ``study_evaluator.compute_outcomes``: that writes a *parallel*
``computed_outcomes`` block per run and deliberately never touches the authored
``outcomes`` surface (so it can never clobber an expert verdict). This module
writes the **authoritative** ``runs[].outcomes`` surface that the report's
pass/fail pill actually reads — but only for tests that have no human-authored
verdict yet, leaving authored outcomes untouched by default.

``runs[].outcomes`` shape (verified against real study.yaml +
``study_status.bucket_tests`` renderer):

    outcomes:
      <test name>:                 # keyed by the test's ``name`` field
        result: PASS|FAIL|PARTIAL|SKIP   # UPPERCASE; the pill keys off this
        note: <prose>              # authored prose — preserved on round-trip
        measured_value: ...        # optional, from the evaluator
        detail: ...                # optional, from the evaluator
        evaluated_by: code         # optional provenance from the evaluator
        auto_evaluated: true       # provenance: written by this module
"""
from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from viva_superpowers import study_io  # noqa: F401  (kept for the documented study_io/study_outcomes seam)
from viva_superpowers.study_outcomes import canonical_run


# ---------------------------------------------------------------------------
# Result normalization — map any runner's status spelling to the closed
# UPPERCASE vocabulary the report pill renders (study_status._TEST_* sets).
# ---------------------------------------------------------------------------

_RESULT_SYNONYMS: dict[str, str] = {
    "PASS": "PASS", "PASSED": "PASS", "OK": "PASS", "TRUE": "PASS",
    "FAIL": "FAIL", "FAILED": "FAIL", "ERROR": "FAIL", "FALSE": "FAIL",
    "PARTIAL": "PARTIAL", "MIXED": "PARTIAL",
    "SKIP": "SKIP", "SKIPPED": "SKIP", "INCONCLUSIVE": "SKIP",
    "PENDING": "SKIP", "NA": "SKIP", "N/A": "SKIP",
    # evaluator buckets that carry no verdict → SKIP (couldn't grade in code)
    "AGENT": "SKIP", "NEEDS_RERUN": "SKIP", "UNGRADED": "SKIP",
}


class StoreUnresolved(Exception):
    """Raised by the default runner when a run's emitter store can't be found."""


def _normalize_result(raw: Mapping | str | None) -> str:
    """Normalize a runner's per-test outcome to an UPPERCASE result string.

    Accepts either a mapping (the common case: ``{"result": ...}`` or an
    evaluator bucket like ``{"evaluated_by": "agent", "reason": ...}``) or a
    bare result string. An outcome with no ``result`` (the agent / needs_rerun
    evaluator buckets) normalizes to ``SKIP`` — it ran but could not be graded
    in code, which the report renders as a skip rather than a pass/fail.
    """
    if isinstance(raw, str):
        key = raw.strip().upper()
        return _RESULT_SYNONYMS.get(key, key)
    if not isinstance(raw, Mapping):
        return "SKIP"
    result = raw.get("result")
    if result is None or str(result).strip() == "":
        return "SKIP"
    key = str(result).strip().upper()
    return _RESULT_SYNONYMS.get(key, key)


def _build_outcome_entry(raw: Mapping | str | None, existing: Mapping | None):
    """Build the CommentedMap written under ``outcomes[<test name>]``.

    Carries the evaluator's ``measured_value`` / ``detail`` / ``evaluated_by``
    provenance through verbatim, preserves any human-authored ``note`` prose
    found on an existing entry (else falls back to the runner's
    ``note`` / ``reason``), and stamps ``auto_evaluated: true`` so a later pass
    can tell its own writes from a human verdict.
    """
    from ruamel.yaml import CommentedMap  # noqa: PLC0415

    entry = CommentedMap()
    entry["result"] = _normalize_result(raw)

    raw_map: Mapping = raw if isinstance(raw, Mapping) else {}
    # ``axis`` is the report_card_verdict/v2 axis study_evaluator now attaches
    # to a code outcome (signed margin + severity); carry it through so the
    # margin survives into runs[].outcomes for the report-card render.
    for key in ("measured_value", "detail", "operator", "evaluated_by", "axis"):
        if raw_map.get(key) is not None:
            entry[key] = raw_map[key]

    # Prose precedence: an authored note wins (never destroy expert prose);
    # otherwise the runner's note/reason is recorded for context.
    note = None
    if isinstance(existing, Mapping) and existing.get("note"):
        note = existing["note"]
    elif raw_map.get("note"):
        note = raw_map["note"]
    elif raw_map.get("reason"):
        note = raw_map["reason"]
    if note is not None:
        entry["note"] = note

    entry["auto_evaluated"] = True
    return entry


# ---------------------------------------------------------------------------
# Run identification
# ---------------------------------------------------------------------------

def _same_run(run: Mapping, run_id: str) -> bool:
    """True if ``run`` is the entry for ``run_id`` (by ``name`` or ``run_id``).

    Dashboard run records key runs[] by ``name`` (== runs_meta.run_id, set by
    ``study_outcomes.record_runs``); ``run_id`` is accepted as a fallback.
    """
    return run.get("name") == run_id or run.get("run_id") == run_id


def _find_run(runs: list, run_id: str) -> dict | None:
    for r in runs:
        if isinstance(r, Mapping) and _same_run(r, run_id):
            return r  # type: ignore[return-value]
    return None


# ---------------------------------------------------------------------------
# Default test runner — reuse the closed measure/pass_if evaluator
# ---------------------------------------------------------------------------

def _evaluator_test_runner(
    study_dir: Path, spec: dict, run: dict, ws_root: Any
) -> dict[str, dict]:
    """Default behavior-test runner: evaluate the study's ``tests`` /
    ``behavior_tests`` against the run's store via
    :func:`study_evaluator.evaluate_study`.

    Returns ``{test_name: outcome_dict}`` in the evaluator's native shape
    (code path carries ``result``; the agent / needs_rerun buckets carry no
    ``result`` and normalize to ``SKIP``). Raises :class:`StoreUnresolved`
    when the run has no openable emitter store, and ``ImportError`` when the
    optional ``[evaluator]`` extra (``pbg_emitters.RunReader``) is absent — the
    orchestrator turns both into a clean ``status`` rather than a crash.
    """
    from viva_superpowers.study_evaluator import _resolve_run_store, evaluate_study  # noqa: PLC0415

    store = _resolve_run_store(run, study_dir, ws_root)
    if store is None:
        if run.get("evaluation_only"):
            # Store-less grading for a run-less (params-only) study: its behavior
            # tests are derived-scalar checks that read persisted observables (via
            # the workspace derived-scalar registry), not the run store, so they
            # grade with reader=None. A test that genuinely needs the reader falls
            # to the agent bucket honestly. Only evaluation-only runs take this
            # path — a real run whose store is missing still raises, preserving
            # that diagnostic.
            return evaluate_study(spec, None, ws_root=ws_root)
        raise StoreUnresolved(f"no openable store for run {run.get('name')!r}")
    from viva_emitters import RunReader  # noqa: PLC0415  (evaluator extra)

    reader = RunReader.open(store)
    return evaluate_study(spec, reader, ws_root=ws_root)


# ---------------------------------------------------------------------------
# Comment-preserving write of runs[].outcomes
# ---------------------------------------------------------------------------

def _write_outcomes(
    study_yaml: Path,
    run_id: str,
    raw_outcomes: Mapping[str, Any],
    *,
    overwrite_authored: bool,
) -> tuple[bool, int, int]:
    """Write normalized outcomes under the target run via a ruamel round-trip.

    Preserves all YAML comments / prose / block style (ruamel), writes the file
    only when the serialized content actually changes (idempotent), and — by
    default — never overwrites a human-authored outcome (an existing entry that
    carries a ``result`` and is NOT stamped ``auto_evaluated``).

    Returns ``(changed, written, skipped_authored)``.
    """
    import ruamel.yaml  # noqa: PLC0415

    ryaml = ruamel.yaml.YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096  # avoid line-wrap reflow on long prose values

    with open(study_yaml, encoding="utf-8") as fh:
        doc = ryaml.load(fh)

    orig_sio = io.StringIO()
    ryaml.dump(doc, orig_sio)
    original = orig_sio.getvalue()

    runs = doc.get("runs") or []
    target = _find_run(list(runs), run_id)
    if target is None:
        return (False, 0, 0)

    outcomes = target.get("outcomes")
    if not isinstance(outcomes, ruamel.yaml.CommentedMap):
        new_outcomes = ruamel.yaml.CommentedMap()
        if isinstance(outcomes, Mapping):
            new_outcomes.update(outcomes)
        outcomes = new_outcomes
        target["outcomes"] = outcomes

    written = 0
    skipped = 0
    for name, raw in raw_outcomes.items():
        existing = outcomes.get(name)
        authored = (
            isinstance(existing, Mapping)
            and existing.get("result") is not None
            and not existing.get("auto_evaluated")
        )
        if authored and not overwrite_authored:
            skipped += 1
            continue
        outcomes[name] = _build_outcome_entry(raw, existing)
        written += 1

    new_sio = io.StringIO()
    ryaml.dump(doc, new_sio)
    updated = new_sio.getvalue()

    if updated != original:
        tmp = study_yaml.with_suffix(study_yaml.suffix + ".tmp")
        tmp.write_text(updated, encoding="utf-8")
        os.replace(str(tmp), str(study_yaml))
        return (True, written, skipped)
    return (False, written, skipped)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_on_run_completion(
    study_dir: Any,
    run_id: str,
    *,
    ws_root: Any = None,
    test_runner: Callable[[Path, dict, dict, Any], Mapping[str, Any]] | None = None,
    overwrite_authored: bool = False,
) -> dict:
    """Evaluate a study's behavior tests against a just-completed run and record
    the per-test verdicts into that run's ``runs[].outcomes``.

    This is the run-completion hook: the dashboard calls it after a study run
    finishes (and after ``study_outcomes.sync`` has recorded the run into
    ``runs[]``), so the report's pass/fail pills are populated automatically.

    Args:
        study_dir: Directory containing ``study.yaml``.
        run_id:    The run that completed — matched against ``runs[].name``
                   (or ``runs[].run_id``).
        ws_root:   Workspace root (for resolving relative store paths and
                   workspace-pluggable evaluators).
        test_runner: Optional injected callable
                   ``(study_dir, spec, run, ws_root) -> {test_name: outcome}``
                   used INSTEAD of the default evaluator runner. The default
                   reuses :func:`study_evaluator.evaluate_study` over a
                   ``RunReader`` opened on the run's store. The injected seam
                   makes the orchestration unit-testable with no live dashboard.
        overwrite_authored: When True, recompute and overwrite human-authored
                   outcomes too (default False: authored verdicts are preserved
                   and only their ``result`` is reported back as skipped).

    Returns a summary dict::

        {
          "study": <slug>, "run": <run_id>,
          "is_canonical": bool,     # is the completed run the canonical one?
          "evaluated": int,         # tests the runner returned
          "written": int,           # outcome entries created/updated
          "skipped_authored": int,  # human-authored outcomes left untouched
          "changed": bool,          # study.yaml actually rewritten
          "status": "ok" | "run_not_found" | "no_tests" | "store_unresolved"
                    | "evaluator_unavailable: ..." | "runner_error: ...",
        }

    Idempotent: re-invoking with the same run + same run data produces no file
    change (``changed: False``). Canonical-run tracking is left to
    ``study_outcomes.canonical_run`` (newest completed run wins) and reported
    via ``is_canonical``; this hook never mutates ``canonical:`` flags, so it
    cannot pin a stale run.
    """
    study_dir = Path(study_dir)
    study_yaml = study_dir / "study.yaml"
    spec = study_io.load_yaml(study_yaml)

    summary: dict = {
        "study": spec.get("name") or study_dir.name,
        "run": run_id,
        "is_canonical": False,
        "evaluated": 0,
        "written": 0,
        "skipped_authored": 0,
        "changed": False,
        "status": "ok",
    }

    runs = spec.get("runs") or []
    target = _find_run(list(runs), run_id)
    if target is None:
        summary["status"] = "run_not_found"
        return summary

    canon = canonical_run(spec)
    summary["is_canonical"] = bool(canon and _same_run(canon, run_id))

    runner = test_runner or _evaluator_test_runner
    try:
        raw_outcomes = runner(study_dir, spec, target, ws_root) or {}
    except StoreUnresolved:
        summary["status"] = "store_unresolved"
        return summary
    except ImportError as exc:
        summary["status"] = f"evaluator_unavailable: {exc}"
        return summary
    except Exception as exc:  # noqa: BLE001 — a runner error must not crash a run
        summary["status"] = f"runner_error: {exc}"
        return summary

    summary["evaluated"] = len(raw_outcomes)
    if not raw_outcomes:
        summary["status"] = "no_tests"
        return summary

    changed, written, skipped = _write_outcomes(
        study_yaml, run_id, raw_outcomes, overwrite_authored=overwrite_authored
    )
    summary["written"] = written
    summary["skipped_authored"] = skipped
    summary["changed"] = changed
    return summary
