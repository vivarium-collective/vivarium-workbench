#!/usr/bin/env python3
"""Pre-demo verification — checks that all dashboard demo prerequisites are met.

Every check is READ-ONLY. This script never modifies any file in the workspace.
It exits 0 if all checks pass, 1 otherwise.

Usage:
    cd ~/vivarium-app/vivarium-dashboard
    python demos/v2ecoli/verify_demo.py
"""

from __future__ import annotations

import os as _os
import subprocess
import sys
from pathlib import Path

import v2ecoli as _v2ecoli

WORKSPACE_ROOT = Path(_os.environ.get(
    "V2ECOLI_ROOT", str(Path(_v2ecoli.__file__).resolve().parent.parent)))
OK = 0
FAIL = 0


def header(msg: str) -> None:
    print(f"\n━━━ {msg} ━━━")


def ok(msg: str) -> None:
    global OK
    OK += 1
    print(f"  ✅ {msg}")


def warn(msg: str, detail: str = "") -> None:
    global OK
    OK += 1
    suffix = f"  ({detail})" if detail else ""
    print(f"  ⚠️  {msg}{suffix}")


def fail(msg: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    suffix = f"  ({detail})" if detail else ""
    print(f"  ❌ {msg}{suffix}")


# ── package imports ──────────────────────────────────────────────────────

def _try_import(name: str) -> None:
    __import__(name)


PACKAGES: list[tuple[str, str]] = [
    ("v2ecoli", ""),
    ("viva_munk", "colony physics"),
    ("pbg_ketchup", "kinetic estimators"),
    ("pbg_copasi", "COPASI ODE"),
    ("pbg_bioreactordesign", "BiRD reactor"),
    ("pbg_torch", "neural surrogate"),
    ("pbg_parsimony", "capsule geometry"),
    ("pbg_emitters", "emitters"),
    ("pbg_superpowers", "superpowers runtime"),
]

OPTIONAL_PACKAGES: list[tuple[str, str]] = [
    ("pbg_oxidizeme", "ME-model wrapper (non-functional)"),
]


def check_imports() -> None:
    header("Package imports")
    for pkg, desc in PACKAGES:
        try:
            _try_import(pkg)
            ok(f"{pkg}{' (' + desc + ')' if desc else ''}")
        except ImportError as e:
            fail(f"{pkg} — not installed", str(e))
    for pkg, desc in OPTIONAL_PACKAGES:
        try:
            _try_import(pkg)
            warn(f"{pkg} (optional) {'(' + desc + ')' if desc else ''}", "non-functional")
        except ImportError:
            warn(f"{pkg} (optional) not installed — OK to skip", "non-functional")


# ── composite resolution ─────────────────────────────────────────────────

SELECTED_COMPOSITES: list[str] = [
    "v2ecoli.composites.parca",
    "v2ecoli.composites.baseline",
    "v2ecoli.composites.baseline_millard.baseline_millard",
    "v2ecoli.composites.millard_pdmp_baseline.millard_pdmp_baseline",
    "v2ecoli.composites.reactor_bird_coupled.reactor_bird_coupled",
    "v2ecoli.composites.reactor_bird_coupled_millard.reactor_bird_coupled_millard",
    "v2ecoli.composites.colony.colony",
    "v2ecoli.composites.baseline_population.baseline_population",
]

EXTERNAL_COMPOSITES: list[str] = [
    "pbg_ketchup.composites.estimation.ketchup_baseline",
    "viva_munk.composites.chemotaxis",
    "viva_munk.composites.biofilm",
]


def _resolve_composite(cid: str) -> bool:
    """Resolve a composite by its spec id via the generator registry."""
    from pbg_superpowers.composite_generator import _REGISTRY, build_generator, discover_generators

    if not _REGISTRY:
        discover_generators()

    if cid not in _REGISTRY:
        return False, "not in registry"

    try:
        entry = _REGISTRY[cid]
        doc = build_generator(entry, overrides={})
        name = doc.get("name", cid) if isinstance(doc, dict) else cid
        return True, name
    except Exception as e:
        return False, str(e)[:120]


def check_composites() -> None:
    header("Composite resolution (read-only)")
    # Ensure v2ecoli composites module is imported so generators register
    import v2ecoli.composites  # noqa: F401
    import pbg_ketchup.composites  # noqa: F401
    # viva_munk composites are registered via its top-level __init__.py

    for cid in SELECTED_COMPOSITES:
        success, detail = _resolve_composite(cid)
        if success:
            ok(f"{cid} → {detail}")
        else:
            fail(f"{cid} — failed to resolve", detail)

    for cid in EXTERNAL_COMPOSITES:
        success, detail = _resolve_composite(cid)
        if success:
            ok(f"{cid} (external) → {detail}")
        else:
            warn(f"{cid} (external) — failed to resolve", detail)


# ── study directories ─────────────────────────────────────────────────────

SHOWCASE_STUDIES: list[str] = [
    "showcase-1-parca",
    "showcase-2-baseline-figures",
    "showcase-3-variant-decide",
    "showcase-4-variant-comparison",
    "showcase-5-next-direction-decide",
    "showcase-6-equivalence-large",
]


def check_studies() -> None:
    header("Study directories")
    from vivarium_dashboard.lib.workspace_paths import WorkspacePaths

    paths = WorkspacePaths.load(WORKSPACE_ROOT)
    studies_root = paths.studies

    for name in SHOWCASE_STUDIES:
        study_dir = studies_root / name
        study_yaml = study_dir / "study.yaml"
        if study_yaml.exists():
            ok(f"{name}/study.yaml")
        else:
            fail(f"{name}/study.yaml — not found", str(study_dir))


# ── ParCa cache ───────────────────────────────────────────────────────────

PARCAS_CACHE_FILES: list[str] = [
    "models/parca/parca_state.pkl.gz",
    "models/parca/runtimes.json",
    "models/parca.pbg",
]


def check_parca_cache() -> None:
    header("ParCa model fixtures")
    for rel in PARCAS_CACHE_FILES:
        path = WORKSPACE_ROOT / rel
        if path.exists():
            size_mb = path.stat().st_size / (1024 * 1024)
            ok(f"{rel} ({size_mb:.0f} MB)")
        else:
            fail(f"{rel} — not found", "run: v2ecoli-parca --mode fast --cpus 4")


# ── git state ─────────────────────────────────────────────────────────────

def check_git() -> None:
    header("Git repository")

    r = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=WORKSPACE_ROOT, capture_output=True, text=True, timeout=5,
    )
    if r.returncode == 0 and r.stdout.strip() == "true":
        ok("inside a git work tree")
    else:
        fail("not a git work tree")
        return

    r = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=WORKSPACE_ROOT, capture_output=True, text=True, timeout=5,
    )
    if r.returncode == 0:
        ok(f"origin remote: {r.stdout.strip()[:60]}")
    else:
        fail("no origin remote — remote demo (Segment 6) needs this")

    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=WORKSPACE_ROOT, capture_output=True, text=True, timeout=10,
    )
    dirty = [line for line in r.stdout.splitlines()
             if len(line) >= 4 and not line[3:].startswith((".pbg/", "out/", "reports/", "demos/"))]
    if not dirty:
        ok("workspace is clean (excluding generated dirs)")
    else:
        warn(f"{len(dirty)} dirty files outside .pbg/out/reports/demos",
             dirty[0][3:] if dirty else "")


