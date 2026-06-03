"""Spec-driven HPC report-generator dispatch.

Resolves the ``report_generator`` block declared by a study (per-entry on
``simulation_set[*].report_generator``, falling back to top-level
``study.report_generator``) into a per-task command + parameter set
suitable for ``submit_investigation_array_job``.

The dashboard is the *consumer* of pbg-compliance — it never imports
workspace-specific packages.  Workspaces declare what the dashboard
should invoke; this module renders those declarations into commands.
"""
from __future__ import annotations

import re


class ReportGeneratorError(ValueError):
    """Raised when a report_generator block is malformed or templates
    cannot be rendered against the per-task substitution context."""


_STEPS_CLAMP_RE = re.compile(r"\{steps_clamped:(\d+)\}")


def resolve_for_entry(study_spec: dict, entry: dict) -> dict | None:
    """Return the resolved report_generator for one simulation_set entry.

    Per-entry overrides top-level.  Returns ``None`` if neither is
    declared.  The returned dict is a shallow copy so callers can mutate
    safely.
    """
    per_entry = entry.get("report_generator") if isinstance(entry, dict) else None
    if isinstance(per_entry, dict):
        return dict(per_entry)
    top = study_spec.get("report_generator") if isinstance(study_spec, dict) else None
    if isinstance(top, dict):
        return dict(top)
    return None


def render_args(
    args: dict,
    *,
    run_id: str,
    overrides: dict,
    steps: int,
) -> dict[str, str]:
    """Render each value in ``args`` via str.format() against the
    per-task substitution context.

    Substitution keys:
        {run_id}              dashboard-generated run id
        {overrides[KEY]}      per-task overrides dict (bracket form)
        {steps}               per-task step count
        {steps_clamped:N}     min(max(steps, 1), N) — bounded duration

    Static-string values pass through unchanged.  Non-string template
    values are coerced via ``str(...)``.  Missing template keys raise
    ReportGeneratorError naming the missing key.
    """
    if not isinstance(args, dict):
        raise ReportGeneratorError(
            f"report_generator.args must be a dict, got {type(args).__name__}"
        )
    rendered: dict[str, str] = {}
    ctx = {"run_id": run_id, "overrides": overrides or {}, "steps": steps}
    for key, raw in args.items():
        if raw is None:
            raise ReportGeneratorError(
                f"report_generator.args[{key!r}] is null"
            )
        template = raw if isinstance(raw, str) else str(raw)
        # Pre-substitute the {steps_clamped:N} sentinel because str.format()
        # cannot express the clamp with its native spec language.
        def _clamp_sub(m: re.Match) -> str:
            cap = int(m.group(1))
            return str(max(1, min(steps, cap)))
        pre = _STEPS_CLAMP_RE.sub(_clamp_sub, template)
        try:
            rendered[key] = pre.format(**ctx)
        except KeyError as exc:
            missing = exc.args[0] if exc.args else "?"
            raise ReportGeneratorError(
                f"report_generator.args[{key!r}] references missing key {missing!r}; "
                f"available: run_id, overrides[*], steps, steps_clamped:N"
            ) from exc
        except (IndexError, ValueError) as exc:
            raise ReportGeneratorError(
                f"report_generator.args[{key!r}] template error: {exc}"
            ) from exc
    return rendered


