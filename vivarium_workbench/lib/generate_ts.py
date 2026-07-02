"""Generate TypeScript declarations from the pydantic payload models.

The models in ``lib/models.py`` are the single source of truth for the
dashboard's client/server JSON contract. This derives a matching
``static/types/domain.generated.d.ts`` so the browser-side code — and
colleagues reading it — get types generated FROM the Python models rather than
hand-copied out of them (which is how the earlier hand-written TS drifted:
started_at typed as string when it is really an epoch float).

Run:  python -m vivarium_workbench.lib.generate_ts
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

from vivarium_workbench.lib import models as _models

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
    _models.InvestigationSummary,
    _models.InvestigationSummariesPayload,
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
    _models.DirtyFile,
    _models.DirtyStatus,
    # Work & branches models (generation, composite diff)
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
    # Source-switch (in-process workspace re-point) response models
    _models.SourceSwitchSource,
    _models.SourceSwitchResponse,
    # C-state-3b: source build-remote response (switch-build reuses SourceSwitchResponse)
    _models.BuildRemoteResponse,
    # C-state-3c: remote-run submit response
    _models.RemoteRunStartResponse,
    # C-state-3e: GitHub device-flow auth (5 routes, pass-through payload)
    _models.AuthPayload,
    # C-state-3f: git-subprocess commit/push routes
    _models.BranchPushResponse,
    _models.DirtyCommitAllResponse,
    # C-state-3f2: workstream-lifecycle routes
    _models.WorkStartResponse,
    _models.WorkPushResponse,
    _models.WorkEndResponse,
    _models.WorkAttachReportResponse,
    # C-state-3f3: workstream GitHub-PR-create route
    _models.WorkCreatePrResponse,
    # C-state-3f4: workstream link-branch-to-upstream route
    _models.WorkLinkBranchResponse,
    # C-state-3h1: workspace-registry routes
    _models.WorkspacesOkResponse,
    _models.WorkspaceEntry,
    # C-state-3h2: misc FS/render routes
    _models.RenderResponse,
    # C-state-3i: visualization-accept finalize route
    _models.VisualizationAcceptResponse,
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

    # list[X] -> "X[]".  When the element type is itself a union (e.g.
    # list[Union[StudyRef, str]] -> "StudyRef | string"), wrap it in parens so
    # TS parses the array of the whole union — "(StudyRef | string)[]" — and not
    # "StudyRef | (string[])", which the bare "StudyRef | string[]" means.
    if origin in (list, typing.List):
        (inner,) = typing.get_args(tp)
        inner_ts = _ts_type(inner)
        inner_origin = typing.get_origin(inner)
        is_union = inner_origin is typing.Union or inner_origin is _pytypes.UnionType
        # A Literal with >1 value (and no named alias) also renders as a union.
        is_inline_literal = inner_origin is typing.Literal and " | " in inner_ts
        if is_union or is_inline_literal:
            inner_ts = f"({inner_ts})"
        return f"{inner_ts}[]"

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
        "// AUTO-GENERATED from vivarium_workbench/lib/models.py — do not edit by hand.\n"
        "// Regenerate: python -m vivarium_workbench.lib.generate_ts"
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