# ── sms-api tunnel (optional) ────────────────────────────────────────────

def check_sms_api() -> None:
    header("sms-api tunnel (optional — for remote demo segments)")
    import urllib.request

    try:
        req = urllib.request.Request(
            "http://localhost:8080/core/v1/simulator/versions",
            method="GET", headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310
            if r.status == 200:
                ok("sms-api reachable at http://localhost:8080")
            else:
                warn(f"sms-api responded with {r.status}", "tunnel may be misconfigured")
    except Exception as e:
        warn("sms-api unreachable", "start SSM tunnel for Segment 6 (remote demo)")


# ── dashboard CLI ────────────────────────────────────────────────────────

def check_cli() -> None:
    header("Dashboard CLI")
    import shutil

    for cli in ("vivarium-dashboard", "vivarium-workbench", "vwb"):
        path = shutil.which(cli)
        if path:
            ok(f"`{cli}` found at {path}")
            break
    else:
        fail("no dashboard CLI found on PATH", "install vivarium-dashboard in this venv")


# ── demo runs DB (Simulations DB tab) ─────────────────────────────────────

def check_demo_runs() -> None:
    header("Simulations DB demo data")
    import sqlite3

    db_path = WORKSPACE_ROOT / ".pbg" / "composite-runs.db"
    if not db_path.exists():
        warn("demo runs DB not found", "run: python demos/v2ecoli/populate_demo_runs.py")
        return

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT COUNT(*) as n FROM runs_meta").fetchone()
        count = rows["n"] if rows else 0
        conn.close()
        if count > 0:
            ok(f"{count} demo runs in .pbg/composite-runs.db")
        else:
            warn("demo runs DB is empty", "run: python demos/v2ecoli/populate_demo_runs.py")
    except Exception as e:
        warn(f"could not read demo runs DB", str(e))


# ── cell-side interface contract ──────────────────────────────────────────

def check_cell_contract() -> None:
    header("Cell-side interface contract (swappability reference)")
    path = WORKSPACE_ROOT / "workspace" / "references" / "expert" / "cell_side_interface_contract.md"
    if path.exists():
        ok(f"cell_side_interface_contract.md ({path.stat().st_size} bytes)")
    else:
        fail("cell_side_interface_contract.md not found", str(path))


# ── PTools (optional) ─────────────────────────────────────────────────────

def check_ptools() -> None:
    header("PTools omics viewer (optional — for Analyses tab)")
    from urllib.request import Request, urlopen
    from urllib.error import URLError

    try:
        req = Request("http://localhost:1555", method="GET")
        with urlopen(req, timeout=3) as r:  # noqa: S310
            ok("sms-ptools reachable at http://localhost:1555")
    except (URLError, OSError):
        warn("sms-ptools not running", "start container for omics viewer (Segment 7)")


# ── main ──────────────────────────────────────────────────────────────────

def main() -> int:
    print("vivarium-workbench Dashboard Demo — Verification")
    print(f"Workspace: {WORKSPACE_ROOT}")

    check_imports()
    check_parca_cache()
    check_git()
    check_composites()
    check_studies()
    check_cell_contract()
    check_cli()
    check_demo_runs()
    check_sms_api()
    check_ptools()

    print(f"\n{'─' * 60}")
    print(f"  Passed: {OK}  |  Failed: {FAIL}")
    if FAIL:
        print("  ⚠️  Fix failures above before running the demo.")
    else:
        print("  ✅ All checks passed — ready for demo.")

    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
