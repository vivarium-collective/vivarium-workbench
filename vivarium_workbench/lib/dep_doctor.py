"""Framework-dependency doctor.

Turns the *silent* / *opaque* staleness failures that cost real debugging time
into actionable messages:

- a stale ``process-bigraph`` (predating the ``artifacts`` module) makes the
  server subprocess crash on boot and the whole test suite fail to collect with
  a bare ``ModuleNotFoundError: process_bigraph.artifacts`` — miles from the
  cause;
- a stale ``viva-superpowers`` (predating ``test_audit`` / ``loop_state``) makes
  the study-detail **Audit** and **Build** tabs render a blank "unavailable"
  with no hint that the fix is a dependency refresh.

`check_framework_deps()` probes each and returns structured findings; the CLI
``vivarium-workbench doctor`` prints them, and ``serve`` logs a one-line warning
per problem at startup. Pure + import-safe: every probe is wrapped, so importing
or running the doctor never raises.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import re
from typing import Any

# (import target, why it matters, how to fix) — the framework deps whose
# staleness produces a confusing downstream symptom.
_PROBES: list[tuple[str, str, str]] = [
    ("process_bigraph.artifacts",
     "the run/artifact plumbing the server imports at boot",
     "process-bigraph is stale (predates the `artifacts` module) — reinstall it at main "
     "(uv lock --upgrade-package process-bigraph && uv sync)"),
    ("viva_superpowers.test_audit",
     "the test-sufficiency audit the Assurance > Audit tab renders",
     "viva-superpowers is stale — the Audit tab will show 'unavailable'; reinstall it at main "
     "(uv lock --upgrade-package viva-superpowers && uv sync)"),
    ("viva_superpowers.loop_state",
     "the model-build loop provenance the Assurance > Build tab renders",
     "viva-superpowers is stale — the Build tab will show 'unavailable'; reinstall it at main "
     "(uv lock --upgrade-package viva-superpowers && uv sync)"),
]


# (dist, min version, why it matters, how to fix) — framework deps whose *stale
# but importable* version silently breaks a composite at runtime. A stale import
# (above) is a hard crash; these are the subtler "it imports, but misbehaves"
# failures that the field hit repeatedly (RENCI). The floors track the fixes that
# shipped: process-bigraph 1.8.5 = core_extensions + the #217 emitter fix +
# requests/fire; viva-emitters 0.4.2 = graceful drop of a removed-cell emit port.
_VERSION_FLOORS: list[tuple[str, str, str, str]] = [
    ("process-bigraph", "1.8.5",
     "the composite core_extensions mechanism, the #217 emitter fix, and the "
     "requests/fire server deps",
     "process-bigraph is below the 1.8.5 floor — upgrade it "
     "(uv lock --upgrade-package process-bigraph && uv sync)"),
    ("viva-emitters", "0.4.2",
     "the graceful drop of a removed-cell emit port — a composite that divides or "
     "removes cells otherwise KeyErrors mid-run",
     "viva-emitters is below the 0.4.2 floor — upgrade it "
     "(uv lock --upgrade-package viva-emitters && uv sync)"),
]


def _version_tuple(v: str) -> tuple[int, ...]:
    """Leading-numeric version tuple, tolerant of suffixes (``1.8.5.dev1`` → (1,8,5))."""
    parts: list[int] = []
    for comp in str(v).split("."):
        m = re.match(r"\d+", comp)
        if not m:
            break
        parts.append(int(m.group()))
    return tuple(parts)


def _check_version_floors() -> list[dict[str, Any]]:
    """Findings for installed-but-too-old framework deps. A dep that isn't
    installed is not this probe's concern (skipped) — only an installed version
    below its floor is a finding. Never raises."""
    out: list[dict[str, Any]] = []
    for dist, floor, why, fix in _VERSION_FLOORS:
        try:
            installed = importlib.metadata.version(dist)
        except Exception:  # noqa: BLE001 — not installed: nothing to floor-check
            continue
        target = f"{dist}>={floor}"
        if _version_tuple(installed) < _version_tuple(floor):
            out.append({"ok": False, "target": target, "why": why,
                        "detail": f"{installed} installed, below {floor}", "fix": fix})
        else:
            out.append({"ok": True, "target": target, "why": why,
                        "detail": f"{installed} ✓", "fix": ""})
    return out


def _check_loom_bundle() -> dict[str, Any]:
    """Finding for the vendored bigraph-loom bundle (``loom/_dist/index.html``).

    Missing means ``/bigraph-loom/*`` 404s and every composite-card graph view
    renders blank (the symptom that cost real debugging time: a long-lived server
    launched from an editable install whose source dir was later moved/deleted).
    Never raises."""
    why = "the bigraph-loom viewer bundle every composite graph view embeds"
    fix = ("the loom bundle is missing — composite graph views will not render. "
           "Reinstall vivarium-workbench and restart the server from a live checkout "
           "(a built wheel always ships it; an editable install needs loom/_dist built).")
    try:
        from vivarium_workbench.lib import static_serving as _ss
        from vivarium_workbench.loom_assets import asset_dir
        if _ss.loom_bundle_present():
            return {"ok": True, "target": "bigraph-loom bundle", "why": why,
                    "detail": f"present at {asset_dir()}", "fix": ""}
        return {"ok": False, "target": "bigraph-loom bundle", "why": why,
                "detail": f"missing at {asset_dir()}", "fix": fix}
    except Exception as e:  # noqa: BLE001 — a probe failure is a finding, not a crash
        return {"ok": False, "target": "bigraph-loom bundle", "why": why,
                "detail": f"{type(e).__name__}: {e}", "fix": fix}


def check_framework_deps() -> list[dict[str, Any]]:
    """Probe the framework deps. Returns one finding dict per probe:
    ``{ok: bool, target: str, why: str, detail: str, fix: str}``. Never raises."""
    out: list[dict[str, Any]] = []
    for target, why, fix in _PROBES:
        try:
            importlib.import_module(target)
            out.append({"ok": True, "target": target, "why": why, "detail": "importable", "fix": ""})
        except Exception as e:  # noqa: BLE001 — any import failure is a finding, not a crash
            out.append({"ok": False, "target": target, "why": why,
                        "detail": f"{type(e).__name__}: {e}", "fix": fix})
    out.extend(_check_version_floors())
    out.append(_check_loom_bundle())
    return out


def problems(findings: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The non-ok findings (empty = healthy)."""
    findings = check_framework_deps() if findings is None else findings
    return [f for f in findings if not f.get("ok")]


def format_report(findings: list[dict[str, Any]] | None = None) -> str:
    """Human-readable multi-line report."""
    findings = check_framework_deps() if findings is None else findings
    lines = ["Framework dependency doctor:"]
    for f in findings:
        mark = "✓" if f.get("ok") else "✗"
        lines.append(f"  {mark} {f['target']} — {f['detail']}")
        if not f.get("ok"):
            lines.append(f"      ↳ needed for {f['why']}")
            lines.append(f"      ↳ fix: {f['fix']}")
    probs = [f for f in findings if not f.get("ok")]
    lines.append("" if probs else "All framework dependencies are current. ✓")
    if probs:
        lines.append(f"{len(probs)} dependency finding(s) — see fixes above.")
    return "\n".join(lines)


def warn_lines(findings: list[dict[str, Any]] | None = None) -> list[str]:
    """One compact warning line per problem, for startup logging (no-op if healthy)."""
    return [f"dependency problem: {f['target']} — {f['detail']}. {f['fix']}"
            for f in problems(findings)]
