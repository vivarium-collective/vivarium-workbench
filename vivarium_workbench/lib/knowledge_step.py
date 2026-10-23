"""KnowledgeStep — a process-bigraph Step that derives the investigation
knowledge store (Concepts + canonical Verdicts) at an event boundary.

It is the thin pbg adapter around
``viva_superpowers.knowledge_store.derive_knowledge``: the pure derivation and
its unit tests live in viva-superpowers; this Step wires it into the engine.
Like the other investigation Steps it runs at a transaction boundary, NOT on the
biological simulation schedule, and writes a read-only ``knowledge`` node — it
mutates no study.yaml or investigation.yaml.

Test hook: assign a dict ``{"inv_spec": ..., "studies_by_slug": ...}`` to
``_KNOWLEDGE_INPUTS`` to inject inputs hermetically instead of loading from disk
(mirrors the ``_RUN_ORDER`` convention in ``investigation_steps.py``). None in
production.
"""
from __future__ import annotations

import process_bigraph

# Hermetic test hook: {"inv_spec": dict, "studies_by_slug": dict} or None.
_KNOWLEDGE_INPUTS: dict | None = None


def _load_knowledge_inputs(workspace: str, inv_slug: str) -> tuple[dict, dict]:
    """Resolve (inv_spec, studies_by_slug) for an investigation.

    Honors the hermetic test hook first; otherwise loads the investigation.yaml
    and each member study.yaml from disk through the canonical path resolvers
    (layout-aware, explicit workspace root — no global state). A member listed
    in ``studies[]`` with no study.yaml is simply absent from the returned map
    (``derive_knowledge`` skips it).
    """
    if _KNOWLEDGE_INPUTS is not None:
        return _KNOWLEDGE_INPUTS["inv_spec"], _KNOWLEDGE_INPUTS["studies_by_slug"]

    from pathlib import Path

    import yaml

    from vivarium_workbench.lib import workspace_paths
    from vivarium_workbench.lib.investigation_status import investigation_yaml_path

    ws = Path(workspace)
    inv_path = investigation_yaml_path(ws, inv_slug)
    inv_spec = {}
    if inv_path and inv_path.exists():
        inv_spec = yaml.safe_load(inv_path.read_text()) or {}

    studies_by_slug: dict[str, dict] = {}
    for slug in inv_spec.get("studies") or []:
        sp = workspace_paths.study_yaml_path(ws, slug)
        if sp and sp.exists():
            studies_by_slug[slug] = yaml.safe_load(sp.read_text()) or {}

    return inv_spec, studies_by_slug


class KnowledgeStep(process_bigraph.Step):
    config_schema = {
        "workspace": "string",
        "investigation_slug": "string",
    }

    def inputs(self):
        return {}

    def outputs(self):
        return {"knowledge": "node"}

    def update(self, state=None):
        from viva_superpowers.knowledge_store import derive_knowledge

        inv_spec, studies_by_slug = _load_knowledge_inputs(
            self.config["workspace"], self.config["investigation_slug"])
        return {"knowledge": derive_knowledge(inv_spec, studies_by_slug)}
