"""Composite-level contract audit — the real roll-up behind a composite card's
contract badge (previously hardcoded ``{"status": "unavailable"}`` because the
Composites list is discovered *coreless*).

Two layers, both requiring a built core + a live ``Composite`` (so this runs in
the workspace env-worker, where ``build_core`` lives):

  Phase 1 — per-process roll-up.  Run the existing per-process audit
    (:func:`vivarium_workbench.lib.contract_catalog.contract_audit_payload`) on
    every process/step the composite instantiates and aggregate one composite
    status (fail > incomplete > not-declared > pass).

  Phase 2 — wiring / interface audit.  For every store that two or more ports
    wire to, check the wired ports' declared TYPES are mutually resolvable
    (``core.resolve_schemas``) and their units dimensionally compatible
    (``core._compute_unit_scale``) — i.e. the processes AGREE on the type of
    each shared store.  Disagreements become composite-level findings.

Pure + core-only: no I/O, no HTTP.  Takes a live ``process_bigraph.Composite``
and its ``core``; returns a JSON-safe dict.  Any failure degrades to
``{"status": "unavailable", "notice": ...}`` rather than raising — one broken
process must never sink the card.
"""
from __future__ import annotations

from statistics import fmean
from typing import Any

_INCOMPLETE_BELOW = 0.5


def _addr_str(addr: Any) -> str:
    """Normalize a process node's ``address`` (dict ``{protocol,data}`` from a
    live ``process_paths`` entry, or an already-dotted/``local:`` string) to the
    string form :func:`contract_audit_payload` expects."""
    if isinstance(addr, dict):
        proto = addr.get("protocol") or "local"
        data = addr.get("data") or ""
        return f"{proto}:{data}" if data else ""
    return str(addr or "")


def _store_key(path: Any) -> str:
    """A stable display key for a wire's store path (``['fields','IFNg']`` →
    ``"fields/IFNg"``)."""
    if isinstance(path, (list, tuple)):
        return "/".join(str(p) for p in path)
    return str(path)


def _node_name(path: tuple, addr_str: str) -> str:
    """Friendly process label: the composite node key, falling back to the
    class name in the address."""
    if path:
        return str(path[-1])
    return addr_str.split(":")[-1] if addr_str else "process"


def _short_type(core: Any, schema: Any) -> str:
    """A compact type string for a finding message; defensive against odd
    schema objects (``core.render`` can be verbose or raise)."""
    try:
        rendered = core.render(schema)
    except Exception:  # noqa: BLE001
        rendered = schema
    if isinstance(rendered, dict):
        rendered = rendered.get("_type", rendered)
    s = str(rendered)
    return s if len(s) <= 80 else s[:77] + "…"


def _safe_units(core: Any, schema: Any) -> str:
    try:
        return core._port_units(schema) or ""
    except Exception:  # noqa: BLE001
        return ""


def _audit_processes(core: Any, process_paths: dict) -> list[dict]:
    """Phase 1: per-process audit of every process/step in the composite."""
    from vivarium_workbench.lib.contract_catalog import contract_audit_payload

    out: list[dict] = []
    for path, node in process_paths.items():
        addr_str = _addr_str(node.get("address"))
        try:
            rep = contract_audit_payload(core, addr_str)
        except Exception as error:  # noqa: BLE001 — one process must not sink the card
            rep = {"status": "error", "message": str(error)}
        ports = rep.get("ports") or {}
        out.append({
            "path": list(path),
            "name": _node_name(path, addr_str),
            "address": addr_str,
            "status": rep.get("status"),
            "grade": rep.get("grade"),
            "completeness": rep.get("completeness"),
            "findings": rep.get("findings") or [],
            "n_inputs": len((ports.get("inputs") or {})),
            "n_outputs": len((ports.get("outputs") or {})),
        })
    return out


