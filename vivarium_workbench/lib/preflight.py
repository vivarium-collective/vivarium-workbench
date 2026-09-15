"""Pre-spend preflight for a composite-id + overrides remote run.

The CD2 GovCloud path dispatches a run as ``run_pbg --composite-id <id>
--overrides {…}`` (composite-id mode — the ``.pbg`` for ``ecoli_baseline`` is
not independently reloadable, so the run is *built in the container* from the
id + overrides). Everything that can go wrong with that request — a dropped
process swap, a typo'd emit path, an empty variant grid, an incoherent step
count — fails *silently and successfully* on GovCloud (see the CD2 pipeline
audit, §2/§3). This module validates the request **locally, before dispatch**,
turning those silent-wrong modes into a loud, aggregated local error.

Public API
----------
``preflight_composite_run(ws_root, composite_id, overrides, ...)`` resolves the
composite locally (a real ParCa cache is required for ``ecoli_baseline`` — e.g.
``~/code/sms-ecoli/out/cache_cd1``), runs every tractable invariant check, and
raises :exc:`PreflightError` listing **all** failures (not just the first) if
any invariant is violated. ``run_remote`` calls it before ``compose_submit``;
a failure aborts the dispatch. Pass ``skip_preflight=True`` (or call with
``strict=False``) to bypass for callers that have already validated.

The checks map onto the audit findings:

1. **build** — ``spec.to_document(overrides)`` resolves + builds (§4.7.1).
2. **injection-applied** — a requested ``injected_processes`` / ``swap_processes``
   swap is actually present in the built document (the P0-1 / §2.2 regression
   guard — the single most valuable check).
3. **emit-paths** — each emit path an emitter node in the built document is
   wired to resolves to a real store, not a silently-materialized empty dict
   (§2.9).
4. **variant-expansion** — a ``variants`` grid expands to a non-zero branch
   count, matching the caller's expectation when supplied (§3.5).
5. **step-shape** — the step count is coherent with the composite's shape
   (single-cell vs. batch) (§3.3).
6. **parca-cache** — a locally-pathed ParCa ``cache_dir`` exists (§2.13; the
   S3 case is noted as deferred, it needs live infra).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Result / error types
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """Outcome of one preflight check."""

    name: str
    status: str  # "pass" | "fail" | "skip"
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "fail"


@dataclass
class PreflightReport:
    """Aggregated outcome of a preflight run."""

    composite_id: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        return [f"[{c.name}] {c.detail}" for c in self.checks if c.status == "fail"]

    @property
    def passed(self) -> bool:
        return not self.failures

    def add(self, name: str, status: str, detail: str = "") -> CheckResult:
        c = CheckResult(name, status, detail)
        self.checks.append(c)
        return c

    def summary(self) -> str:
        lines = [f"preflight for composite {self.composite_id!r}:"]
        for c in self.checks:
            mark = {"pass": "ok", "fail": "FAIL", "skip": "skip"}.get(c.status, c.status)
            lines.append(f"  [{mark}] {c.name}: {c.detail}" if c.detail else f"  [{mark}] {c.name}")
        return "\n".join(lines)


class PreflightError(RuntimeError):
    """Raised when one or more preflight invariants fail.

    ``failures`` carries the individual failure messages; ``report`` the full
    :class:`PreflightReport` (including passed/skipped checks) for logging.
    """

    def __init__(self, failures: list[str], report: PreflightReport | None = None):
        self.failures = failures
        self.report = report
        msg = (
            "Preflight validation failed before remote dispatch — NOT dispatching "
            "(this would otherwise be a silent wrong-but-successful GovCloud run):\n"
            + "\n".join(f"  - {f}" for f in failures)
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def preflight_composite_run(
    ws_root: "Path | str",
    composite_id: str,
    overrides: "dict | None" = None,
    *,
    core: Any = None,
    n_steps: "int | None" = None,
    expected_variant_count: "int | None" = None,
    emit_paths: "list[str] | None" = None,
    strict: bool = True,
) -> PreflightReport:
    """Validate a composite-id + overrides run LOCALLY before remote dispatch.

    Parameters
    ----------
    ws_root, composite_id, overrides:
        The same triple ``run_pbg --composite-id <id> --overrides {…}`` receives.
    core:
        Optional pre-built process-bigraph core; built from the workspace if None.
    n_steps:
        The step count the run will be dispatched with, for the shape check (§3.3).
    expected_variant_count:
        If the caller knows how many variant branches it expects (e.g. 84 for
        Run-4 pathway-expression), the variant grid must expand to exactly that.
    emit_paths:
        Extra emit paths to reconcile against the built state (on top of the ones
        recovered from the document's emitter nodes).
    strict:
        When True (default) a failing check raises :exc:`PreflightError`. When
        False the report is returned without raising (inspect ``.passed``).

    Returns
    -------
    PreflightReport
        The full report. Raises :exc:`PreflightError` on any failure when
        ``strict``.
    """
    overrides = dict(overrides or {})
    report = PreflightReport(composite_id=composite_id)

    # --- Check 1: composite resolves + builds -----------------------------
    document: "dict | None" = None
    try:
        spec, core = _resolve_spec(ws_root, composite_id, core)
        document = spec.to_document(overrides=overrides, core=core)
        report.add("build", "pass", "composite resolved and built")
    except Exception as exc:  # noqa: BLE001 — surface any build failure loudly
        report.add(
            "build", "fail",
            f"composite {composite_id!r} failed to build with the given overrides: "
            f"{type(exc).__name__}: {exc}",
        )

    # --- Check 2: the injection actually applied (P0-1 / §2.2) -------------
    _check_injection_applied(document, overrides, report)

    # --- Check 3: declared emit paths resolve to real stores (§2.9) --------
    _check_emit_paths(document, emit_paths, report)

    # --- Check 4: variant expansion count (§3.5) --------------------------
    _check_variant_expansion(overrides, expected_variant_count, report)

    # --- Check 5: step count vs composite shape (§3.3) --------------------
    _check_step_shape(document, overrides, n_steps, report)

    # --- Check 6: ParCa cache resolves (§2.13) ----------------------------
    _check_parca_cache(overrides, report)

    if strict and report.failures:
        raise PreflightError(report.failures, report)
    return report


# ---------------------------------------------------------------------------
# Spec resolution (mirrors pbg_export.export_composite_pbg)
# ---------------------------------------------------------------------------

def _resolve_spec(ws_root: "Path | str", composite_id: str, core: Any):
    """Resolve a composite spec (generator or static) + core for the workspace.

    Mirrors ``pbg_export.export_composite_pbg``'s resolution so the preflight
    validates exactly what the dispatch would build.
    """
    import sys

    import yaml
    from process_bigraph.composite_spec import CompositeSpec
    from process_bigraph.composite_spec import get as _get_spec

    from vivarium_workbench.lib.pbg_export import _build_core_for_workspace

    ws_root = Path(ws_root)
    ws_str = str(ws_root)
    if ws_str not in sys.path:
        sys.path.insert(0, ws_str)

    if core is None:
        core = _build_core_for_workspace(ws_root)

    spec = _get_spec(composite_id)
    if spec is None:
        from vivarium_workbench.lib.composite_lookup import find_composite_path

        ws_yaml = ws_root / "workspace.yaml"
        ws_data = (
            yaml.safe_load(ws_yaml.read_text(encoding="utf-8")) if ws_yaml.is_file() else {}
        )
        pkg = ws_data.get("package_path") or (
            "pbg_" + str(ws_data.get("name", "")).replace("-", "_")
        )
        path = find_composite_path(ws_root, pkg, composite_id)
        if path is None:
            raise ValueError(f"Composite {composite_id!r} not found in workspace {ws_root}")
        spec = CompositeSpec.from_file(path)

    return spec, core


# ---------------------------------------------------------------------------
# Check 2 — injection applied
# ---------------------------------------------------------------------------

_SWAP_KEYS = ("injected_processes", "swap_processes")


def _last_segment(token: str) -> str:
    """Last address segment: ``local:!mod.qual.Cls`` / ``pkg.mod.Cls`` -> ``Cls``."""
    s = str(token)
    s = s.split(":")[-1]
    if s.startswith("!"):
        s = s[1:]
    return s.rsplit(".", 1)[-1]


def _requested_swaps(overrides: dict) -> "list[tuple[str, str]]":
    """Flatten ``injected_processes`` / ``swap_processes`` overrides.

    Returns a list of ``(slot, repl_token)`` where ``repl_token`` is the
    replacement's identifying string (an address, class name, or the slot name
    itself if the replacement is unnamed).
    """
    out: list[tuple[str, str]] = []
    for key in _SWAP_KEYS:
        spec = overrides.get(key)
        if not isinstance(spec, dict):
            continue
        for slot, repl in spec.items():
            token = ""
            if isinstance(repl, str):
                token = repl
            elif isinstance(repl, dict):
                token = str(repl.get("address") or repl.get("name") or repl.get("_type") or "")
            if not token:
                token = str(slot)
            out.append((str(slot), token))
    return out


def _collect_injection_evidence(document: "dict | None") -> "tuple[set[str], dict]":
    """Walk the built document; return (address-last-segments, carried-swaps).

    ``addresses`` is the set of last-segments of every ``address`` in the doc
    (single-cell evidence: the swapped process appears as a node). ``carried``
    merges every ``injected_processes`` / ``swap_processes`` dict found in any
    node/config (batch evidence: the swap is threaded into the runner's config
    to be applied inside the worker).
    """
    addresses: set[str] = set()
    carried: dict = {}

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            addr = node.get("address")
            if isinstance(addr, str):
                addresses.add(_last_segment(addr))
            for key in _SWAP_KEYS:
                val = node.get(key)
                if isinstance(val, dict):
                    carried.update(val)
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(document or {})
    return addresses, carried


def _check_injection_applied(document: "dict | None", overrides: dict, report: PreflightReport) -> None:
    requested = _requested_swaps(overrides)
    if not requested:
        report.add("injection-applied", "skip", "no injected_processes/swap_processes requested")
        return
    if document is None:
        report.add(
            "injection-applied", "fail",
            "cannot verify the requested process swap(s) — the composite did not build",
        )
        return

    addresses, carried = _collect_injection_evidence(document)
    dropped: list[str] = []
    for slot, token in requested:
        in_addresses = _last_segment(token) in addresses
        in_carried = slot in carried
        if not (in_addresses or in_carried):
            dropped.append(f"{slot!r} -> {token!r}")

    if dropped:
        report.add(
            "injection-applied", "fail",
            "requested process swap(s) NOT present in the built composite — the "
            "metabolism-redux swap was silently dropped (audit §2.2 / P0-1; this is "
            "the batch-mode _build_batch_document drop): " + ", ".join(dropped),
        )
    else:
        report.add(
            "injection-applied", "pass",
            f"all {len(requested)} requested swap(s) present in the built composite",
        )


# ---------------------------------------------------------------------------
# Check 3 — declared emit paths resolve
# ---------------------------------------------------------------------------

def _emit_paths_from_document(document: dict) -> list[str]:
    """Emit paths recovered registry-free from the document's emitter nodes.

    Mirrors ``run_runner._emit_paths_from_state`` — walks the state tree, finds
    every ``*Emitter`` node, and returns the '/'-joined store paths it is wired
    to read (skipping internal ``_``-prefixed ports).
    """
    state = document.get("state", document) if isinstance(document, dict) else {}
    out: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            addr = str(node.get("address", ""))
            if addr.split(":")[-1].endswith("Emitter"):
                wires = node.get("inputs")
                if isinstance(wires, dict):
                    for port, target in wires.items():
                        if str(port).startswith("_"):
                            continue
                        segs = target if isinstance(target, list) else [target]
                        norm = "/".join(str(s) for s in segs if s not in (None, ""))
                        if norm and norm not in out:
                            out.append(norm)
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(state)
    return out


def _has_emitter_node(document: dict) -> bool:
    found = [False]

    def _walk(node: Any) -> None:
        if found[0]:
            return
        if isinstance(node, dict):
            if str(node.get("address", "")).split(":")[-1].endswith("Emitter"):
                found[0] = True
                return
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(document.get("state", document) if isinstance(document, dict) else {})
    return found[0]


def _resolve_store_path(state: dict, path: str) -> "tuple[bool, bool]":
    """Resolve ``path`` (``a/b/c``) against ``state``.

    Returns ``(exists, is_empty_dict)``: ``exists`` is whether every segment
    resolves; ``is_empty_dict`` flags the §2.9 case where a declared path was
    materialized as an empty ``{}`` (emits nothing).
    """
    node: Any = state
    for seg in [s for s in path.split("/") if s]:
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return False, False
    return True, (isinstance(node, dict) and len(node) == 0)


def _check_emit_paths(document: "dict | None", extra_paths: "list[str] | None", report: PreflightReport) -> None:
    if document is None:
        report.add("emit-paths", "skip", "composite did not build")
        return

    state = document.get("state", document) if isinstance(document, dict) else {}
    from_doc = _emit_paths_from_document(document)
    extra = list(extra_paths or [])
    declared = from_doc + [p for p in extra if p not in from_doc]

    if not declared:
        report.add(
            "emit-paths", "skip",
            "no emitter node / emit paths in the built document — for a batch "
            "composite the cell-level emitter is built inside the runner at run "
            "time; leaf-KPI reconciliation is a postflight concern",
        )
        return

    # Paths recovered from an emitter node in THIS document must resolve here.
    # Externally-supplied paths for a batch composite may legitimately target
    # cell-level stores built at run time, so a miss there is a note, not a fail.
    is_batch = _infer_shape(document, {}) == "batch"
    problems: list[str] = []
    notes: list[str] = []
    for path in declared:
        exists, empty = _resolve_store_path(state, path)
        # Agents-wrapped whole-cell composites nest the cell — and its bulk /
        # listeners stores — under agents/<id>/, with the emitter placed INSIDE
        # the agent (e.g. ecoli_baseline's /agents/0/emitter). The emitter's
        # cell-relative wires ('bulk', 'listeners') are recovered here without
        # the agents/<id>/ prefix, so a top-level resolve misses a store that
        # genuinely exists and IS emitted at run time. Retry under each agent
        # before declaring a miss. This only ADDS resolution — a path that
        # resolves nowhere still fails, so the §2.9 protection is intact.
        if not exists:
            agents = state.get("agents") if isinstance(state, dict) else None
            if isinstance(agents, dict):
                for agent in agents.values():
                    if isinstance(agent, dict):
                        a_exists, a_empty = _resolve_store_path(agent, path)
                        if a_exists:
                            exists, empty = a_exists, a_empty
                            break
        from_emitter_node = path in from_doc
        if exists and not empty:
            continue
        if not exists:
            msg = f"{path!r} does not resolve to a store in the built composite"
        else:
            msg = f"{path!r} resolves to an empty dict (emitter would emit nothing — §2.9)"
        if from_emitter_node or not is_batch:
            problems.append(msg)
        else:
            notes.append(msg + " (cell-level store built at run time — deferred)")

    if problems:
        report.add(
            "emit-paths", "fail",
            "declared emit path(s) would silently emit nothing (§2.9): " + "; ".join(problems),
        )
    else:
        detail = f"all {len(declared)} declared emit path(s) resolve to real stores"
        if notes:
            detail += " (" + "; ".join(notes) + ")"
        report.add("emit-paths", "pass", detail)


# ---------------------------------------------------------------------------
# Check 4 — variant expansion
# ---------------------------------------------------------------------------

def expand_variant_count(variants: Any) -> int:
    """Count the branches a ``variants`` grid expands to.

    Prefers v2ecoli's own ``expand_branches`` (authoritative) when importable;
    otherwise uses a self-contained expander with the same shape semantics:

    - grid = ``{name: {"value": [...], "op": "product"|"zip"}}``;
    - an empty ``value`` list contributes 0 branches (silently deletes the arm);
    - a single param -> ``len(value)``; multiple params -> product, or the
      shortest-``value`` length when any op is ``"zip"``.
    """
    if not variants:
        return 0

    # Authoritative path: v2ecoli's own expander.
    try:  # pragma: no cover - exercised only where v2ecoli is installed
        from v2ecoli.workflow.variants import expand_branches, parse_variant_params

        parsed = parse_variant_params(variants)
        return len(expand_branches(parsed))
    except Exception:
        pass

    # Self-contained fallback (workbench venv, no v2ecoli).
    if not isinstance(variants, dict):
        return 0
    lengths: list[int] = []
    ops: list[str] = []
    for spec in variants.values():
        if isinstance(spec, dict) and "value" in spec:
            value = spec.get("value")
            ops.append(str(spec.get("op") or "product"))
        else:
            value = spec
            ops.append("product")
        if isinstance(value, (list, tuple)):
            lengths.append(len(value))
        else:
            lengths.append(1)
    if not lengths:
        return 0
    if any(n == 0 for n in lengths):
        return 0
    if len(lengths) == 1:
        return lengths[0]
    if any(op == "zip" for op in ops):
        return min(lengths)
    count = 1
    for n in lengths:
        count *= n
    return count


def _check_variant_expansion(overrides: dict, expected: "int | None", report: PreflightReport) -> None:
    variants = overrides.get("variants")
    if not variants:
        if expected:
            report.add(
                "variant-expansion", "fail",
                f"caller expects {expected} variant branch(es) but the overrides "
                f"carry no 'variants' grid",
            )
        else:
            report.add("variant-expansion", "skip", "no variant grid in overrides")
        return

    count = expand_variant_count(variants)
    if count == 0:
        report.add(
            "variant-expansion", "fail",
            "the 'variants' grid expands to 0 branches — an empty value list "
            "silently deletes a whole arm (audit §3.5)",
        )
        return
    if expected is not None and count != expected:
        report.add(
            "variant-expansion", "fail",
            f"the 'variants' grid expands to {count} branch(es), but the caller "
            f"expects {expected} (audit §3.5)",
        )
        return
    detail = f"variant grid expands to {count} branch(es)"
    if expected is not None:
        detail += f" (matches expected {expected})"
    report.add("variant-expansion", "pass", detail)


# ---------------------------------------------------------------------------
# Check 5 — step count vs composite shape
# ---------------------------------------------------------------------------

def _infer_shape(document: "dict | None", overrides: dict) -> str:
    """Return ``"batch"`` or ``"single-cell"`` for the composite.

    Batch = a ``BatchBaselineRunner`` node, or an override / node flag that puts
    the whole sweep inside one Step (``stop_at_division``, ``n_seeds`` > 1,
    ``n_generations`` > 1). Everything else is a per-step temporal single-cell run.
    """
    def _num(v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    if _num(overrides.get("n_seeds")) > 1 or _num(overrides.get("n_generations")) > 1 \
            or overrides.get("stop_at_division"):
        return "batch"

    is_batch = [False]

    def _walk(node: Any) -> None:
        if is_batch[0]:
            return
        if isinstance(node, dict):
            if _last_segment(node.get("address", "")).endswith("BatchBaselineRunner"):
                is_batch[0] = True
                return
            if node.get("stop_at_division") or _num(node.get("n_seeds")) > 1 \
                    or _num(node.get("n_generations")) > 1:
                is_batch[0] = True
                return
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk((document or {}).get("state", document) if isinstance(document, dict) else {})
    return "batch" if is_batch[0] else "single-cell"


def _check_step_shape(document: "dict | None", overrides: dict, n_steps: "int | None", report: PreflightReport) -> None:
    if n_steps is None:
        report.add("step-shape", "skip", "no step count supplied")
        return

    if int(n_steps) <= 0:
        report.add(
            "step-shape", "fail",
            f"step count is {n_steps} — a zero/negative-step run reports success "
            f"while doing nothing (audit §3.3)",
        )
        return

    shape = _infer_shape(document, overrides)
    if shape == "batch":
        if int(n_steps) == 1:
            report.add("step-shape", "pass", "batch composite with n_steps=1 (the sweep runs inside one Step)")
        else:
            report.add(
                "step-shape", "pass",
                f"batch composite: n_steps={n_steps} (a batch sweep runs inside one "
                f"Step, so 1 is canonical; {n_steps} is tolerated but check it is intended — §3.3)",
            )
    else:
        report.add(
            "step-shape", "pass",
            f"single-cell composite with n_steps={n_steps} "
            f"(single-cell is a hard per-step ceiling — ensure it covers the intended generations — §3.3)",
        )


# ---------------------------------------------------------------------------
# Check 6 — ParCa cache
# ---------------------------------------------------------------------------

def _check_parca_cache(overrides: dict, report: PreflightReport) -> None:
    cache = overrides.get("cache_dir") or overrides.get("cache") or overrides.get("parca_cache")
    if not cache:
        report.add("parca-cache", "skip", "no cache_dir override to check")
        return
    cache_str = str(cache)
    if cache_str.startswith(("s3://", "s3a://", "gs://")):
        report.add("parca-cache", "skip", f"remote cache {cache_str!r} — HeadObject check needs live infra (deferred)")
        return
    if Path(cache_str).expanduser().exists():
        report.add("parca-cache", "pass", f"local ParCa cache {cache_str!r} exists")
    else:
        report.add(
            "parca-cache", "fail",
            f"ParCa cache_dir {cache_str!r} does not exist locally — the run would "
            f"stage an empty cache and start against wild-type ParCa (audit §2.13)",
        )
