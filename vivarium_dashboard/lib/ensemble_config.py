"""SP2a — translate a dashboard sweep/seeds variant into a v2ecoli-workflow
config and decide whether it can be delegated.

A ``kind: sweep`` / ``kind: seeds`` variant is NOT executed as N independent
dashboard subprocesses. It is DELEGATED to v2ecoli's ``v2ecoli-workflow``
ensemble machinery (``meta_composite``), which packs every grid point into ONE
parquet hive store (``variant`` / ``lineage_seed`` partition dims) that the
existing analysis already reads. The dashboard is a thin translator + invoker.

Grounded anchors (do not invent keys):
  * config schema  — ``v2ecoli/v2ecoli/configs/default.json``
  * variant grammar — ``v2ecoli/v2ecoli/workflow/variants.py`` (``target`` is
    ``"<process-name>.<config-key>"``; multi-param blocks combine via top-level
    ``op``: ``prod`` | ``zip`` | ``add``).

The ``target`` MUST already be ``<proc>.<key>`` — the dashboard does NOT
translate composite-param names into process addresses, so a bare-key
``sweep_over`` is non-delegatable (see :func:`is_delegatable_sweep`).

The xarray emitter branch does NOT pack (each branch gets its own zarr —
``v2ecoli/workflow/lineage.py``), so we FORCE ``emitter: parquet`` here; parquet
shares one ``out_dir`` hive store.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# Inline default mirroring v2ecoli/v2ecoli/configs/default.json. We keep this
# inline (rather than reading the file) so the pure translator has no dependency
# on a v2ecoli checkout location — availability detection lives in
# delegation_available(), and the workflow CLI fills the rest from its own
# default on inheritance.
_DEFAULT_WORKFLOW_CONFIG: dict[str, Any] = {
    "experiment_id": "default",
    "generations": 1,
    "n_init_sims": 1,
    "single_daughters": True,
    "lineage_seed": 0,
    "different_seeds_per_variant": False,
    "skip_baseline": False,
    "cache_dir": "out/cache",
    "out_dir": "out/workflow",
    "time_step": 1.0,
    "max_duration_per_gen": 3600.0,
    "variants": {},
    "analysis_options": {},
}


def build_workflow_config(
    variant: dict[str, Any],
    experiment_id: str,
    out_dir: str,
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Translate a dashboard sweep/seeds ``variant`` into a v2ecoli-workflow config.

    Mapping:
      * ``n_seeds``     → ``n_init_sims`` (number of lineage seeds per point).
      * ``generations`` → ``generations`` (default 1 when absent).
      * ``sweep_over``  → ``variants`` block. Each ``{"<proc>.<key>": [vals]}``
        entry becomes a named param ``{"<key-suffix>": {"target": "<proc>.<key>",
        "value": [vals]}}``; the ``target`` is preserved VERBATIM (it must
        already be ``<proc>.<key>``). Two or more params add a top-level
        ``op: "prod"`` (cartesian grid) per the variant grammar.
      * ``experiment_id`` / ``out_dir`` are set from the arguments.
      * ``emitter`` is FORCED to ``"parquet"`` (the only packing emitter).
    """
    cfg: dict[str, Any] = dict(base if base is not None else _DEFAULT_WORKFLOW_CONFIG)

    n_seeds = variant.get("n_seeds")
    if n_seeds is not None:
        cfg["n_init_sims"] = int(n_seeds)

    # generations comes from the variant; default 1 when not declared.
    cfg["generations"] = int(variant.get("generations", 1))

    sweep_over = variant.get("sweep_over") or {}
    if sweep_over:
        variants_block: dict[str, Any] = {}
        for target, values in sweep_over.items():
            # Param name is the key suffix after the final "." — the full key is
            # the workflow target (<proc>.<config-key>), kept verbatim.
            param_name = str(target).rsplit(".", 1)[-1]
            variants_block[param_name] = {
                "target": target,
                "value": list(values),
            }
        # >1 param needs a combine op; default to the cartesian product.
        if len(sweep_over) > 1:
            variants_block["op"] = "prod"
        cfg["variants"] = variants_block

    cfg["experiment_id"] = experiment_id
    cfg["out_dir"] = out_dir
    cfg["emitter"] = "parquet"  # forced — xarray branch does not pack
    return cfg


def is_delegatable_sweep(variant: dict[str, Any]) -> bool:
    """True iff ``variant`` is an ensemble that v2ecoli-workflow can pack.

    Delegatable when EITHER:
      * ``kind == "seeds"`` with ``n_seeds >= 1`` (a pure lineage-seed ensemble), OR
      * ``kind == "sweep"`` AND every ``sweep_over`` key is a ``<proc>.<key>``
        target (contains a ``.``). A bare composite-param name cannot be a
        workflow target, so a bare-key sweep is NOT delegatable (it must error
        clearly rather than half-run).

    Anything else (a plain variant, an empty sweep) is not delegatable.
    """
    if not isinstance(variant, dict):
        return False
    kind = variant.get("kind")
    if kind == "seeds":
        n_seeds = variant.get("n_seeds")
        return n_seeds is not None and int(n_seeds) >= 1
    if kind == "sweep":
        sweep_over = variant.get("sweep_over") or {}
        if not sweep_over:
            return False
        return all("." in str(key) for key in sweep_over)
    return False


def delegation_available(ws_root) -> bool:
    """True iff ``ws_root`` can delegate an ensemble to v2ecoli-workflow.

    A cheap filesystem check — deliberately does NOT import v2ecoli into the
    dashboard process. Available iff ``<ws>/.venv/bin/v2ecoli-workflow`` exists
    (the console script is installed in the workspace venv).

    Review FIX 2: the binary is ALWAYS required. A ``package_path: v2ecoli``
    declaration alone is NOT sufficient — without the console script,
    :func:`_invoke_v2ecoli_workflow`'s ``subprocess.run`` would raise an uncaught
    ``FileNotFoundError``. Requiring the binary here makes a delegatable sweep on
    such a workspace return the clear v2ecoli-required 422 instead.
    """
    ws = Path(ws_root)
    return (ws / ".venv" / "bin" / "v2ecoli-workflow").exists()