def _audit_wiring(core: Any, process_paths: dict) -> list[dict]:
    """Phase 2: for every store wired by >=2 ports, check the ports' declared
    types resolve together and their units are dimensionally compatible."""
    # store key -> list of {proc, port, direction, schema}
    wires: dict[str, list[dict]] = {}
    for path, node in process_paths.items():
        proc = _node_name(path, _addr_str(node.get("address")))
        for direction, wire_key, type_key in (
            ("input", "inputs", "_inputs"),
            ("output", "outputs", "_outputs"),
        ):
            wiring = node.get(wire_key) or {}
            types = node.get(type_key) or {}
            if not isinstance(wiring, dict):
                continue
            for port, store_path in wiring.items():
                wires.setdefault(_store_key(store_path), []).append({
                    "proc": proc, "port": port,
                    "direction": direction, "schema": types.get(port),
                })

    findings: list[dict] = []
    for store_key, ports in wires.items():
        typed = [p for p in ports if p["schema"] is not None]
        if len(typed) < 2:
            continue
        where = store_key
        # Type agreement: all ports on the store must resolve together.
        try:
            core.resolve_schemas([p["schema"] for p in typed])
        except Exception as error:  # noqa: BLE001 — a raise IS the incompatibility signal
            detail = ", ".join(
                f"{p['proc']}.{p['direction']}s.{p['port']} ({_short_type(core, p['schema'])})"
                for p in typed
            )
            findings.append({
                "severity": "error", "code": "wire-type-mismatch", "where": where,
                "message": f"store '{where}' is wired with incompatible port types: {detail}"
                           f" — {str(error).splitlines()[0][:120]}",
            })
            continue  # a type mismatch subsumes a units mismatch
        # Units agreement: pairwise dimensional compatibility of declared units.
        unit_ports = [(p, _safe_units(core, p["schema"])) for p in typed]
        unit_ports = [(p, u) for p, u in unit_ports if u]
        for i in range(len(unit_ports)):
            for j in range(i + 1, len(unit_ports)):
                (pa, ua), (pb, ub) = unit_ports[i], unit_ports[j]
                if ua == ub:
                    continue
                try:
                    core._compute_unit_scale(ua, ub)
                except Exception:  # noqa: BLE001 — incompatible dimensionality
                    findings.append({
                        "severity": "warning", "code": "wire-unit-mismatch", "where": where,
                        "message": f"store '{where}': {pa['proc']}.{pa['port']} declares "
                                   f"units '{ua}' but {pb['proc']}.{pb['port']} declares '{ub}'",
                    })
    return findings


def audit_composite(core: Any, comp: Any) -> dict:
    """Audit a live composite's contracts + wiring.

    ``comp`` is a built ``process_bigraph.Composite`` (its ``process_paths``
    carries resolved port-type schemas + wiring); ``core`` is the composite's
    core.  Returns a JSON-safe roll-up — never raises.
    """
    try:
        process_paths = comp.process_paths
        if callable(process_paths):
            process_paths = process_paths()
    except Exception as error:  # noqa: BLE001
        return {"status": "unavailable", "notice": f"no process paths: {error}"}
    if not process_paths:
        return {"status": "not-declared", "grade": None, "n_processes": 0,
                "processes": [], "wiring": {"status": "pass", "findings": []}}

    processes = _audit_processes(core, process_paths)
    try:
        wire_findings = _audit_wiring(core, process_paths)
    except Exception as error:  # noqa: BLE001 — wiring audit must not sink Phase 1
        wire_findings = []
        _ = error

    statuses = [p["status"] for p in processes]
    grades = [p["grade"] for p in processes if isinstance(p.get("grade"), (int, float))]
    wire_error = any(f["severity"] == "error" for f in wire_findings)
    wire_warn = any(f["severity"] == "warning" for f in wire_findings)

    if any(s == "fail" for s in statuses) or wire_error:
        status = "fail"
    elif any(s == "incomplete" for s in statuses):
        status = "incomplete"
    elif statuses and all(s == "not-declared" for s in statuses):
        status = "not-declared"
    elif statuses and not any(s in ("pass", "incomplete", "fail", "not-declared") for s in statuses):
        # every process errored/unavailable — surface it honestly
        status = "error"
    else:
        status = "pass"

    return {
        "status": status,
        "grade": round(fmean(grades), 3) if grades else None,
        "n_processes": len(processes),
        "improve": _aggregate_improvements(processes),
        "processes": processes,
        "wiring": {
            "status": "fail" if wire_error else ("warn" if wire_warn else "pass"),
            "findings": wire_findings,
        },
    }


def _aggregate_improvements(processes: list[dict]) -> list[dict]:
    """Roll each process's completeness ``missing`` items up to the composite, so
    the panel can lead with a few concrete, deduplicated "to improve" actions
    (with how many processes each applies to) instead of a repeated 0.75."""
    counts: dict[str, int] = {}
    for p in processes:
        for item in ((p.get("completeness") or {}).get("missing") or []):
            # Collapse port-specific text to one bucket so counts stay legible.
            key = "declare predicate conditions (bounds or invariants)" \
                if item.startswith("declare predicate") else item
            counts[key] = counts.get(key, 0) + 1
    out = [{"action": k, "n_processes": v} for k, v in counts.items()]
    out.sort(key=lambda d: (-d["n_processes"], d["action"]))
    return out
