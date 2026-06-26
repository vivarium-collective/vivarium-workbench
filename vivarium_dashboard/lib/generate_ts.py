"""Generate TypeScript declarations from the pydantic payload models.

The models in ``lib/models.py`` are the single source of truth for the
dashboard's client/server JSON contract. This derives a matching
``static/types/domain.generated.d.ts`` so the browser-side code — and
colleagues reading it — get types generated FROM the Python models rather than
hand-copied out of them (which is how the earlier hand-written TS drifted:
started_at typed as string when it is really an epoch float).

Run:  python -m vivarium_dashboard.lib.generate_ts
A test (tests/test_generate_ts.py) asserts the committed file is up to date.

Intentionally dependency-free (no npm / json-schema tooling): the model
vocabulary is small and fixed (str/int/float/bool, Optional, Literal, list,
nested models), so a direct introspective generator is simpler and has no
build chain. It raises on any type it does not recognise rather than emitting
silently-wrong TypeScript.
"""

from __future__ import annotations

import types as _pytypes
import typing
from pathlib import Path

from pydantic import BaseModel

from vivarium_dashboard.lib import models as _models

# Named Literal aliases to preserve in the output. pydantic inlines these in
# field annotations, so we re-attach the name by matching the value set.
_ALIASES: dict[str, object] = {
    "EmitterKind": _models.EmitterKind,
    "RemoteJobStatus": _models.RemoteJobStatus,
}

# Models to emit, in a readable order (interface order is cosmetic in TS).
_MODELS: list[type[BaseModel]] = [
    _models.RemoteOrigin,
    _models.StudyRef,
    _models.SimRow,
    _models.SimulationsPayload,
    _models.RemoteRunStep,
    _models.RemoteRunJob,
    _models.ChartPayload,
    _models.StudyChartsPayload,
    _models.DashConfig,
    _models.InvestigationSummary,
    _models.DataSource,
    _models.DataSourcesPayload,
    _models.BibEntry,
    _models.ReferencesBibPayload,
    _models.SavedViz,
    _models.PtoolsStudy,
    _models.PtoolsInfo,
    _models.ReportCard,
    _models.SavedVisualizationsPayload,
    # Git & branch models
    _models.GitStatus,
    _models.WorkStatusInactive,
    _models.WorkStatusActive,
    _models.BranchStaleness,
    _models.DirtyFile,
    _models.DirtyStatus,
    _models.BranchCommit,
    _models.BranchInfo,
    _models.BranchesPayload,
    _models.BranchDiff,
    # Work & branches models (pending entries, generation, composite diff)
    _models.PendingEntries,
    _models.GenerationSummary,
    _models.Generation,
    _models.WorkCompositeDiffEntry,
    _models.WorkCompositeDiff,
    # Investigation detail models
    _models.VizHtmlFile,
    _models.InvestigationVizHtmlPayload,
    _models.InvestigationCompositeEntry,
    _models.InvestigationCompositesPayload,
    _models.InvestigationCompositeDocPayload,
    _models.InvestigationStateTree,
    _models.InvestigationHypothesesPayload,
    # Rigor models
    _models.StudyRigor,
    _models.InvestigationRigor,
    # Studies detail model
    _models.StudyDetail,
    # Data explorer models
    _models.ExplorerRuns,
    _models.ExplorerObservables,
    _models.ExplorerSeries,
    _models.ExplorerFlux,
    _models.ExplorerVector,
    _models.ExplorerProteinBreakdown,
    # Reports & inputs models
    _models.ReportLint,
    _models.NeedsAttention,
    _models.InputsPayload,
    _models.IsetDetail,
    # Observables + linkage-index models
    _models.ObservablesPayload,
    _models.StudyObservableCheck,
    _models.LinkageIndex,
    # Composite-state model
    _models.CompositeState,
    # System & workspace models
    _models.FrameworkMetrics,
    _models.GithubRepo,
    _models.UiConfig,
    _models.WorkspaceHome,
    # Composite runs models
    _models.CompositeRunsList,
    _models.CompositeRunTrajectory,
    _models.CompositeRunState,
    _models.CompositeRunStatus,
    # Batch 11: study-bigraph-paths, visualization status/instances, ptools-launch
    _models.StudyBigraphPaths,
    _models.VisualizationStatus,
    _models.VisualizationInstances,
    _models.PtoolsLaunch,
    # Workspace & source models
    _models.SourceBuilds,
    _models.WorkspacesList,
    _models.SystemDepsCheck,
    # Job status model (in-memory manager polling)
    _models.JobStatusPayload,
]

OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent / "static" / "types" / "domain.generated.d.ts"
)

_ALIAS_BY_VALUES = {
    frozenset(typing.get_args(tp)): name for name, tp in _ALIASES.items()
}


def _ts_literal(value: object) -> str:
    if isinstance(value, str):
        return "'" + value.replace("'", "\\'") + "'"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _ts_type(tp: object) -> str:
    origin = typing.get_origin(tp)

    # Literal -> named alias if the value set matches one, else an inline union.
    if origin is typing.Literal:
        values = list(typing.get_args(tp))
        alias = _ALIAS_BY_VALUES.get(frozenset(values))
        if alias is not None:
            return alias
        return " | ".join(_ts_literal(v) for v in values)

    # Union / Optional (X | None) -> "X | null".
    if origin is typing.Union or origin is _pytypes.UnionType:
        parts = [
            "null" if arg is type(None) else _ts_type(arg)
            for arg in typing.get_args(tp)
        ]
        return " | ".join(parts)

    # list[X] -> "X[]".
    if origin in (list, typing.List):
        (inner,) = typing.get_args(tp)
        return f"{_ts_type(inner)}[]"

    # Primitives.
    if tp is str:
        return "string"
    if tp in (int, float):
        return "number"
    if tp is bool:
        return "boolean"
    if tp is typing.Any:
        return "any"

    # Nested model -> reference by name.
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return tp.__name__

    raise TypeError(f"generate_ts: unhandled annotation {tp!r}")


def _emit_interface(model: type[BaseModel]) -> str:
    lines = [f"export interface {model.__name__} {{"]
    for name, field in model.model_fields.items():
        # model_dump() always includes every key, so no optional `?` properties;
        # nullability is expressed as `| null` from the Optional annotation.
        lines.append(f"  {name}: {_ts_type(field.annotation)};")
    lines.append("}")
    return "\n".join(lines)


def generate_ts() -> str:
    """Return the full TypeScript declaration text for the payload models."""
    blocks = [
        "// AUTO-GENERATED from vivarium_dashboard/lib/models.py — do not edit by hand.\n"
        "// Regenerate: python -m vivarium_dashboard.lib.generate_ts"
    ]
    for name, tp in _ALIASES.items():
        union = " | ".join(_ts_literal(v) for v in typing.get_args(tp))
        blocks.append(f"export type {name} = {union};")
    for model in _MODELS:
        blocks.append(_emit_interface(model))
    return "\n\n".join(blocks) + "\n"


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(generate_ts(), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