def build_dispatch(
    tasks: list[dict],
    generators: list[dict],
    *,
    remote_ws: str,
    sif_path: str,
) -> tuple[list[dict], str]:
    """Build (param_values, cmd_tmpl) for an array-task report dispatch.

    Each entry in ``generators`` corresponds 1:1 with ``tasks``.  All
    generators must share the same ``script`` and ``output_dir``
    (mixing is rejected — return-via-raise so the caller surfaces a 400).

    The returned command template has per-task substitution slots named
    after each generator arg key (plus ``run_id``).  ``param_values`` is
    the list of substitution dicts that ``submit_investigation_array_job``
    will plug into the template.
    """
    if len(tasks) != len(generators):
        raise ReportGeneratorError(
            f"tasks/generators length mismatch: {len(tasks)} vs {len(generators)}"
        )
    if not tasks:
        raise ReportGeneratorError("no tasks to dispatch")

    scripts = {g.get("script") for g in generators}
    if len(scripts) > 1 or None in scripts:
        raise ReportGeneratorError(
            f"report_generator.script must be declared and identical across tasks; "
            f"got {sorted(s for s in scripts if s)!r}"
        )
    script = next(iter(scripts))
    if not isinstance(script, str) or not script.strip():
        raise ReportGeneratorError("report_generator.script must be a non-empty string")
    if script.startswith("/") or ".." in script.split("/"):
        raise ReportGeneratorError(
            f"report_generator.script must be workspace-relative, not {script!r}"
        )

    output_dirs = {g.get("output_dir") for g in generators}
    if len(output_dirs) > 1:
        raise ReportGeneratorError(
            f"report_generator.output_dir must be identical across tasks; "
            f"got {sorted(o for o in output_dirs if o)!r}"
        )
    output_dir = next(iter(output_dirs)) or ""

    # Render per-task args, then derive the union of keys for the bash
    # template.  Each task's param_values dict must carry every key the
    # template references; render_args guarantees presence per task, and
    # we assert the keysets match.
    rendered_per_task: list[dict[str, str]] = []
    arg_key_sets: list[frozenset[str]] = []
    for task, gen in zip(tasks, generators):
        args = gen.get("args") or {}
        rendered = render_args(
            args,
            run_id=task["run_id"],
            overrides=task.get("overrides") or {},
            steps=int(task.get("steps") or 1),
        )
        rendered_per_task.append(rendered)
        arg_key_sets.append(frozenset(rendered.keys()))

    if len(set(arg_key_sets)) > 1:
        raise ReportGeneratorError(
            "report_generator.args keys must be identical across tasks; "
            f"got {[sorted(s) for s in arg_key_sets]!r}"
        )
    arg_keys = sorted(arg_key_sets[0])

    # Build the param_values: each task's CLI args + its run_id (latter
    # is informational; the substitution into the bash template happens
    # via {key} slots).
    param_values: list[dict] = []
    for task, rendered in zip(tasks, rendered_per_task):
        pv = {"run_id": task["run_id"]}
        for k in arg_keys:
            # Bash substitution slot names can't contain hyphens; sanitize.
            slot = _slot_name(k)
            pv[slot] = rendered[k]
        param_values.append(pv)

    # Build the command template.  We reconstruct the CLI flags using
    # slot-named placeholders so submit_investigation_array_job can
    # substitute per-task.  Quoting follows the same single-quote rule
    # as _format_cli_flags (applied to the rendered value, not the slot).
    flag_parts: list[str] = []
    for k in arg_keys:
        flag = ("-" + k) if len(k) == 1 else ("--" + k)
        slot = _slot_name(k)
        flag_parts.append(f"{flag} {{{slot}}}")
    flags = " ".join(flag_parts)

    # mkdir output_dir under /app so the report script can write into it.
    # We mount remote_ws/out:/app/out, so the report's writes land in the
    # workspace's out/ tree and rsync-back picks them up.
    mkdir_clause = ""
    if output_dir:
        # output_dir is workspace-relative (e.g. "out/colony" → /app/out/colony).
        rel = output_dir.lstrip("/")
        if ".." in rel.split("/"):
            raise ReportGeneratorError(
                f"report_generator.output_dir must be workspace-relative, not {output_dir!r}"
            )
        mkdir_clause = f"mkdir -p /app/{rel} && "

    cmd_tmpl = (
        f"apptainer exec "
        f"-B {remote_ws}/results:/app/results "
        f"-B {remote_ws}/out:/app/out "
        f"-B {remote_ws}:/workspace "
        f"{sif_path} "
        f"bash -c 'export PATH=/app/.venv/bin:$PATH; "
        f"cd /app && {mkdir_clause}"
        f"exec /app/.venv/bin/python3 /workspace/{script} {flags}'"
    )
    return param_values, cmd_tmpl


def _slot_name(key: str) -> str:
    """Sanitize an arg key for use as a {placeholder} in the bash template.

    Bash + Python str.format() both choke on hyphens inside braces.
    Replace with underscores; the dashboard never reads these slot
    names back, so we don't need a reversible mapping.
    """
    return re.sub(r"[^A-Za-z0-9_]", "_", key)
