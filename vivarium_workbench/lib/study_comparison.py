"""Cross-engine comparison summary for a remote run's snapshot state.

A viva-biomodels batch run lands as ONE snapshot step in ``history`` whose
state carries ``comparisons.<model>.<job>`` (engine-by-engine NRMSE matrix,
bucket, worst pair) and ``diagnostics.runs.<model>.<job>.<engine>`` (status,
runtime). ``build_comparison`` reduces that to a bounded, JSON-safe
``StudyComparison`` for the Results tab — dropping the per-species ``pairs``
blobs and the full series, which dominate the state's size.

Reads the state through ``explorer_data._first_state`` (the same reader the
scalar preview uses). Returns ``None`` — never raises — when the state has no
usable comparison, so the caller falls back to the scalar preview.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from .models import (
    ComparisonBucketCount,
    ComparisonEngineRun,
    ComparisonJob,
    StudyComparison,
)


def _num(v: Any) -> Optional[float]:
    """A finite float, else ``None`` (NaN/inf/bool/non-numeric never reach the
    wire — the response encoder and the published bundle both reject them)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _str(v: Any) -> Optional[str]:
    return v if isinstance(v, str) and v else None


def _matrix(raw: Any) -> dict[str, dict[str, Optional[float]]]:
    out: dict[str, dict[str, Optional[float]]] = {}
    if not isinstance(raw, dict):
        return out
    for a, row in raw.items():
        if isinstance(row, dict):
            out[str(a)] = {str(b): _num(v) for b, v in row.items()}
    return out


def _engine_runs(diag_job: Any) -> dict[str, ComparisonEngineRun]:
    runs: dict[str, ComparisonEngineRun] = {}
    if not isinstance(diag_job, dict):
        return runs
    for eng, d in diag_job.items():
        if not isinstance(d, dict):
            continue
        n = d.get("n_points")
        runs[str(eng)] = ComparisonEngineRun(
            status=str(d.get("status") or "unknown"),
            runtime_s=_num(d.get("runtime_s")),
            n_points=n if isinstance(n, int) and not isinstance(n, bool) else None,
            error=str(d.get("error") or "")[:300],  # repeated per model: keep the payload bounded
        )
    return runs


def _job(model: str, job: str, c: dict, diag_job: Any) -> Optional[ComparisonJob]:
    matrix = _matrix(c.get("matrix"))
    if not matrix:
        return None  # nothing to compare (single engine / all engines failed)
    engines = [str(e) for e in (c.get("engines") or [])] or sorted(matrix)
    worst = c.get("worst_pair")
    return ComparisonJob(
        model=model,
        job=job,
        engines=engines,
        matrix=matrix,
        max_nrmse=_num(c.get("max_nrmse")),
        worst_pair=[str(x) for x in worst] if isinstance(worst, (list, tuple)) and len(worst) == 2 else None,
        bucket=_str(c.get("bucket")),
        bucket_label=_str(c.get("bucket_label")),
        closeness_bucket_label=_str(c.get("closeness_bucket_label")),
        runs=_engine_runs(diag_job),
    )


def build_comparison(db_path: str, run_id: Optional[str]) -> Optional[dict]:
    """``StudyComparison`` (as a dict) for the run's snapshot state, or ``None``."""
    from .explorer_data import _first_state

    state = _first_state(str(db_path), run_id)
    comps = state.get("comparisons") if isinstance(state, dict) else None
    if not isinstance(comps, dict) or not comps:
        return None
    diag = ((state.get("diagnostics") or {}).get("runs") or {}) if isinstance(state.get("diagnostics"), dict) else {}

    jobs: list[ComparisonJob] = []
    for model, by_job in comps.items():
        if not isinstance(by_job, dict):
            continue
        for job, c in by_job.items():
            if not isinstance(c, dict):
                continue
            diag_job = (diag.get(model) or {}).get(job) if isinstance(diag.get(model), dict) else None
            j = _job(str(model), str(job), c, diag_job)
            if j is not None:
                jobs.append(j)
    if not jobs:
        return None

    # Worst disagreement first (unknown scores last), then by name — a stable
    # order that puts the models worth looking at on top.
    jobs.sort(key=lambda j: (j.max_nrmse is None, -(j.max_nrmse or 0.0), j.model, j.job))

    engines: list[str] = []
    for j in jobs:
        for e in [*j.engines, *j.runs]:
            if e not in engines:
                engines.append(e)

    # Bucket summary, ordered best -> worst by the lowest score in each bucket.
    lo: dict[str, float] = {}
    counts: dict[str, int] = {}
    for j in jobs:
        key = j.bucket_label or j.bucket or "unclassified"
        counts[key] = counts.get(key, 0) + 1
        lo[key] = min(lo.get(key, math.inf), j.max_nrmse if j.max_nrmse is not None else math.inf)
    buckets = [ComparisonBucketCount(label=k, count=counts[k]) for k in sorted(counts, key=lambda k: (lo[k], k))]

    return StudyComparison(
        n_models=len({j.model for j in jobs}),
        n_jobs=len(jobs),
        engines=sorted(engines),
        buckets=buckets,
        jobs=jobs,
    ).model_dump()
