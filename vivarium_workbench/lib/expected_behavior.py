"""Evaluator for the structured ``expected_behavior:`` block in study.yaml.

Each entry in ``expected_behavior:`` has the shape::

    name:   <slug>
    en:     "one-sentence English description"
    given:  {run: baseline | variant, window: full | second_half | ..., ...}
    measure: {kind: bulk_count | listener_path | listener_sum | xy_correlation
                  | event_count | concentration,
              ...kind-specific args,
              reduce: series | median | mean | first_and_last | pre_post_event_ratio}
    expect: {op: in_range | rolling_cv_below | ratio_at_most | ratio_at_least
                | monotonic_decreasing | pearson_below | pearson_above,
             ...op-specific args}
    status: implemented | stub | gated
    requires: [...]

Call :func:`evaluate` with one entry dict and a history list (each element
is ``{step, time, state}``) to get an :class:`EvaluationResult`.

This module is stdlib + statistics only — no numpy / pandas — so it
compiles cleanly in minimal environments.

**Measure kinds**
  - ``bulk_count`` — look up a bulk species by ID in agents.*.bulk.
  - ``listener_path`` — walk a dotted path inside the first agent's subtree.
  - ``listener_sum`` — sum of a list-valued listener path.
  - ``xy_correlation`` — pair of (x, y) sub-measures for Pearson tests.
  - ``event_count`` — count timesteps satisfying a predicate dict.
  - ``concentration`` — bulk_count(molecule) / volume(volume_path); derived.

**Reduce modes**
  - ``series`` — return the full list (default).
  - ``median`` / ``mean`` — scalar aggregate.
  - ``first_and_last`` — ``{first, last}`` dict for ratio tests.
  - ``pre_post_event_ratio`` — see :func:`_pre_post_event_ratio`.

**Expect ops**
  - ``in_range``              — lo ≤ value ≤ hi.
  - ``rolling_cv_below``      — max rolling CV < threshold.
  - ``ratio_at_most``         — last/first ≤ ratio.
  - ``ratio_at_least``        — last/first ≥ ratio.
  - ``monotonic_decreasing``  — series is non-increasing (allow_rebound_pct).
  - ``pearson_below``         — Pearson r < threshold.
  - ``pearson_above``         — Pearson r > threshold.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any


# ─── Result type ────────────────────────────────────────────────────────────


@dataclass
class EvaluationResult:
    """Outcome of evaluating one expected_behavior entry."""

    passed: bool
    message: str
    name: str = ""
    en: str = ""
    extras: dict = field(default_factory=dict)

    def __bool__(self) -> bool:  # pragma: no cover
        return self.passed

    def __iter__(self):
        """Unpack as ``passed, message`` for backward compat with v2ecoli."""
        yield self.passed
        yield self.message


# ─── Error types ────────────────────────────────────────────────────────────


class MissingMeasureError(ValueError):
    """Raised when the measure kind is not recognised."""


class MissingExpectError(ValueError):
    """Raised when the expect op is not recognised."""


# ─── State accessors ────────────────────────────────────────────────────────


def bulk_count(state: dict, molecule_id: str) -> int | float | None:
    """Return the count of *molecule_id* from one history snapshot's state.

    The bulk store is a ``bulk_array``: serialised as either
    ``{"id": [...], "count": [...]}`` or a list of ``(id, count)`` pairs.
    Searches the first agent found under ``agents.*``.
    """
    agents = state.get("agents") or {}
    if not agents:
        return None
    first_agent = next(iter(agents.values()))
    bulk = first_agent.get("bulk")
    if bulk is None:
        return None
    if isinstance(bulk, dict) and "id" in bulk and "count" in bulk:
        ids = bulk["id"]
        counts = bulk["count"]
    elif isinstance(bulk, list) and bulk and isinstance(bulk[0], (list, tuple)):
        ids = [row[0] for row in bulk]
        counts = [row[1] for row in bulk]
    else:
        return None
    try:
        idx = ids.index(molecule_id)
    except ValueError:
        return None
    return counts[idx]


def listener_value(state: dict, dotted_path: str) -> Any:
    """Walk *dotted_path* inside the first agent's subtree.

    Example: ``listener_value(state, 'listeners.rnap_data.rna_init_event')``.
    Returns ``None`` if any segment is missing.
    """
    agents = state.get("agents") or {}
    if not agents:
        return None
    cursor = next(iter(agents.values()))
    for seg in dotted_path.split("."):
        if not isinstance(cursor, dict) or seg not in cursor:
            return None
        cursor = cursor[seg]
    return cursor


def volume_value(state: dict, volume_path: str) -> float | None:
    """Return a scalar volume from *volume_path* inside the first agent.

    Tries ``agents.<id>.<volume_path>`` (dotted).  Returns ``None`` on miss.
    """
    return listener_value(state, volume_path)


# ─── Window selection ───────────────────────────────────────────────────────

_BUILTIN_WINDOWS = ("full", "second_half", "post_initiation_10min")


def window(history: list[dict], name: str) -> list[dict]:
    """Slice *history* to the named window.

    Built-in windows:
      - ``full``                  — the whole history.
      - ``second_half``           — steps from midpoint onward.
      - ``post_initiation_10min`` — 10 min after the first replication-initiation
                                    event (requires ``initiation_events`` listener;
                                    returns ``[]`` until dnaa-04 lands).
    """
    if name == "full":
        return history
    if name == "second_half":
        n = len(history)
        return history[n // 2:] if n >= 2 else history
    if name == "post_initiation_10min":
        # Stub: needs an initiation-event detector (dnaa-04).
        return []
    raise ValueError(f"unknown window {name!r}")


# ─── Measure primitives ─────────────────────────────────────────────────────


def _resolve_predicate(state: dict, pred: dict) -> bool:
    """Evaluate a simple predicate dict against *state*.

    Predicate shape::

        {observable: "dotted.path", op: "==" | ">" | ">=" | "<" | "<=", value: <scalar>}

    The ``observable`` is resolved via :func:`listener_value` first; falls
    back to :func:`bulk_count` if the path looks like a molecule ID (contains
    ``[c]`` or is a bare ID).  Returns ``False`` on any access miss.
    """
    path = pred.get("observable", "")
    op = pred.get("op", "==")
    target = pred.get("value")

    raw = listener_value(state, path)
    if raw is None:
        raw = bulk_count(state, path)
    if raw is None:
        return False
    v = sum(raw) if isinstance(raw, (list, tuple)) else raw

    if op == "==":
        return v == target
    if op == ">":
        return v > target
    if op == ">=":
        return v >= target
    if op == "<":
        return v < target
    if op == "<=":
        return v <= target
    raise ValueError(f"unknown predicate op {op!r}")


def event_count(history: list[dict], predicate: dict) -> int:
    """Count timesteps in *history* where *predicate* is True.

    *predicate* shape::

        {observable: "dotted.path", op: "==" | ">" | ..., value: <scalar>}

    Example — count initiation steps::

        event_count(history, {observable: "listeners.replication.initiation_events",
                               op: ">", value: 0})
    """
    return sum(1 for snap in history if _resolve_predicate(snap["state"], predicate))


def pre_post_event(
    history: list[dict],
    event_predicate: dict,
    before_min: float,
    after_min: float,
) -> tuple[list[dict], list[dict]] | None:
    """Slice *history* around the first event matching *event_predicate*.

    Returns ``(pre_window, post_window)`` where:
      - *pre_window* — up to *before_min* minutes before the event step.
      - *post_window* — up to *after_min* minutes after the event step.

    Returns ``None`` if no matching event is found.

    Parameters
    ----------
    history:
        Ordered list of ``{step, time, state}`` dicts.
    event_predicate:
        Dict in the same shape as :func:`event_count`'s predicate.
    before_min:
        Duration in minutes before the event to include in the pre-window.
    after_min:
        Duration in minutes after the event to include in the post-window.
    """
    event_idx: int | None = None
    event_time: float | None = None
    for i, snap in enumerate(history):
        if _resolve_predicate(snap["state"], event_predicate):
            event_idx = i
            event_time = snap.get("time", 0.0)
            break

    if event_idx is None:
        return None

    before_s = before_min * 60.0
    after_s = after_min * 60.0

    pre: list[dict] = [
        s for s in history[:event_idx]
        if event_time - s.get("time", 0.0) <= before_s
    ]
    post: list[dict] = [
        s for s in history[event_idx + 1:]
        if s.get("time", 0.0) - event_time <= after_s
    ]
    return pre, post


def concentration(
    history_state: dict,
    molecule_id: str,
    volume_path: str,
) -> float | None:
    """Compute derived concentration: bulk_count(molecule) / volume.

    Returns ``None`` if either accessor misses.

    Parameters
    ----------
    history_state:
        A single snapshot's ``state`` dict (one element of history).
    molecule_id:
        Bulk species ID, e.g. ``"MONOMER0-160[c]"``.
    volume_path:
        Dotted path to the volume scalar, e.g. ``"listeners.mass.cell_volume"``.
    """
    count = bulk_count(history_state, molecule_id)
    if count is None:
        return None
    vol = volume_value(history_state, volume_path)
    if not vol:
        return None
    return count / vol


# ─── Measure series extraction ───────────────────────────────────────────────


def _series_for_simple_kind(history: list[dict], measure: dict) -> list[float] | None:
    """Extract a numeric series for non-xy, non-event measure kinds."""
    kind = measure["kind"]

    if kind == "bulk_count":
        mol = measure["id"]
        series = [bulk_count(s["state"], mol) for s in history]
        if all(v is None for v in series):
            return None
        return [v for v in series if v is not None]

    if kind == "listener_sum":
        path = measure["path"]
        out = []
        for s in history:
            v = listener_value(s["state"], path)
            out.append(sum(v) if isinstance(v, (list, tuple)) else (v or 0))
        return out

    if kind == "listener_path":
        path = measure["path"]
        return [listener_value(s["state"], path) for s in history]

    if kind == "event_count":
        pred = measure["predicate"]
        return [float(event_count(history, pred))]

    if kind == "concentration":
        mol = measure["molecule"]
        vol_path = measure["volume_path"]
        series = [
            concentration(s["state"], mol, vol_path)
            for s in history
        ]
        if all(v is None for v in series):
            return None
        return [v for v in series if v is not None]

    raise MissingMeasureError(f"unknown measure kind {kind!r}")


def _apply_reduce(series: list, reduce: str, history: list[dict], measure: dict):
    """Apply *reduce* to *series*.  Returns scalar, dict, or list."""
    if not series:
        return None

    if reduce == "series":
        return series

    if reduce == "median":
        return statistics.median(series)

    if reduce == "mean":
        return statistics.mean(series)

    if reduce == "first_and_last":
        return {"first": series[0], "last": series[-1]}

    if reduce == "pre_post_event_ratio":
        # Requires event_predicate, before_min, after_min in the measure dict.
        event_pred = measure.get("event_predicate")
        before_min = measure.get("before_min", 5.0)
        after_min = measure.get("after_min", 5.0)
        if not event_pred:
            return None  # stub — no predicate supplied yet
        result = pre_post_event(history, event_pred, before_min, after_min)
        if result is None:
            return None
        pre_snaps, post_snaps = result
        # Extract the same series kind from pre/post sub-histories.
        sub_measure = dict(measure)
        sub_measure["reduce"] = "mean"
        pre_series = _series_for_simple_kind(pre_snaps, sub_measure) if pre_snaps else None
        post_series = _series_for_simple_kind(post_snaps, sub_measure) if post_snaps else None
        if not pre_series or not post_series:
            return None
        pre_mean = statistics.mean(pre_series)
        post_mean = statistics.mean(post_series)
        return {"pre_mean": pre_mean, "post_mean": post_mean,
                "ratio": post_mean / pre_mean if pre_mean else None}

    if reduce == "top_quartile_vs_bottom_quartile":
        sorted_s = sorted(series)
        n = len(sorted_s)
        q1 = statistics.median(sorted_s[: n // 4]) if n >= 4 else sorted_s[0]
        q3 = statistics.median(sorted_s[3 * n // 4:]) if n >= 4 else sorted_s[-1]
        return {"q1": q1, "q3": q3}

    raise ValueError(f"unknown reduce mode {reduce!r}")


def _measure(history: list[dict], measure: dict) -> Any:
    """Compute the reduced measure value from *history*."""
    kind = measure["kind"]

    # xy_correlation: pair of sub-measures
    if kind == "xy_correlation":
        x_series = _series_for_simple_kind(history, measure["x"])
        y_series = _series_for_simple_kind(history, measure["y"])
        return {"x": x_series, "y": y_series}

    # event_count special-case: always returns a scalar count, no reduce step.
    if kind == "event_count":
        pred = measure["predicate"]
        return float(event_count(history, pred))

    reduce = measure.get("reduce", "series")
    series = _series_for_simple_kind(history, measure)
    if series is None:
        return None
    return _apply_reduce(series, reduce, history, measure)


# ─── Expect dispatch ────────────────────────────────────────────────────────


def _pearson(x: list[float], y: list[float]) -> float | None:
    """Compute Pearson r between *x* and *y*.  Returns ``None`` on degenerate input."""
    if not x or not y or len(x) != len(y) or len(x) < 2:
        return None
    if statistics.stdev(x) == 0 or statistics.stdev(y) == 0:
        return None
    mx, my = statistics.mean(x), statistics.mean(y)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    denom = statistics.stdev(x) * statistics.stdev(y) * (len(x) - 1)
    return cov / denom if denom else None


def _check(value: Any, expect: dict) -> tuple[bool, str]:
    """Evaluate *expect* against *value*.  Returns ``(passed, message)``."""
    op = expect["op"]
    if value is None:
        return False, f"measure returned None; cannot evaluate op={op!r}"

    if op == "in_range":
        lo, hi = expect["low"], expect["high"]
        return (lo <= value <= hi,
                f"value={value} expected in [{lo}, {hi}]")

    if op == "rolling_cv_below":
        series = value
        w = expect.get("window_steps", 5)
        thresh = expect["threshold"]
        if not isinstance(series, list):
            return False, f"rolling_cv_below expects a series; got {type(series).__name__}"
        if len(series) < w:
            return False, f"need ≥{w} samples for rolling CV; got {len(series)}"
        cvs = []
        for i in range(len(series) - w + 1):
            block = series[i: i + w]
            m = statistics.mean(block)
            if m == 0:
                continue
            cvs.append(statistics.stdev(block) / m if len(block) > 1 else 0.0)
        max_cv = max(cvs) if cvs else 0.0
        return (max_cv < thresh, f"max rolling CV={max_cv:.3f} expected < {thresh}")

    if op == "ratio_at_most":
        if isinstance(value, dict):
            first, last = value["first"], value["last"]
        else:
            return False, f"ratio_at_most expects first_and_last dict; got {type(value).__name__}"
        if first == 0:
            return False, "first sample is zero; ratio undefined"
        ratio = last / first
        return (ratio <= expect["ratio"],
                f"last/first ratio={ratio:.3f} expected ≤ {expect['ratio']}")

    if op == "ratio_at_least":
        if not isinstance(value, dict):
            return False, f"ratio_at_least expects a dict; got {type(value).__name__}"
        if "pre_mean" in value:
            # From pre_post_event_ratio reduce
            first, last = value["pre_mean"], value["post_mean"]
        elif "first" in value and "last" in value:
            # From first_and_last reduce
            first, last = value["first"], value["last"]
        else:
            return False, f"ratio_at_least: unrecognised dict shape {list(value.keys())}"
        if first == 0:
            return False, "first sample is zero; ratio undefined"
        ratio = last / first
        return (ratio >= expect["ratio"],
                f"last/first ratio={ratio:.3f} expected ≥ {expect['ratio']}")

    if op == "monotonic_decreasing":
        if not isinstance(value, list):
            return False, f"monotonic_decreasing expects a series; got {type(value).__name__}"
        if len(value) < 2:
            return False, f"need ≥2 samples; got {len(value)}"
        peak = value[0]
        max_rebound_pct = expect.get("allow_rebound_pct", 0) / 100.0
        for v in value[1:]:
            if peak == 0:
                continue
            if (v - peak) / peak > max_rebound_pct:
                return False, (
                    f"non-monotonic: peak={peak}, later value={v} "
                    f"(rebound > {max_rebound_pct:.1%})"
                )
            peak = min(peak, v)
        return True, f"monotonic-decreasing (first={value[0]}, last={value[-1]})"

    if op in ("pearson_below", "pearson_above"):
        if not isinstance(value, dict) or "x" not in value or "y" not in value:
            return False, f"{op} expects {{x, y}} dict from xy_correlation measure"
        r = _pearson(value["x"], value["y"])
        if r is None:
            return False, "insufficient variance to compute Pearson r"
        thresh = expect["threshold"]
        if op == "pearson_below":
            return r < thresh, f"r={r:.3f} expected < {thresh}"
        return r > thresh, f"r={r:.3f} expected > {thresh}"

    if op == "pre_post_event_ratio":
        # value is {pre_mean, post_mean, ratio} from reduce: pre_post_event_ratio
        if not isinstance(value, dict) or "ratio" not in value:
            return False, f"pre_post_event_ratio expects ratio dict; got {value!r}"
        ratio = value["ratio"]
        if ratio is None:
            return False, "pre_mean was zero; ratio undefined"
        direction = expect.get("direction", "at_least")
        thresh = expect["ratio"]
        if direction == "at_least":
            return (ratio >= thresh, f"post/pre ratio={ratio:.3f} expected ≥ {thresh}")
        return (ratio <= thresh, f"post/pre ratio={ratio:.3f} expected ≤ {thresh}")

    raise MissingExpectError(f"unknown expect op {op!r}")


# ─── Public entry point ─────────────────────────────────────────────────────


def evaluate(entry: dict, history: list[dict]) -> EvaluationResult:
    """Evaluate one ``expected_behavior`` entry against a loaded history.

    Parameters
    ----------
    entry:
        One element from ``study.yaml``'s ``expected_behavior:`` list.
    history:
        Ordered list of ``{step, time, state}`` dicts, typically loaded from
        runs.db via :func:`vivarium_dashboard.testing.study_fixtures.baseline_history`.

    Returns
    -------
    EvaluationResult
        Unpacks as ``(passed, message)`` for backward compatibility with
        the v2ecoli per-study ``_behaviors.py``.

    Notes
    -----
    Does not raise on evaluation errors — the result's ``passed=False`` and
    ``message`` carry the diagnostic. The caller (pytest or the dashboard)
    decides whether to assert, xfail, or skip.
    """
    name = entry.get("name", "")
    en = entry.get("en", "")

    given = entry.get("given") or {}
    window_name = given.get("window", "full")
    sub_history = window(history, window_name)
    if not sub_history:
        return EvaluationResult(
            passed=False,
            message=f"window {window_name!r} produced an empty history slice",
            name=name,
            en=en,
        )

    try:
        value = _measure(sub_history, entry["measure"])
    except MissingMeasureError as exc:
        return EvaluationResult(passed=False, message=str(exc), name=name, en=en)

    try:
        passed, message = _check(value, entry["expect"])
    except MissingExpectError as exc:
        return EvaluationResult(passed=False, message=str(exc), name=name, en=en)

    return EvaluationResult(passed=passed, message=message, name=name, en=en)
