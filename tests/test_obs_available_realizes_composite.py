"""Regression for #1471: ``env_worker._obs_available`` must introspect the
REALIZED composite state, not the raw generator document.

A composite may declare a store empty (``vitals: {}``) while a process fills it
through its OUTPUT wires (``vitals/<name>``). The raw document therefore has no
leaves under that store — so every readout pointing there was flagged
``not_in_structure`` (the never-fabricate guard), permanently blocking the
check-observables hard gate. Building the ``Composite`` runs ``initial_state()``
plus the link_state pass, materialising the output-wired stores; the leaves are
then present.

The composite + process here are tiny and synthetic (no viva-pulse dependency),
and the test drives the exact function the ``/api/observables`` /
check-observables path uses: ``env_worker._obs_available``.
"""
from __future__ import annotations

from process_bigraph import Process, allocate_core

from vivarium_workbench import env_worker
from vivarium_workbench.lib.readout_validation import validate_readouts


class _VitalsWriter(Process):
    """Writes vitals through OUTPUT wires into a store left empty by the document."""

    config_schema: dict = {}

    def inputs(self):
        return {}

    def outputs(self):
        return {"heart_rate": "float", "temperature": "float"}

    def update(self, state, interval=1.0):
        return {"heart_rate": 70.0, "temperature": 37.0}


def _synthetic_core():
    core = allocate_core()
    core.register_link("VitalsWriter", _VitalsWriter)
    return core


# A document that declares ``vitals`` EMPTY; the process fills vitals/<name> via
# its output wires. Pre-filling the store is deliberately avoided (document
# values override initial_state()), exactly the shape #1471 describes.
_DOC_STATE = {
    "vitals_writer": {
        "_type": "process",
        "address": "local:VitalsWriter",
        "config": {},
        "inputs": {},
        "outputs": {
            "heart_rate": ["vitals", "heart_rate"],
            "temperature": ["vitals", "temperature"],
        },
        "interval": 1.0,
    },
    "vitals": {},  # declared empty; filled only through the process's outputs
}


def _run_obs_available(monkeypatch):
    """Drive ``_obs_available`` with the synthetic core (static ``{state}`` branch,
    so no workspace registry is needed)."""
    monkeypatch.setattr(env_worker, "_obs_build_core", _synthetic_core)
    # Isolate from cwd / any real workspace package on disk.
    monkeypatch.setattr(env_worker, "_import_workspace_package", lambda ws: None)
    return env_worker._obs_available({"state": _DOC_STATE, "schema": None})


def test_obs_available_reports_output_wired_store_leaves(monkeypatch):
    """The output-wired store's leaves are present after realization (#1471)."""
    result = _run_obs_available(monkeypatch)

    assert not any(k.startswith("__") for k in result), (
        f"_obs_available returned a sentinel, not leaves: {result}"
    )
    leaves = result["leaves"]

    # The real, realized store leaves — absent when walking the raw document.
    assert "vitals.heart_rate" in leaves, leaves
    assert "vitals.temperature" in leaves, leaves

    # And the raw-document artefacts (the process node's own wire/config keys)
    # must NOT be reported as stores — they are gone once the composite is built.
    assert not any(l.startswith("vitals_writer") for l in leaves), leaves


def test_output_wired_readout_is_not_flagged_not_in_structure(monkeypatch):
    """A readout pointing at an output-wired leaf validates as ``ok``, not
    ``not_in_structure`` (the gate the bug permanently blocked)."""
    result = _run_obs_available(monkeypatch)
    available = {
        "leaves": result["leaves"],
        "catalogs": result.get("catalogs", {}),
    }
    spec = {"readouts": [{"name": "hr", "identifier": "vitals.heart_rate"}]}
    status = validate_readouts(spec, available=available)[0]
    assert status["status"] == "ok", status
